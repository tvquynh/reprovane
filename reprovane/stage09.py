"""Shared persistence plumbing, not acquisition, discovery or identity logic."""
from contextlib import contextmanager
from .core.migrations import apply_migrations
from .discovery import DiscoveryStore
from .providers.scimago import _transaction


class Stage09Store:
    def __init__(self,database):
        self.db=database
        if not hasattr(database,'_stage09_discovery'): database._stage09_discovery=DiscoveryStore(database)
        self.discovery=database._stage09_discovery; self.foundation=self.discovery.foundation; self.acquisitions=self.discovery.acquisitions
        if not hasattr(database,'_stage09_authorization'): database._stage09_authorization=None
        # One combined store can be used for both services on the same connection.
        database.connection.create_function('stage09_authorized',2,lambda kind,rid:int(database._stage09_authorization==(kind,rid)))
        apply_migrations(database.connection,target_version=9)

    @contextmanager
    def authorized(self,kind,rid):
        before=self.db._stage09_authorization; self.db._stage09_authorization=(kind,rid)
        try: yield
        finally: self.db._stage09_authorization=before

    def insert(self,table,row): return self.foundation._insert(table,row)
    def update(self,table,key,value,**fields): self.db.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in fields)+' WHERE '+key+'=?',(*fields.values(),value))
    def one(self,table,key,value): return self.foundation._one(table,key,value)


def stable(row): return {k:v for k,v in row.items() if not k.endswith('_at_utc')}
