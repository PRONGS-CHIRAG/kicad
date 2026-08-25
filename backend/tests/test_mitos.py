"""Coverage for the Mitos MCP adapter and the capability probe.

These run against `tests.mitos_stub`, a stdio MCP server test double. They prove
the client, the action->tool mapping and the probe work; they prove nothing about
the real Mitos server, whose tool names are still unconfirmed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.execution.mitos import MitosExecutor
from app.models import ActionPlan, ConnectPins, ConnectPinToNet, EnsurePullup
from app.probe_mitos import main as probe_main
from app.probe_mitos import probe, render_matrix

# Absolute path, not `-m tests.mitos_stub`, so the tests do not depend on cwd.
STUB = f"{sys.executable} {Path(__file__).with_name('mitos_stub.py')}"


def _plan() -> ActionPlan:
    return ActionPlan(
        goal="Connect U2 to U1 using I2C",
        selected_components=["U1", "U2"],
        actions=[
            ConnectPins(id="a1", **{"from": "U2.SDA", "to": "U1.GPIO21"}, net_name="I2C_SDA"),
            ConnectPinToNet(id="a2", pin="U2.VCC", net="+3V3"),
            EnsurePullup(id="a3", net="I2C_SDA", to_net="+3V3", value="4.7k"),
        ],
    )


def test_executor_forwards_every_action_to_the_mapped_tool(tmp_path: Path) -> None:
    calls = tmp_path / "calls.jsonl"
    executor = MitosExecutor(f"{STUB} --calls {calls}")
    result = executor.execute(tmp_path, _plan())

    assert result.completed is True
    assert [step.status for step in result.steps] == ["applied"] * 3
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    assert [call["name"] for call in recorded] == [
        "kicad_connect_pins",
        "kicad_connect_pin_to_net",
        "kicad_add_component",
    ]
    # Only schema-validated values are forwarded; no free-form model output.
    assert recorded[0]["arguments"]["from_pin"] == "U2.SDA"
    assert recorded[0]["arguments"]["net"] == "I2C_SDA"
    assert recorded[2]["arguments"]["connect"] == ["I2C_SDA", "+3V3"]


def test_executor_stops_at_the_first_failing_tool(tmp_path: Path) -> None:
    executor = MitosExecutor(f"{STUB} --fail-on kicad_connect_pin_to_net")
    result = executor.execute(tmp_path, _plan())

    assert result.completed is False
    assert result.error and "refused" in result.error
    # Stops rather than pressing on, so the batch stays a transaction.
    assert [step.status for step in result.steps] == ["applied", "failed"]


def test_executor_without_a_command_is_a_failure_not_a_crash(tmp_path: Path) -> None:
    result = MitosExecutor(None).execute(tmp_path, _plan())
    assert result.completed is False
    assert "not configured" in (result.error or "")


def test_probe_reports_the_tools_the_server_advertises() -> None:
    tools = probe(STUB)
    assert {tool["name"] for tool in tools} >= {"kicad_connect_pins", "kicad_add_component"}

    matrix = render_matrix(STUB, tools)
    assert "Mitos capability matrix (probed)" in matrix
    assert "`kicad_connect_pins`" in matrix
    # The stub does not advertise a read tool, so the matrix must say so.
    assert "**no**" in matrix
    # Tools the backend does not map are still surfaced.
    assert "kicad_get_selection" in matrix


def test_probe_refuses_to_write_a_matrix_without_a_server(tmp_path: Path) -> None:
    out = tmp_path / "mitos.md"
    assert probe_main(["--command", "", "--out", str(out)]) == 2
    assert not out.exists()

    assert probe_main(["--command", f"{sys.executable} -c 'raise SystemExit(1)'", "--out", str(out)]) == 1
    assert not out.exists(), "a failed probe must never leave a matrix behind"


def test_probe_writes_the_matrix_when_a_server_answers(tmp_path: Path) -> None:
    out = tmp_path / "mitos.md"
    assert probe_main(["--command", STUB, "--out", str(out)]) == 0
    assert "kicad_connect_pins" in out.read_text()
