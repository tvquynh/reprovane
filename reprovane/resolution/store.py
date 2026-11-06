"""Immutable decision ledger and deterministic run-scoped publication projections."""
import json
from contextlib import contextmanager
from ..core import ValidationError
from ..core.db import Database
from ..core.ids import new_id, utc_now
from ..core.migrations import apply_migrations
from ..core.provenance import canonical_json
from ..observations.validation import text
from . import ResolutionStateError, ResolutionConflictError, DecisionSupersessionError
from .policy import (ResolverPolicy, ObservationSelector, digest, object_value, POLICY_NAME, POLICY_VERSION,
                     RESOLVER_NAME, RESOLVER_VERSION)
from .evidence import extract_evidence
from .resolver import generate, automatic, components, entity_identity

RELATION_TYPES = frozenset({"PREPRINT_OF", "ACCEPTED_MANUSCRIPT_OF", "REPOSITORY_COPY_OF", "CONFERENCE_PRECURSOR_OF",
                           "EXTENDED_VERSION_OF", "CORRECTION_OF", "RETRACTION_OF", "POSSIBLY_RELATED_VERSION"})


class ResolutionStore:
    def __init__(self, database: Database):
        self.db = database
        self._authorization = None
        self._savepoint_number = 0
        database.connection.create_function("resolution_authorized", 2,
            lambda rid, phase: int(self._authorization == (rid, phase)))
        database.connection.create_function("resolution_canonical_json", 1,
            lambda value: canonical_json(json.loads(value)), deterministic=True)
        database.connection.create_function("resolution_sorted_array", 1,
            lambda value: canonical_json(sorted(set(json.loads(value)))), deterministic=True)
        apply_migrations(database.connection, target_version=4)

    @contextmanager
    def _transaction(self):
        if not self.db.connection.in_transaction:
            with self.db.transaction():
                yield
            return
        # Each high-level operation remains atomic even if a caller catches its
        # exception and later commits the enclosing foundation transaction.
        self._savepoint_number += 1
        name = f"resolution_operation_{self._savepoint_number}"
        self.db.execute("SAVEPOINT " + name)
        try:
            yield
            self.db.execute("RELEASE " + name)
        except BaseException:
            self.db.connection.execute("ROLLBACK TO " + name)
            self.db.connection.execute("RELEASE " + name)
            raise

    @contextmanager
    def _authorize(self, run_id, phase):
        if self._authorization is not None:
            raise ResolutionStateError("Nested lifecycle authorization is unsupported")
        self._authorization = (run_id, phase)
        try:
            yield
        finally:
            self._authorization = None

    def _insert(self, table: str, row: dict) -> dict:
        self.db.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
        return row.copy()

    def get_resolution_run(self, run_id: str) -> dict:
        row = self.db.execute("SELECT * FROM resolution_run WHERE resolution_run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ResolutionStateError("Resolution run does not exist")
        return dict(row)

    def _open(self, run_id: str) -> dict:
        row = self.get_resolution_run(run_id)
        if row["materialized_at_utc"] is not None:
            raise ResolutionStateError("Resolution run is materialized or sealed")
        return row

    def create_resolution_run(self, project_revision_id: str, observation_selector: str | dict, *,
                              policy: str | dict | ResolverPolicy | None = None) -> dict:
        text(project_revision_id)
        policy = policy if isinstance(policy, ResolverPolicy) else ResolverPolicy.from_value(policy)
        selector = ObservationSelector.from_value(observation_selector)
        p, s = policy.as_dict(), selector.as_dict()
        row = dict(resolution_run_id=new_id("res"), project_revision_id=project_revision_id,
            resolver_name=RESOLVER_NAME, resolver_version=RESOLVER_VERSION, policy_name=POLICY_NAME, policy_version=POLICY_VERSION,
            policy_json=canonical_json(p), policy_sha256=digest(p), observation_selector_json=canonical_json(s),
            observation_selector_sha256=digest(s), created_at_utc=utc_now(), candidates_generated_at_utc=None,
            candidate_manifest_sha256=None, materialized_at_utc=None, sealed_at_utc=None, input_manifest_sha256=None,
            materialization_manifest_sha256=None, decision_manifest_json=None, relation_manifest_sha256=None)
        with self._transaction():
            return self._insert("resolution_run", row)

    def add_resolution_input(self, run_id: str, provider_record_id: str) -> dict:
        return self.add_resolution_inputs(run_id, [provider_record_id])[0]

    def add_resolution_inputs(self, run_id: str, provider_record_ids: list[str]) -> list[dict]:
        for record_id in provider_record_ids:
            text(record_id)
        if len(set(provider_record_ids)) != len(provider_record_ids):
            raise ValidationError("Duplicate resolution input")
        with self._transaction():
            run = self._open(run_id)
            if run["candidates_generated_at_utc"] is not None:
                raise ResolutionStateError("Inputs are frozen after candidate generation")
            existing = {r["provider_record_id"] for r in self.list_resolution_inputs(run_id)}
            if existing.intersection(provider_record_ids):
                raise ValidationError("Duplicate resolution input")
            selector = ObservationSelector.from_value(run["observation_selector_json"])
            evidence = [extract_evidence(self.db, rid, selector) for rid in sorted(provider_record_ids)]
            result = []
            with self._authorize(run_id, "INPUT"):
                for proof in evidence:
                    result.append(self._insert("resolution_run_input", dict(resolution_run_id=run_id,
                        provider_record_id=proof["record_id"], evidence_json=canonical_json(proof), added_at_utc=utc_now())))
            return result

    def list_resolution_inputs(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM resolution_run_input WHERE resolution_run_id=? ORDER BY provider_record_id", (run_id,))]

    def list_candidates(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM resolution_candidate WHERE resolution_run_id=? ORDER BY left_provider_record_id,right_provider_record_id", (run_id,))]

    @staticmethod
    def _candidate_content(row):
        return {k: v for k, v in row.items() if k != "created_at_utc"}

    def generate_candidates(self, run_id: str) -> list[dict]:
        with self._transaction():
            run = self._open(run_id)
            if run["candidates_generated_at_utc"] is not None:
                self._verify_candidates(run)
                return self.list_candidates(run_id)
            policy = ResolverPolicy.from_value(run["policy_json"])
            inputs = self.list_resolution_inputs(run_id)
            rows = generate([json.loads(r["evidence_json"]) for r in inputs], policy.max_candidate_block_size)
            with self._authorize(run_id, "GENERATE"):
                for row in rows:
                    proof = dict(candidate_id="cand_"+digest([run_id, row["left_provider_record_id"], row["right_provider_record_id"]])[:40],
                        resolution_run_id=run_id, left_provider_record_id=row["left_provider_record_id"],
                        right_provider_record_id=row["right_provider_record_id"], candidate_methods_json=canonical_json(row["candidate_methods"]),
                        evidence_json=canonical_json(row["evidence"]), hard_conflicts_json=canonical_json(row["hard_conflicts"]), created_at_utc=utc_now())
                    self._insert("resolution_candidate", proof)
                manifest = digest([self._candidate_content(r) for r in self.list_candidates(run_id)])
                self.db.execute("UPDATE resolution_run SET candidates_generated_at_utc=?,candidate_manifest_sha256=? WHERE resolution_run_id=?",
                                (utc_now(), manifest, run_id))
            return self.list_candidates(run_id)

    def _verify_candidates(self, run):
        if run["candidates_generated_at_utc"] is None or digest([self._candidate_content(r) for r in self.list_candidates(run["resolution_run_id"])]) != run["candidate_manifest_sha256"]:
            raise ResolutionConflictError("Candidate generation manifest does not verify")

    def get_active_decision(self, candidate_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM active_entity_resolution_decision WHERE candidate_id=?", (candidate_id,)).fetchone()
        return dict(row) if row else None

    def list_active_decisions(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM active_entity_resolution_decision WHERE resolution_run_id=? ORDER BY candidate_id", (run_id,))]

    def _candidate(self, candidate_id):
        row = self.db.execute("SELECT * FROM resolution_candidate WHERE candidate_id=?", (candidate_id,)).fetchone()
        if row is None:
            raise ValidationError("Candidate does not exist")
        return dict(row)

    def _decision(self, candidate, decision_type, mode, reason, actor=None, note=None, supersedes=None):
        body = dict(resolution_run_id=candidate["resolution_run_id"], candidate_id=candidate["candidate_id"],
            decision_type=decision_type, decision_mode=mode, reason_code=reason, evidence_json=candidate["evidence_json"],
            hard_conflicts_json=candidate["hard_conflicts_json"], actor_id=actor, note=note, supersedes_decision_id=supersedes)
        row = dict(decision_id="dec_"+digest(body)[:40], **body, created_at_utc=utc_now())
        try:
            return self._insert("entity_resolution_decision", row)
        except ValidationError:
            raise DecisionSupersessionError("Decision collision or invalid supersession") from None

    def evaluate_candidates_automatically(self, run_id: str) -> list[dict]:
        with self._transaction():
            run = self._open(run_id); self._verify_candidates(run)
            for candidate in self.list_candidates(run_id):
                if self.get_active_decision(candidate["candidate_id"]) is None:
                    outcome, reason = automatic(json.loads(candidate["evidence_json"]), json.loads(candidate["hard_conflicts_json"]))
                    self._decision(candidate, outcome, "AUTOMATIC", reason)
            return self.list_active_decisions(run_id)

    def record_manual_decision(self, candidate_id: str, decision_type: str, *, actor_id: str, reason_code: str,
                               note: str | None = None, supersedes_decision_id: str | None = None) -> dict:
        text(actor_id); text(reason_code)
        if note is not None: text(note)
        if decision_type not in {"SAME", "DISTINCT", "AMBIGUOUS"}:
            raise ValidationError("Invalid resolution decision type")
        with self._transaction():
            candidate = self._candidate(candidate_id); self._open(candidate["resolution_run_id"])
            active = self.get_active_decision(candidate_id)
            if active and supersedes_decision_id != active["decision_id"]:
                raise DecisionSupersessionError("Explicit current active decision is required")
            return self._decision(candidate, decision_type, "MANUAL", reason_code, actor_id, note, supersedes_decision_id)

    def list_review_queue(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM resolution_review_queue WHERE resolution_run_id=? ORDER BY left_provider_record_id,right_provider_record_id", (run_id,))]

    @staticmethod
    def _decision_content(row):
        return {k: v for k, v in row.items() if k != "created_at_utc"}

    def _validated_decisions(self, run_id: str, frozen: str | None = None) -> list[dict]:
        candidates = self.list_candidates(run_id)
        if frozen is None:
            decisions = self.list_active_decisions(run_id)
        else:
            decisions = []
            for descriptor in json.loads(frozen):
                row = self.db.execute("SELECT * FROM entity_resolution_decision WHERE decision_id=?", (descriptor["decision_id"],)).fetchone()
                if row is None or self._decision_content(dict(row)) != descriptor:
                    raise ResolutionConflictError("Frozen decision content does not verify")
                decisions.append(dict(row))
        if len(decisions) != len(candidates) or {d["candidate_id"] for d in decisions} != {c["candidate_id"] for c in candidates}:
            raise ResolutionConflictError("Every candidate requires exactly one active decision")
        by_id = {c["candidate_id"]: c for c in candidates}
        for d in decisions:
            c = by_id[d["candidate_id"]]
            if d["resolution_run_id"] != run_id or d["evidence_json"] != c["evidence_json"] or d["hard_conflicts_json"] != c["hard_conflicts_json"]:
                raise ResolutionConflictError("Decision evidence disagrees with candidate")
            if d["decision_mode"] == "AUTOMATIC" and (d["decision_type"],d["reason_code"]) != automatic(json.loads(c["evidence_json"]),json.loads(c["hard_conflicts_json"])):
                raise ResolutionConflictError("Automatic decision violates conservative policy")
        return sorted(decisions, key=lambda r: r["candidate_id"])

    def _projection(self, run, decisions):
        inputs = self.list_resolution_inputs(run["resolution_run_id"])
        records = [r["provider_record_id"] for r in inputs]
        if not records:
            raise ResolutionStateError("Resolution materialization requires at least one input")
        groups = components(records, self.list_candidates(run["resolution_run_id"]), decisions)
        input_hash = digest(dict(provider_record_ids=records, policy_sha256=run["policy_sha256"],
            observation_selector_sha256=run["observation_selector_sha256"], resolver_name=run["resolver_name"], resolver_version=run["resolver_version"],
            input_evidence_sha256=digest([dict(provider_record_id=r["provider_record_id"], evidence=json.loads(r["evidence_json"])) for r in inputs])))
        decision_content = [self._decision_content(d) for d in decisions]
        material_hash = digest(dict(input_manifest_sha256=input_hash, active_decisions=decision_content,
            entity_memberships=groups, policy_sha256=run["policy_sha256"], observation_selector_sha256=run["observation_selector_sha256"],
            resolver_version=run["resolver_version"]))
        return groups, input_hash, material_hash, decision_content

    def materialize_resolution_run(self, run_id: str) -> list[dict]:
        with self._transaction():
            run = self._open(run_id); self._verify_candidates(run)
            decisions = self._validated_decisions(run_id)
            groups, input_hash, material_hash, frozen = self._projection(run, decisions)
            bases = self._membership_bases(groups, self.list_candidates(run_id), decisions)
            with self._authorize(run_id, "MATERIALIZE"):
                for members in groups:
                    entity_id, member_hash = entity_identity(members)
                    self._insert("publication_entity", dict(resolution_run_id=run_id, entity_id=entity_id,
                        membership_sha256=member_hash, member_count=len(members), created_at_utc=utc_now()))
                    for record in members:
                        self._insert("entity_membership", dict(resolution_run_id=run_id, entity_id=entity_id,
                            provider_record_id=record, membership_basis_json=canonical_json(bases[record]), created_at_utc=utc_now()))
                self.db.execute("UPDATE resolution_run SET materialized_at_utc=?,input_manifest_sha256=?,materialization_manifest_sha256=?,decision_manifest_json=? WHERE resolution_run_id=?",
                    (utc_now(), input_hash, material_hash, canonical_json(frozen), run_id))
            return self.list_publication_entities(run_id)

    @staticmethod
    def _membership_bases(groups, candidates, decisions):
        bases = {}
        for members in groups:
            membership_hash = entity_identity(members)[1]
            for record in members:
                bases[record] = dict(component_membership_sha256=membership_hash, same_decision_ids=[])
        by_candidate = {c["candidate_id"]: c for c in candidates}
        for decision in decisions:
            if decision["decision_type"] == "SAME":
                candidate = by_candidate[decision["candidate_id"]]
                for field in ("left_provider_record_id", "right_provider_record_id"):
                    bases[candidate[field]]["same_decision_ids"].append(decision["decision_id"])
        for basis in bases.values():
            basis["same_decision_ids"].sort()
        return bases

    def list_publication_entities(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM publication_entity WHERE resolution_run_id=? ORDER BY entity_id", (run_id,))]

    def list_entity_members(self, run_id: str, entity_id: str | None = None) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM entity_membership WHERE resolution_run_id=? AND (? IS NULL OR entity_id=?) ORDER BY entity_id,provider_record_id", (run_id,entity_id,entity_id))]

    def add_publication_relation(self, run_id: str, source_entity_id: str, target_entity_id: str, relation_type: str, *,
            decision_mode: str = "MANUAL", evidence_json: str | dict | None = None, reason_code: str,
            actor_id: str | None = None, note: str | None = None, supersedes_relation_id: str | None = None) -> dict:
        text(reason_code)
        text(source_entity_id); text(target_entity_id)
        if note is not None: text(note)
        if actor_id is not None: text(actor_id)
        if decision_mode not in {"MANUAL", "IMPORTED"} or relation_type not in RELATION_TYPES:
            raise ValidationError("Invalid publication relation type or mode")
        if decision_mode == "MANUAL": text(actor_id)
        if source_entity_id == target_entity_id:
            raise ResolutionConflictError("Publication relations require distinct entities")
        proof = object_value({} if evidence_json is None else evidence_json)
        with self._transaction():
            run = self.get_resolution_run(run_id)
            if run["materialized_at_utc"] is None or run["sealed_at_utc"] is not None:
                raise ResolutionStateError("Relations require an unsealed materialized run")
            body = dict(resolution_run_id=run_id, source_entity_id=source_entity_id, target_entity_id=target_entity_id,
                relation_type=relation_type, decision_mode=decision_mode, evidence_json=canonical_json(proof), reason_code=reason_code,
                actor_id=actor_id, note=note, supersedes_relation_id=supersedes_relation_id)
            return self._insert("publication_relation", dict(relation_id="rel_"+digest(body)[:40], **body, created_at_utc=utc_now()))

    def get_active_relation(self, relation_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM active_publication_relation WHERE relation_id=?", (relation_id,)).fetchone()
        return dict(row) if row else None

    def list_active_relations(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM active_publication_relation WHERE resolution_run_id=? ORDER BY relation_id", (run_id,))]

    def _relations_hash(self, run_id):
        rows = [dict(r) for r in self.db.execute("SELECT * FROM publication_relation WHERE resolution_run_id=? ORDER BY relation_id", (run_id,))]
        entities = {r["entity_id"] for r in self.list_publication_entities(run_id)}
        for row in rows:
            if row["source_entity_id"] not in entities or row["target_entity_id"] not in entities or row["source_entity_id"] == row["target_entity_id"] or row["relation_type"] not in RELATION_TYPES:
                raise ResolutionConflictError("Invalid publication relation structure")
        return digest([{k:v for k,v in row.items() if k != "created_at_utc"} for row in rows])

    def verify_resolution_run(self, run_id: str) -> bool:
        with self._transaction():
            run = self.get_resolution_run(run_id)
            if run["materialized_at_utc"] is None:
                raise ResolutionStateError("Run must be materialized before verification")
            if digest(json.loads(run["policy_json"])) != run["policy_sha256"] or digest(json.loads(run["observation_selector_json"])) != run["observation_selector_sha256"]:
                raise ResolutionConflictError("Resolution policy or selector hash mismatch")
            self._verify_candidates(run)
            decisions = self._validated_decisions(run_id, run["decision_manifest_json"])
            groups, input_hash, material_hash, frozen = self._projection(run, decisions)
            if (input_hash,material_hash) != (run["input_manifest_sha256"],run["materialization_manifest_sha256"]):
                raise ResolutionConflictError("Resolution materialization manifest mismatch")
            expected = {entity_identity(members)[0]: members for members in groups}
            bases = self._membership_bases(groups, self.list_candidates(run_id), decisions)
            actual = {r["entity_id"]: [] for r in self.list_publication_entities(run_id)}
            for row in self.list_entity_members(run_id):
                if row["entity_id"] not in actual:
                    raise ResolutionConflictError("Membership references unknown entity")
                actual[row["entity_id"]].append(row["provider_record_id"])
                if json.loads(row["membership_basis_json"]) != bases.get(row["provider_record_id"]):
                    raise ResolutionConflictError("Membership decision basis does not verify")
            if {k:sorted(v) for k,v in actual.items()} != expected:
                raise ResolutionConflictError("Entity membership does not match SAME projection")
            for entity in self.list_publication_entities(run_id):
                members = actual[entity["entity_id"]]
                if entity_identity(members) != (entity["entity_id"],entity["membership_sha256"]) or len(members) != entity["member_count"]:
                    raise ResolutionConflictError("Entity identity or count mismatch")
            relation_hash = self._relations_hash(run_id)
            if run["sealed_at_utc"] is not None and relation_hash != run["relation_manifest_sha256"]:
                raise ResolutionConflictError("Sealed relation manifest mismatch")
            return True

    def seal_resolution_run(self, run_id: str) -> dict:
        with self._transaction():
            run = self.get_resolution_run(run_id)
            if run["sealed_at_utc"] is not None:
                raise ResolutionStateError("Resolution run is already sealed")
            self.verify_resolution_run(run_id)
            with self._authorize(run_id, "SEAL"):
                self.db.execute("UPDATE resolution_run SET sealed_at_utc=?,relation_manifest_sha256=? WHERE resolution_run_id=?",
                                (utc_now(),self._relations_hash(run_id),run_id))
            return self.get_resolution_run(run_id)
