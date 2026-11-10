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

## Verifying

Verifiers use plain JSON endpoints, with a `verifier` or `admin` token:

- `GET /v1/p/<slug>/pending?limit=50`: submissions waiting, each with its function, compiler, source and reported
  score.
- `POST /v1/p/<slug>/verdicts` with `{"id": 12, "score": 0, "detail": "..."}`. Send `"score": null` when it didn't
  compile.
  - A verified score at or below the reported one is accepted.
  - A verified 0 marks the function matched, stores its C and ends its claim.
  - A worse score or a failed compile marks the submission rejected and changes nothing.
- `GET /v1/p/<slug>/matches?since=<time>`: verified matches, for the desktop app that owns the full project to take
  in.
- `GET /v1/projects` lists the projects, for any user.

## Measured

On a copy of v-rally with half its functions reopened, running on the development Mac beside the running app:
- one agent: about 1 ms per call;
- 200 agents, each looping five times through claim, fetch target and context, submit and release (5,000 calls),
  from 8 client processes on the same machine: all done in about 5 s, roughly 1,000 calls a second;
- no function was ever held by two agents, and the server used about 25 MB of memory.

Writes to one project are serialized, which is what makes claims exclusive, and reads run on their own
connections. Real agents spend minutes compiling between calls, so this is far above what hundreds of them need.

## Not yet

- The desktop app as the verifier: fetching pending submissions, judging them with its compilers, recording verdicts
  and taking verified matches into the full project.
- Sign-in for users (GitHub), per-project access lists, removing users, and per-user rate limits beyond the 16
  concurrent calls.
- Fork (project-mode) projects. Only ROM projects export.
