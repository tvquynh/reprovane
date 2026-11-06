"""Deterministic IDs with the accepted typed-table validation and immutable guards."""
import hashlib
from contextlib import nullcontext
from ..core import ValidationError,ArtifactIntegrityError
from ..core.provenance import canonical_json
from ..observations.store import ObservationStore,TABLES
from ..observations.validation import NormalizationProvenance,text


class AcquisitionObservationStore(ObservationStore):
    def _add(self,prefix,provider_record_id,provenance,fields):
        text(provider_record_id)
        if not isinstance(provenance,NormalizationProvenance): raise ValidationError('Normalization provenance required')
        common=provenance.fields(); semantic={k:v for k,v in common.items() if k!='normalized_at_utc'}
        oid=prefix+'_'+hashlib.sha256(canonical_json(dict(record=provider_record_id,profile=semantic,fields=fields)).encode()).hexdigest()[:32]
        row=dict(observation_id=oid,provider_record_id=provider_record_id,**fields,**common)
        with (nullcontext() if self.db.connection.in_transaction else self.db.transaction()):
            existing=self.db.execute('SELECT * FROM '+TABLES[prefix]+' WHERE observation_id=?',(oid,)).fetchone()
            if existing is not None:
                if any(existing[k]!=v for k,v in row.items() if k!='normalized_at_utc'): raise ArtifactIntegrityError('Normalization identity mismatch')
                return dict(existing)
            self.db.execute('INSERT INTO '+TABLES[prefix]+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
        return row
