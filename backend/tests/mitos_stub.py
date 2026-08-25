"""A stdio MCP server standing in for Mitos, so the adapter and probe are testable.

This is a test double, not a Mitos implementation. It speaks just enough of the
protocol to exercise the real client: initialize, tools/list, tools/call.

    python -m tests.mitos_stub [--fail-on TOOL] [--calls PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TOOLS = [
    {
        "name": "kicad_connect_pins",
        "description": "Join two component pins onto one net.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project directory"},
                "from_pin": {"type": "string", "description": "REF.PIN"},
                "to_pin": {"type": "string", "description": "REF.PIN"},
                "net": {"type": "string", "description": "Net name"},
            },
            "required": ["project", "from_pin", "to_pin", "net"],
        },
    },
    {
        "name": "kicad_connect_pin_to_net",
        "description": "Join one pin to a named net.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "pin": {"type": "string"},
                "net": {"type": "string"},
            },
            "required": ["project", "pin", "net"],
        },
    },
    {
        "name": "kicad_add_component",
        "description": "Place a symbol and connect its terminals.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "symbol": {"type": "string"},
                "value": {"type": "string"},
                "connect": {"type": "array"},
            },
            "required": ["project", "symbol"],
        },
    },
    {"name": "kicad_get_selection", "description": "Read the current KiCAD selection.", "inputSchema": {}},
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-on", default=None, help="tool name that should report an error")
    parser.add_argument("--calls", type=Path, default=None, help="append each tools/call here as JSON lines")
    args = parser.parse_args(argv)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        method, request_id = message.get("method"), message.get("id")
        if request_id is None:  # a notification needs no reply
            continue

        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "mitos-stub"},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            if args.calls:
                with args.calls.open("a") as handle:
                    handle.write(json.dumps({"name": name, "arguments": params.get("arguments")}) + "\n")
            if name == args.fail_on:
                result = {"isError": True, "content": [{"type": "text", "text": f"{name} refused"}]}
            else:
                result = {"content": [{"type": "text", "text": f"{name} ok"}]}
        else:
            error = {"code": -32601, "message": f"unsupported method {method}"}
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": error}) + "\n")
            sys.stdout.flush()
            continue

        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
