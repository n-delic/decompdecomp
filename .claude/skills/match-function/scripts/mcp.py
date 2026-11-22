#!/usr/bin/env python3
"""Call any of the app's MCP tools as a setup.py session, over the connection that holds its claim.

    mcp.py HOLDER --list                 every tool the app offers to this connection: name and one line
    mcp.py HOLDER TOOL --help            the tool's description and arguments (with the allowed values of each, e.g. the
                                         categories of report_repair), read from the app's own tool list
    mcp.py HOLDER TOOL ['{"json": "args"}']
                                         call it and print the result (arguments: a JSON object, `-` for stdin; none: {})

Never write your own gateway client: this reuses setup.py's (the same connection and token as the claim, which stay in
connection.json and are never printed). A "still running" answer repeats the identical call until the app finishes it;
an "accepted ... do not repeat" answer is printed once and never repeated. A call whose arguments break the tool's
schema (an unknown category, say) is refused here with the valid values, before anything reaches the app; a tool's own
refusal is printed with those values too.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import setup  # noqa: E402  (the session's Gateway and its connection file)


def tool_list(gateway):
    """The tools the app offers this connection (tools/list, every page), each with its input schema."""
    tools, cursor = [], None
    while True:
        result = gateway.rpc("tools/list", {"cursor": cursor} if cursor else {})
        tools += result.get("tools", [])
        cursor = result.get("nextCursor")
        if not cursor:
            return tools


def first_line(text):
    return ((text or "").strip().splitlines() or [""])[0]


def choices(schema):
    """{argument: allowed values} for every argument of the tool's input schema that lists its values (an enum, possibly
    under anyOf/oneOf or as the items of an array)."""
    found = {}
    for name, spec in (schema or {}).get("properties", {}).items():
        values = []
        for part in [spec, spec.get("items") or {}, *spec.get("anyOf", []), *spec.get("oneOf", [])]:
            values += [v for v in part.get("enum", []) if v not in values]
        if values:
            found[name] = values
    return found


def describe(tool):
    """The tool's help: description, then each argument (required marked), its type, text and allowed values."""
    schema = tool.get("inputSchema") or {}
    lines = [tool["name"], (tool.get("description") or "").strip(), "", "arguments:"]
    required = set(schema.get("required", []))
    allowed = choices(schema)
    for name, spec in schema.get("properties", {}).items():
        kind = spec.get("type") or "|".join(p.get("type", "?") for p in spec.get("anyOf", [])) or "any"
        lines.append(f"  {name}{' (required)' if name in required else ''}: {kind}" +
                     (f" - {first_line(spec['description'])}" if spec.get("description") else ""))
        if name in allowed:
            lines.append(f"      one of: {', '.join(map(str, allowed[name]))}")
    if len(lines) == 4:
        lines.append("  (none)")
    return "\n".join(lines)


def check_values(tool, arguments):
    """Refuse a value outside an argument's listed values, naming the valid ones (the app's schema, never a copy here)."""
    for name, allowed in choices(tool.get("inputSchema")).items():
        given = arguments.get(name)
        for value in given if isinstance(given, list) else [] if given is None else [given]:
            if value not in allowed:
                sys.exit(f"{tool['name']}: {name}={value!r} is not valid; use one of: {', '.join(map(str, allowed))}")


def parse_arguments(text):
    try:
        arguments = json.loads(sys.stdin.read() if text == "-" else text or "{}")
    except ValueError as error:
        sys.exit(f"the arguments must be one JSON object ({error})")
    if not isinstance(arguments, dict):
        sys.exit("the arguments must be one JSON object")
    return arguments


def run(argv):
    if len(argv) < 2 or argv[0] in ("-h", "--help"):
        sys.exit(__doc__)
    holder, rest = argv[0], argv[1:]
    gateway, _info = setup.session_gateway(holder=holder)
    try:
        tools = tool_list(gateway)
    except setup.McpError as error:
        sys.exit(f"the app wouldn't list its tools: {error}")
    if rest == ["--list"]:
        width = max((len(t["name"]) for t in tools), default=0)
        for tool in sorted(tools, key=lambda t: t["name"]):
            print(f"{tool['name']:<{width}}  {first_line(tool.get('description'))}")
        return 0
    name = rest[0]
    tool = next((t for t in tools if t["name"] == name), None)
    if tool is None:
        sys.exit(f"the app has no tool {name!r}: `mcp.py {holder} --list` shows what it offers")
    if rest[1:] in (["--help"], ["-h"]):
        print(describe(tool))
        return 0
    if len(rest) > 2:
        sys.exit(__doc__)
    arguments = parse_arguments(rest[1] if len(rest) == 2 else "")
    check_values(tool, arguments)
    try:
        print(gateway.call(name, arguments))
    except setup.McpError as error:
        hints = "; ".join(f"{k}: {', '.join(map(str, v))}" for k, v in choices(tool.get("inputSchema")).items())
        sys.exit(f"{error}" + (f"\nvalid values - {hints}" if name == "report_repair" and hints else ""))
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
