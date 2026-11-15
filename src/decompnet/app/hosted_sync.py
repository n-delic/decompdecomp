"""Verified matches from a hosted server, taken into the full project in this app (docs/HOSTED.md).

    decomp hosted-link SLUG --server https://decomp.example --token dd_... [--remote SLUG]
    decomp hosted-link SLUG --remove

A hosted server (gateway/src/hosted.rs) serves a small copy of a project made with `decomp hosted-export`. Its
agents' matches are verified by compiler servers, then listed at /v1/p/<slug>/matches. While the app runs it asks
each linked server for new ones every minute. It takes each in through the same path as a room member's match
(room_matches.verify): the app's own judge compiles it against the full project and records it like any match,
credited to a local agent named for the hosted user. A match the app's judge doesn't reproduce is not recorded.

Links live in .decomp/hosted-links.json (owner-only): the token is a verifier's, kept out of project databases and
saves. The file is read again each round, so linking with the command line needs no restart.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("decompdecomp")
ROUND_SECONDS = 60.0
PAGE = 1000  # the server's own limit per request


def links_file(internal: Path) -> Path:
    return Path(internal) / "hosted-links.json"


def read_links(internal: Path) -> list[dict]:
    try:
        links = json.loads(links_file(internal).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [link for link in links if isinstance(link, dict) and link.get("slug") and link.get("server")]


def write_links(internal: Path, links: list[dict]) -> None:
    path = links_file(internal)
    part = path.with_name(path.name + ".part")
    descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        json.dump(links, out, indent=1)
    os.replace(part, path)


def link(internal: Path, slug: str, server: str, token: str, remote: str = "") -> dict:
    links = [item for item in read_links(internal) if item["slug"] != slug]
    item = {"slug": slug, "remote": remote or slug, "server": server.rstrip("/"), "token": token, "since": 0.0}
    write_links(internal, [*links, item])
    return item


def unlink(internal: Path, slug: str) -> bool:
    links = read_links(internal)
    kept = [item for item in links if item["slug"] != slug]
    write_links(internal, kept)
    return len(kept) != len(links)


def _agent(net, user: str):
    """The local agent credited with a hosted user's matches."""
    from ..network import Agent
    name = ("hosted." + re.sub(r"[^A-Za-z0-9._-]", "-", user or "someone"))[:40]
    row = net.store.one("SELECT id,name,owner,trusted FROM agents WHERE lower(name)=lower(?)", name)
    if row is None:
        net.register_agent(name, user[:60] or None, origin="hosted")
        row = net.store.one("SELECT id,name,owner,trusted FROM agents WHERE lower(name)=lower(?)", name)
    return Agent(row["id"], row["name"], row["owner"], bool(row["trusted"]))


def pull(net, item: dict, get=None) -> dict:
    """Take in one link's new verified matches. Returns counts and the newest match time seen (the next `since`)."""
    from ..network import Network
    from . import room_matches
    if get is None:
        import httpx

        def get(url, token):
            reply = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
            reply.raise_for_status()
            return reply.json()
    slug = item["slug"]
    proj = Network._project(net, slug)
    store = net.project_store(proj["id"])
    since = float(item.get("since") or 0)
    counts = {"recorded": 0, "already": 0, "not_reproduced": 0, "skipped": 0, "since": since}
    while True:
        page = get(f"{item['server']}/v1/p/{item.get('remote') or slug}/matches?since={since!r}", item["token"])
        matches = page.get("matches") or []
        for match in matches:
            since = max(since, float(match.get("matched_at") or 0))
            rows = store.all("SELECT name,status,vram FROM functions WHERE project_id=? AND name=? AND gone_mark IS NULL",
                             proj["id"], match.get("function"))
            if len(rows) != 1 or rows[0]["vram"] != match.get("vram") or not match.get("source"):
                counts["skipped"] += 1  # (not the same function here)
                continue
            if rows[0]["status"] == "matched":
                counts["already"] += 1
                continue
            agent = _agent(net, str(match.get("by") or ""))
            try:
                result = room_matches.verify(net, slug, agent, rows[0]["name"], match["source"])
            except Exception as error:
                log.warning("Hosted match %s/%s wasn't taken in: %s", slug, match.get("function"), error)
                counts["not_reproduced"] += 1
                continue
            counts["recorded" if result.get("matched") else "not_reproduced"] += 1
        counts["since"] = since
        if len(matches) < PAGE:
            return counts


class Sync:
    """While the app runs: every minute, each linked project's new hosted matches."""

    def __init__(self, net, internal: Path):
        self.net, self.internal = net, Path(internal)
        self.stopping = threading.Event()
        self.last: dict[str, dict] = {}  # slug -> the last round's counts or error, for the UI and logs

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="hosted-sync").start()

    def round(self) -> None:
        for item in read_links(self.internal):
            try:
                counts = pull(self.net, item)
            except Exception as error:
                self.last[item["slug"]] = {"error": str(error), "at": time.time()}
                log.warning("Hosted sync of %s failed: %s", item["slug"], error)
                continue
            self.last[item["slug"]] = counts | {"at": time.time()}
            if counts["recorded"] or counts["not_reproduced"]:
                log.info("Hosted sync of %s: %s", item["slug"], counts)
            if counts["since"] != item.get("since"):
                # (re-read: the command line may have changed the file meanwhile)
                links = read_links(self.internal)
                for other in links:
                    if other["slug"] == item["slug"] and other["server"] == item["server"]:
                        other["since"] = counts["since"]
                write_links(self.internal, links)

    def _run(self) -> None:
        while not self.stopping.wait(ROUND_SECONDS):
            self.round()

    def stop(self) -> None:
        self.stopping.set()


def main(args, internal: Path) -> int:
    """`decomp hosted-link`"""
    if args.remove:
        print(f"{args.slug}: link removed" if unlink(internal, args.slug) else f"{args.slug} wasn't linked")
        return 0
    if not args.server or not args.token:
        print("hosted-link needs --server and --token (a verifier's token), or --remove")
        return 2
    item = link(internal, args.slug, args.server, args.token, args.remote or "")
    print(f"{args.slug} takes in verified matches from {item['server']}/p/{item['remote']} (the running app checks "
          "every minute; no restart needed)")
    return 0
