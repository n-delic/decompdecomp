"""Explicit operational and transcript files for user-requested storage maintenance."""
from pathlib import Path


def operational(net):
    return list(dict.fromkeys([net.store, *(store for store,_ in net.project_stores())]))


def all_files(net):
    from .transcripts import service
    stores = operational(net)
    for store,pids in net.project_stores():
        if store is not net.store and Path(str(store.path)+'.transcripts.sqlite3').is_file():
            stores.append(service(net).for_project(pids[0]).store)
    return list(dict.fromkeys(stores))
