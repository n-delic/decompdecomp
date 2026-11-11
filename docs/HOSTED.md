# Hosted projects

`decomp-mcp-gateway hosted` serves shared projects on a server, without the desktop app. Agents run on other
people's machines with their own harness. Over MCP they fetch functions, claim them, compile on their own computer
and submit the C with the score they measured. The server never compiles. A trusted verifier compiles each
submission, and only a verified score changes a function.

This is the second step towards a standalone MCP server. The desktop app and its own gateway mode work as before.
Hosted projects are separate files, and in them the server is the only thing that claims.

## Running a server

```sh
decomp hosted-export v-rally /srv/decomp        # from the desktop app's home: /srv/decomp/projects/v-rally.sqlite3
decomp-mcp-gateway hosted add-user --dir /srv/decomp --name alice                  # prints alice's API token
decomp-mcp-gateway hosted add-user --dir /srv/decomp --name checker --role verifier
decomp-mcp-gateway hosted serve --dir /srv/decomp --listen 127.0.0.1:8080
```

- Put a TLS reverse proxy in front of it (Caddy, nginx) to serve it on the internet.
- Tokens are shown once and stored as SHA-256 hashes in `server.sqlite3`. Roles are `agent`, `verifier` and `admin`.
- Running `hosted-export` again updates the project in place, in one transaction, so the server can keep running.
  The server keeps its claims, submissions, notes, matches and better scores. A function matched on the server
  stays matched, and functions gone from the project become `removed`.

## Who may use it

- **Roles:** `agent` uses projects over MCP; `verifier` registers compiler servers and judges; `admin` manages users
  and may use everything.
- **Access:** the server is `open` (every user may use every project) or `invite` (agents and verifiers use only the
  projects granted to them). A verifier is leased work only from projects it may use, and `/v1/projects` lists only
  those.
- **Rate:** each user may make `--rate` tool calls a minute (600 by default), and have 16 running at once.
- Tokens are shown once. Replacing one stops the old one at once; so does removing a user, which also removes their
  grants and compiler-server entries (their claims run out).

| | On the server | Over HTTP (admins) |
|---|---|---|
| Add a user | `hosted add-user --name N --role R` | `POST /v1/users {name, role}` |
| List users and access | `hosted list-users` | `GET /v1/users` |
| Remove a user | `hosted remove-user --name N` | `DELETE /v1/users/N` |
| Replace a token | `hosted rotate-token --name N` | `POST /v1/users/N/token` |
| Grant or revoke a project | `hosted grant` / `revoke --name N --project P` | `POST /v1/users/N/access {project, allow}` |
| Open or invite-only | `hosted policy --access open\|invite` | `POST /v1/policy {access}` |

Every command takes `--dir`. Changes over HTTP apply at once. Changes made with the command line while the server
runs apply within 10 seconds (it remembers users and grants that long).

## The hosted project file

`hosted_export.py` writes one small SQLite file per project, holding what an agent needs to decompile and judge a
function on its own computer:
- each function's name, unit, address, size, target bytes, relocations, assembly and effective compiler (toolchain
  and flags only: no paths from the owner's computer);
- the latest context header of each unit;
- each function's status, best score and C, and its matching C.

Assembly, C and headers are zlib-compressed. There is no attempt history, transcript, log or saved-progress copy,
which are most of a desktop project. v-rally (2,121 functions) is 5.7 MB, against 874 MB for its project database.
`meta.format` is the file's version.

## What agents get

The MCP endpoint is `/p/<slug>/mcp`, with `Authorization: Bearer <token>`. It is stateless JSON-RPC.

| Tool | |
|---|---|
| `project_info` | the game, compiler, progress, and how to work here |
| `list_functions(status, limit)` | functions by address: status, size, best verified score, holder |
| `get_function(function, section)` | `summary` (with assembly), `assembly`, `target` (the bytes to equal, hex, and relocations), `context`, `best`, `matched`, `notes`, `all` |
| `claim_function(function, strategy)` | the next open function (`easiest`, `hardest`, `closest`, `random`) or a named one, for 30 minutes; at most 6 at once |
| `get_claims()` | your live claims and their tokens |
| `submit_result(function, claim_token, source, score)` | C you compiled and the score you measured (0 = byte-identical); renews the claim |
| `release_function(function, claim_token, notes)` | give a claim back with notes |
| `add_note(text, function)` | a note for everyone |

- A claim is one atomic update of the project's file, so two agents never hold the same function.
- A function is named by name, `name@unit` or address.
- Each user may have 16 calls running at once.

## Verifying: compiler servers

A verifier is a compiler server: any computer with decompdecomp, the game imported and its compilers installed.
The app's exact judge links each function at its address with the whole project's symbols and the ROM, so a
verifier needs the full project, not just the hosted file. Several can serve one hosted server; each judges only
the projects and compilers it has.

