"""Mitos MCP executor.

The adapter speaks MCP over stdio and only ever forwards schema-validated
actions. Tool names are configurable because the Mitos capability matrix has to
be confirmed against a running server (see docs/mitos-capabilities.md).
"""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
from pathlib import Path

from ..models import ActionPlan, ConnectPins, ConnectPinToNet, EnsurePullup, ExecutionResult, ExecutionStep
from .base import Executor

logger = logging.getLogger(__name__)

DEFAULT_TOOL_MAP = {
    "connect_pins": "kicad_connect_pins",
    "connect_pin_to_net": "kicad_connect_pin_to_net",
    "add_component": "kicad_add_component",
    "read_schematic": "kicad_read_schematic",
}


class McpStdioClient:
    """Minimal MCP JSON-RPC client over a stdio server process."""

    def __init__(self, command: str, timeout: float = 120.0) -> None:
        self.command = command
        self.timeout = timeout
        self._process: subprocess.Popen[str] | None = None
        self._next_id = 0

    def __enter__(self) -> McpStdioClient:
        self._process = subprocess.Popen(
            shlex.split(self.command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "kicad-mitos", "version": "0.1.0"},
            },
        )
        self._notify("notifications/initialized", {})
        return self

    def __exit__(self, *_: object) -> None:
        if self._process is not None:
            self._process.terminate()
            self._process.wait(timeout=10)
            self._process = None

    def _send(self, payload: dict) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("MCP server process is not running")
        self._process.stdin.write(json.dumps(payload) + "\n")
        self._process.stdin.flush()

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict) -> dict:
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("MCP server process is not running")
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            line = self._process.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP server closed the connection during {method}")
            message = json.loads(line)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"MCP error for {method}: {message['error']}")
            return message.get("result", {})

    def list_tools(self) -> list[dict]:
        return self._request("tools/list", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(f"tool {name} failed: {result.get('content')}")
        return result


class MitosExecutor(Executor):
    name = "mitos"

    def __init__(self, command: str | None, tool_map: dict[str, str] | None = None) -> None:
        self.command = command
        self.tool_map = {**DEFAULT_TOOL_MAP, **(tool_map or {})}

    @property
    def available(self) -> bool:
        return bool(self.command)

    def capabilities(self) -> list[dict]:
        if not self.available:
            return []
        with McpStdioClient(self.command) as client:
            return client.list_tools()

    def execute(self, project_dir: Path, plan: ActionPlan) -> ExecutionResult:
        if not self.available:
            return ExecutionResult(
                completed=False,
                steps=[],
                error="Mitos MCP server is not configured (set KICAD_MITOS_MITOS_COMMAND)",
            )
        steps: list[ExecutionStep] = []
        with McpStdioClient(self.command) as client:
            for action in plan.actions:
                tool, arguments = self._map_action(project_dir, action)
                try:
                    client.call_tool(tool, arguments)
                except RuntimeError as exc:
                    steps.append(
                        ExecutionStep(action_id=action.id, tool=tool, status="failed", detail=str(exc))
                    )
                    return ExecutionResult(completed=False, steps=steps, error=str(exc))
                steps.append(
                    ExecutionStep(
                        action_id=action.id, tool=tool, status="applied", detail=json.dumps(arguments)
                    )
                )
        return ExecutionResult(completed=True, steps=steps)

    def _map_action(
        self, project_dir: Path, action: ConnectPins | ConnectPinToNet | EnsurePullup
    ) -> tuple[str, dict]:
        base = {"project": str(project_dir)}
        if isinstance(action, ConnectPins):
            return self.tool_map["connect_pins"], {
                **base,
                "from_pin": action.from_pin,
                "to_pin": action.to_pin,
                "net": action.net_name,
            }
        if isinstance(action, ConnectPinToNet):
            return self.tool_map["connect_pin_to_net"], {**base, "pin": action.pin, "net": action.net}
        if isinstance(action, EnsurePullup):
            return self.tool_map["add_component"], {
                **base,
                "symbol": "Device:R",
                "value": action.value,
                "connect": [action.net, action.to_net],
            }
        raise TypeError(f"unsupported action type {action.type}")  # pragma: no cover
