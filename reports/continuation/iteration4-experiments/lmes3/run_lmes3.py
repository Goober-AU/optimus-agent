"""LME-S sequential-queue wrapper around scripts/memory_question_loop.py.

Safe patches (no shared-code edits):
1. find_entry prefers the longmemeval_s entry when a qid exists in both
   longmemeval_m and longmemeval_s (M entries sort first in the ledger file).
   Returns the ORIGINAL entry object so ledger appends persist.
2. prepare_typeb forces captureProtocol
   optimus-memory-capture/temporal-authority/1.0.0 (smoke5/KU validated mode).
3. prepare_typea honors the LMESQ_STORE env var (typeA regression gates
   against frozen stores): overrides entry.store_path for the copy-out only.
"""
import os
import sys

IMP4 = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4b-lmes3"
sys.path.insert(0, IMP4 + "/scripts")

import memory_question_loop as M  # noqa: E402

BENCHMARK = "longmemeval_s"
CAPTURE_PROTOCOL = "optimus-memory-capture/temporal-authority/1.0.0"

_stock_find_entry = M.find_entry


def find_entry_lmes(ledger, qid):
    for entry in ledger.get("entries", []):
        if entry.get("qid") == qid and entry.get("benchmark") == BENCHMARK:
            return entry
    return _stock_find_entry(ledger, qid)


M.find_entry = find_entry_lmes

_stock_prepare_typeb = M.prepare_typeb


def prepare_typeb_forced(*args, **kwargs):
    kwargs["capture_protocol"] = CAPTURE_PROTOCOL
    return _stock_prepare_typeb(*args, **kwargs)


M.prepare_typeb = prepare_typeb_forced

_stock_prepare_typea = M.prepare_typea


def prepare_typea_store(*args, **kwargs):
    store = os.environ.get("LMESQ_STORE")
    if store and args:
        entry = dict(args[0])
        entry["store_path"] = store
        args = (entry,) + args[1:]
    return _stock_prepare_typea(*args, **kwargs)


M.prepare_typea = prepare_typea_store

if __name__ == "__main__":
    sys.exit(M.main(sys.argv[1:]))