```sh
# on the server, or remotely by an admin: POST /v1/users {"name": "mac-studio", "role": "verifier"}
decomp-mcp-gateway hosted add-user --dir /srv/decomp --name mac-studio --role verifier
# on the compiler server (DECOMP_HOME: the home with the games imported)
decomp-mcp-gateway verifier --server https://decomp.example --token dd_... --name mac-studio --slots 4
```

`decomp-mcp-gateway verifier` (gateway/src/verifier.rs) does the following:
- It runs one judge process per slot, `decomp judge-worker` by default (src/decompnet/hosted_judge.py), or another
  program given with `--judge PROGRAM --judge-arg ARG`. A judge reads one JSON request per line and answers one per
  line.
  - `decomp judge-worker` opens the home's projects as the reader process does, with no saved-progress sync, so it
    can run beside the app.
  - It finds the submission's function by name and address, and checks that its target bytes equal the hosted
    ones (another build of the game is never judged).
  - It compiles with the app's own judge (`RomNetwork._judge_with`). A match there is a match in the app.
- It asks a judge which ROM projects and compilers it has, and registers them (`POST /v1/verifiers`). It does this
  again every 5 minutes.
- It leases submissions up to its free slots (`POST /v1/work/lease`), for its projects and only those whose compiler
  it has. A lease lasts 10 minutes, and no two verifiers hold the same submission.
- It posts each verdict (`POST /v1/p/<slug>/verdicts` with its verifier id).
- What it can't judge it gives back (`POST /v1/work/release`): another build of the game, or a judge that crashed
  or took over 5 minutes, which it replaces. After 3 tries a submission is marked `unverifiable`.
- `--idle-exit SECONDS` ends it after that long with nothing to do (for cron or CI); without it, it runs until
  stopped.

Verdicts:
- A verified 0 marks the function matched, stores its C and ends its claim.
- Any other successful compile is recorded with the verifier's score, and becomes the best if it is better. Agents'
  own tools measure differences their own way, so only a match has to agree.
- Rejected, changing nothing: it didn't compile, or it claimed a match that isn't one.

Other endpoints:
- `GET /v1/verifiers` (admins): the registered compiler servers, with their projects, compilers, slots, version
  and last contact.
- `POST /v1/users {name, role}` (admins): a new user's token, to register agents or compiler servers remotely.
- `GET /v1/p/<slug>/pending?limit=50` (verifiers): submissions waiting, for a verifier of your own.
- `GET /v1/p/<slug>/matches?since=<time>` (verifiers): the matches verified on this server (not the ones the project
  came with), for the desktop app that owns the full project to take in.
- `GET /v1/projects`: the projects, for any user.

On a copy of v-rally, with 10 matched functions reopened, a verifier on the development Mac judged 10 submissions
with the real compilers in about 3 s. The 8 with each function's real matching C were verified byte-identical. The 2
carrying another function's C were rejected ("the function isn't in what it compiled to").

## Measured

On a copy of v-rally with half its functions reopened, running on the development Mac beside the running app:
- one agent: about 1 ms per call;
- 200 agents, each looping five times through claim, fetch target and context, submit and release (5,000 calls),
  from 8 client processes on the same machine: all done in about 5 s, roughly 1,000 calls a second;
- no function was ever held by two agents, and the server used about 25 MB of memory.

Writes to one project are serialized, which is what makes claims exclusive, and reads run on their own
connections. Real agents spend minutes compiling between calls, so this is far above what hundreds of them need.

## Back into the full project

The desktop app that owns the project takes the server's verified matches in by itself:

```sh
decomp hosted-link v-rally --server https://decomp.example --token dd_...   # a verifier's token
decomp hosted-link v-rally --remove
```

- While the app runs it asks each linked server for new matches every minute (`app/hosted_sync.py`).
- It takes each in the way a room member's match comes in (`room_matches.verify`): the app's own judge compiles it
  against the full project and records it like any match.
- The match is credited to a local agent named `hosted.<user>`.
- A match the app's judge doesn't reproduce isn't recorded.
- Links are kept in `.decomp/hosted-links.json`, readable only by its owner and out of project databases and saves.
  The file is read again each round, so no restart is needed.

On a copy of v-rally, the whole loop ran with the real compilers:
1. Six matched functions were reopened in the project and its hosted file.
2. An agent submitted their C to the hosted server.
3. The Rust verifier confirmed all six.
4. The app took them in, re-judged, in 3 s. All six were matched again, credited to `hosted.alice`.

## Not yet

- A judge in Rust for a console, so a compiler server needs only the compilers and the game, not decompdecomp.
- Sign-in for users with GitHub, instead of tokens handed out by an admin. This needs a GitHub OAuth app of the
  server's own.
- Fork (project-mode) projects. Only ROM projects export.
