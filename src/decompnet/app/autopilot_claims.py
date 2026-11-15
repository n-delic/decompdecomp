"""Coalesce pass-wide lease maintenance while checking each selected claim atomically."""
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

CLEANUP_SECONDS=.5

_current=ContextVar('autopilot_claim_maintenance',default=None)


class Batch:
    def __init__(self):
        self.lock=threading.Lock()
        self.identity_time=float('-inf')
        self.identity_ids=None
        self.expired_at=float('-inf')

    def identities(self,net):
        # Read the catalog before taking a separate project's write transaction.
        with self.lock:
            now=time.monotonic()
            if self.identity_ids is None or now-self.identity_time>=CLEANUP_SECONDS:
                self.identity_ids={'machine_ids':net._machine_agent_ids(),
                                   'nonexclusive_ids':net._nonexclusive_agent_ids()}
                self.identity_time=now
            return self.identity_ids


def identities(net,store):
    if batch:=_current.get():return batch.identities(net)
    return {} if store is net.store else {'machine_ids':net._machine_agent_ids(),
                                         'nonexclusive_ids':net._nonexclusive_agent_ids()}


def expire(net,db,fid,ids):
    batch=_current.get()
    if batch is None:
        net._expire_claims(db,**ids);return
    # Transactions on this pass's project store serialize this timestamp. Expired
    # or legacy speculative claims on the selected function never wait for cadence.
    claim=db.execute('SELECT agent_id,expires_at FROM claims WHERE function_id=?',(fid,)).fetchone()
    if (time.monotonic()-batch.expired_at>=CLEANUP_SECONDS or claim and
            (claim['expires_at']<time.time() or claim['agent_id'] in ids['nonexclusive_ids'])):
        net._expire_claims(db,**ids)
        batch.expired_at=time.monotonic()


@contextmanager
def scope(batch):
    token=_current.set(batch)
    try:yield
    finally:_current.reset(token)
