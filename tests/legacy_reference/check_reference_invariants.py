"""Independent invariant checker for an enriched corpus produced by `enrich`/`run`.

Usage: python -I check_reference_invariants.py <scimago_cache> <raw.csv> <output_prefix> <mode>
mode: default | all_source_types

Recomputes publication-year/latest SCImago metrics from the raw SCImago CSVs (csv module only,
not the project's parser) and compares them with the enriched output. Prints one PASS/FAIL line
per check; exit code 1 if any check fails.
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

cache, raw_path, prefix, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
manifest = json.load(open(cache / "manifest.json", encoding="utf-8"))
first = int(manifest["sjr"]["first_year"])
last = int(manifest["sjr"]["last_year"])
latest_y = int(manifest["sjr"]["latest_snapshot_year"])


def rcsv(p):
    with open(p, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def rjsonl(p):
    with open(p, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def num(s):
    s = (s or "").strip().replace(",", ".")
    return float(s) if s else None


def q(s):
    s = (s or "").strip().upper()
    return s if s in {"Q1", "Q2", "Q3", "Q4"} else ""


sc = {}
for y in range(first, last + 1):
    with open(cache / f"scimagojr-{y}.csv", encoding="utf-8-sig", newline="") as fh:
        sc[y] = {r["Sourceid"].strip(): r for r in csv.DictReader(fh, delimiter=";")}

raw = rcsv(raw_path)
enr = rcsv(prefix + "_enriched.csv")
enrj = rjsonl(prefix + "_enriched.jsonl")
main = rcsv(prefix + "_main.csv")
exc = rcsv(prefix + "_excluded.csv")
audit = rcsv(prefix + "_source_audit.csv")
summ = json.load(open(prefix + "_summary.json", encoding="utf-8"))
fails = 0


def check(name, ok, detail=""):
    global fails
    if not ok:
        fails += 1
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))


check("D1 record count preserved raw->enriched", len(raw) == len(enr) == len(enrj),
      f"raw={len(raw)} csv={len(enr)} jsonl={len(enrj)}")
ids = [r["openalex_id"] for r in enr]
check("D2 no duplicate OpenAlex IDs in enriched", len(ids) == len(set(ids)), f"unique={len(set(ids))}")
dois = [r["doi"] for r in enr if r["doi"]]
check("D2b no duplicate non-empty DOIs in enriched", len(dois) == len(set(dois)), f"dois={len(dois)}")

rawmap = {r["openalex_id"]: r for r in raw}
missing_cols = [c for c in raw[0] if c not in enr[0]]
diff_vals = [(r["openalex_id"], c) for r in enr for c in raw[0] if c in r and r[c] != rawmap[r["openalex_id"]][c]]
check("D3 raw columns preserved with identical values", not missing_cols and not diff_vals,
      f"missing={missing_cols} changed={diff_vals[:3]}")

m_ids = {r["openalex_id"] for r in main}
e_ids = {r["openalex_id"] for r in exc}
check("D4 main + excluded = enriched", (m_ids | e_ids) == set(ids) and len(main) + len(exc) == len(enr),
      f"main={len(main)} excluded={len(exc)}")
check("D5 main and excluded disjoint", not (m_ids & e_ids))
check("D5b main_corpus flag agrees with partition",
      all((r["main_corpus"] == "True") == (r["openalex_id"] in m_ids) for r in enr))
check("D5c every excluded row has an exclusion reason", all(r["main_exclusion_reasons"].strip() for r in exc))


def norm(v):
    return "" if v is None else str(v)


jmap = {r["openalex_id"]: r for r in enrj}
mism = [(r["openalex_id"], k) for r in enr for k, v in r.items() if norm(jmap.get(r["openalex_id"], {}).get(k)) != v]
check("D6 enriched CSV and JSONL hold the same records/values", set(jmap) == set(ids) and not mism,
      f"mismatches={mism[:3]}")

bad = []
for r in enr:
    st = r["source_status"]
    if st == "ACTIVE_LATEST":
        if r["scimago_sourceid"] not in sc[latest_y] or r["in_scimago_latest"] != "True":
            bad.append((r["openalex_id"], st))
    elif st == "HISTORICAL_ONLY":
        h = r.get("scimago_sourceid_historical", "")
        if (not h or h in sc[latest_y] or not any(h in sc[y] for y in range(first, latest_y))
                or r["in_scimago_latest"] != "False"):
            bad.append((r["openalex_id"], st))
    elif st in ("UNMATCHED", "AMBIGUOUS"):
        if r["scimago_sourceid"] or r["in_scimago_latest"] != "False":
            bad.append((r["openalex_id"], st))
    else:
        bad.append((r["openalex_id"], st))
check("D7 source_status internally consistent with SCImago presence", not bad, f"bad={bad[:5]}")

pm = []
for r in enr:
    y = int(r["publication_year"])
    sid = r.get("scimago_sourceid_at_publication") or (
        r["scimago_sourceid"] if r["source_status"] == "ACTIVE_LATEST" else r.get("scimago_sourceid_historical", ""))
    if r["ranking_basis"] == "EXACT_PUBLICATION_YEAR":
        src = sc.get(y, {}).get(sid)
        if (not src or r["sjr_year_at_publication"] != str(y)
                or num(r["sjr_at_publication"]) != num(src["SJR"])
                or q(r["best_quartile_at_publication"]) != q(src["SJR Best Quartile"])
                or r["categories_at_publication"] != src["Categories"].strip()):
            pm.append((r["openalex_id"], y, sid))
    elif r["sjr_year_at_publication"] or r["sjr_at_publication"] or q(r["best_quartile_at_publication"]):
        pm.append((r["openalex_id"], y, "metric-without-exact-basis"))
check("D8 publication-year SJR/quartile/categories equal the exact-year SCImago row", not pm, f"bad={pm[:5]}")

placeholder = [r["openalex_id"] for r in enr
               if r["best_quartile_at_publication"].strip() and not q(r["best_quartile_at_publication"])]
check("D8b publication-year quartile holds only Q1-Q4 or blank (no SCImago '-' placeholder)", not placeholder,
      f"n={len(placeholder)}")

lm = []
for r in enr:
    if r["source_status"] == "ACTIVE_LATEST":
        src = sc[latest_y][r["scimago_sourceid"]]
        if num(r["sjr_latest"]) != num(src["SJR"]) or q(r["best_quartile_latest"]) != q(src["SJR Best Quartile"]):
            lm.append(r["openalex_id"])
    elif r.get("sjr_latest") or q(r.get("best_quartile_latest")):
        lm.append(r["openalex_id"] + ":latest-metric-on-non-active")
check("D9 latest SJR/quartile equal the latest SCImago row; absent for non-active", not lm, f"bad={lm[:5]}")

fut = [r for r in enr if int(r["publication_year"]) > last]
fbad = []
for r in fut:
    if r["sjr_year_at_publication"] or r["sjr_at_publication"] or r["best_quartile_at_publication"]:
        fbad.append((r["openalex_id"], "pub-metric"))
    if r["source_status"] == "ACTIVE_LATEST" and r["ranking_basis"] != "LATEST_AVAILABLE":
        fbad.append((r["openalex_id"], "basis-active"))
check(f"E1 future-year papers (n={len(fut)}) have no publication-year SJR/quartile; active ones use LATEST_AVAILABLE",
      not fbad, f"bad={fbad[:5]}")
fut_nonactive_latest = [r["openalex_id"] for r in fut
                        if r["source_status"] != "ACTIVE_LATEST" and r["ranking_basis"] == "LATEST_AVAILABLE"]
check("E2 future-year non-active papers are not labelled LATEST_AVAILABLE (no latest metadata exists)",
      not fut_nonactive_latest, f"n={len(fut_nonactive_latest)}")

if mode == "default":
    nonj = [r["openalex_id"] for r in main if (r.get("scimago_type_latest") or r.get("source_type")).casefold() != "journal"]
    check("D10 default main corpus contains only journals", not nonj, f"non-journal={nonj}")
else:
    types = Counter((r.get("scimago_type_latest") or r.get("source_type")) for r in main)
    n_active = sum(1 for r in enr if r["source_status"] == "ACTIVE_LATEST")
    check("D11 --all-source-types main keeps every ACTIVE_LATEST row incl. non-journal",
          len(main) == n_active and any(t != "journal" for t in types), f"types={dict(types)}")
check("D12 every main row is ACTIVE_LATEST (latest-presence policy)",
      all(r["source_status"] == "ACTIVE_LATEST" for r in main))

paper_counts = Counter(r["source_status"] for r in enr)
check("G1 summary paper_source_status_counts equals recount",
      summ["paper_source_status_counts"] == dict(sorted(paper_counts.items())), str(summ["paper_source_status_counts"]))
check("G2 summary unique_source_status_counts sums to source_audit rows",
      sum(summ["unique_source_status_counts"].values()) == len(audit), f"audit_rows={len(audit)}")
check("G3 source_audit paper_count sums to enriched rows", sum(int(a["paper_count"]) for a in audit) == len(enr))
print(f"SUMMARY: fails={fails}")
sys.exit(1 if fails else 0)
