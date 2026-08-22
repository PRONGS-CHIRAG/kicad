"""A real MCP server exposing KiCAD schematic edits over stdio.

Why this exists: `MitosExecutor` was written against a capability matrix that
could never be confirmed, because no Mitos MCP server was reachable — the tool
names in `DEFAULT_TOOL_MAP` were educated guesses, and the only thing the
adapter had ever talked to was a test double. That made the whole MCP path
decorative.

This server implements exactly the contract that adapter already speaks, against
real files, using the same edit primitives as the local executor
(`app.kicad.edits`). So `KICAD_MITOS_EXECUTOR=mitos` now runs the real pipeline
— checkpoint, execute over JSON-RPC, KiCAD ERC, deterministic decision, rollback
— through a real MCP server rather than a stub.

It is NOT the Mitos server, and it does not make the Mitos matrix confirmed. It
makes the *adapter* verified: the client, the tool contract and the argument
shapes are exercised against something that genuinely edits a schematic. If the
real Mitos server turns up, its tool names go in the same configurable map.

    python3 -m app.mcp_server

Speaks MCP 2024-11-05 over stdin/stdout: `initialize`, `tools/list`,
`tools/call`. One JSON-RPC message per line.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable

from .kicad import edits, writer
from .kicad.reader import read_project

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "kicad-mitos-mcp", "version": "0.1.0"}

_PROJECT_PROPERTY = {
    "project": {"type": "string", "description": "Absolute path to the KiCAD project directory"}
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "kicad_connect_pins",
        "description": (
            "Join two component pins onto one net by placing a global label on each "
            "pin's connection point. Pins are 'REF.PIN', e.g. 'U2.SDA' or 'U1.21'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_PROJECT_PROPERTY,
                "from_pin": {"type": "string", "description": "First pin, as REF.PIN"},
                "to_pin": {"type": "string", "description": "Second pin, as REF.PIN"},
                "net": {"type": "string", "description": "Net name to place on both pins"},
            },
            "required": ["project", "from_pin", "to_pin", "net"],
        },
    },
    {
        "name": "kicad_connect_pin_to_net",
        "description": "Join one component pin to an existing named net, e.g. '+3V3' or 'GND'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                **_PROJECT_PROPERTY,
                "pin": {"type": "string", "description": "Pin to connect, as REF.PIN"},
                "net": {"type": "string", "description": "Net to join"},
            },
            "required": ["project", "pin", "net"],
        },
    },
    {
        "name": "kicad_add_component",
        "description": (
            "Place a two-terminal symbol and connect its terminals to the two named nets. "
            "Skips placement when a matching part already bridges those nets."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_PROJECT_PROPERTY,
                "symbol": {"type": "string", "description": "Library id, e.g. 'Device:R'"},
                "value": {"type": "string", "description": "Component value, e.g. '4.7k'"},
                "connect": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "The two nets to bridge, [signal, rail]",
                },
            },
            "required": ["project", "symbol", "value", "connect"],
        },
    },
    {
        "name": "kicad_read_schematic",
        "description": "Read back components, their pins and the nets each pin sits on.",
        "inputSchema": {
            "type": "object",
            "properties": {**_PROJECT_PROPERTY},
            "required": ["project"],
        },
    },
]


class ToolError(RuntimeError):
    """A tool failed in a way the caller should see as isError, not a crash."""


def _project_dir(arguments: dict) -> Path:
    raw = arguments.get("project")
    if not raw:
        raise ToolError("'project' is required")
    project = Path(raw)
    if not project.is_dir():
        raise ToolError(f"not a directory: {project}")
    if not list(project.glob("*.kicad_sch")):
        raise ToolError(f"no .kicad_sch in {project}")
    return project


def _connect_pins(arguments: dict) -> str:
    project = _project_dir(arguments)
    for field in ("from_pin", "to_pin", "net"):
        if not arguments.get(field):
            raise ToolError(f"'{field}' is required")
    state = read_project(project)
    doc = writer.load(state.schematic_path)
    try:
        detail = edits.connect_pins(
            doc, state, arguments["from_pin"], arguments["to_pin"], arguments["net"]
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    writer.save(doc, state.schematic_path)
    return detail


def _connect_pin_to_net(arguments: dict) -> str:
    project = _project_dir(arguments)
    for field in ("pin", "net"):
        if not arguments.get(field):
            raise ToolError(f"'{field}' is required")
    state = read_project(project)
    doc = writer.load(state.schematic_path)
    try:
        detail = edits.connect_pin_to_net(doc, state, arguments["pin"], arguments["net"])
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    writer.save(doc, state.schematic_path)
    return detail


def _add_component(arguments: dict) -> str:
    project = _project_dir(arguments)
    symbol = arguments.get("symbol")
    if symbol != "Device:R":
        # Refusing beats placing the wrong footprint: the only supporting part any
        # action type asks for today is a resistor.
        raise ToolError(f"only 'Device:R' can be placed, not {symbol!r}")
    connect = arguments.get("connect") or []
    if len(connect) != 2:
        raise ToolError("'connect' must name exactly two nets, [signal, rail]")
    state = read_project(project)
    doc = writer.load(state.schematic_path)
    detail, added = edits.add_pullup(
        doc,
        state,
        net=connect[0],
        to_net=connect[1],
        value=arguments.get("value") or "4.7k",
        project_name=state.schematic_path.stem,
    )
    if added:
        writer.save(doc, state.schematic_path)
    return detail


def _read_schematic(arguments: dict) -> str:
    project = _project_dir(arguments)
    state = read_project(project)
    return json.dumps(
        {
            "components": state.component_summaries(),
            "nets": {name: sorted(pins) for name, pins in sorted(state.nets.items())},
        }
    )


HANDLERS: dict[str, Callable[[dict], str]] = {
    "kicad_connect_pins": _connect_pins,
    "kicad_connect_pin_to_net": _connect_pin_to_net,
    "kicad_add_component": _add_component,
    "kicad_read_schematic": _read_schematic,
}


def handle(message: dict) -> dict | None:
    """Return the JSON-RPC response for `message`, or None for a notification."""
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            result = {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
        else:
            try:
                result = {"content": [{"type": "text", "text": handler(params.get("arguments") or {})}]}
            except ToolError as exc:
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            except Exception as exc:  # noqa: BLE001 - a tool fault is a result, not a crash
                logger.exception("tool %s raised", name)
                result = {
                    "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    "isError": True,
                }
    elif request_id is None:
        return None  # any other notification, including notifications/initialized
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }

    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue  # a malformed line has no id to answer against
        response = handle(message)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
