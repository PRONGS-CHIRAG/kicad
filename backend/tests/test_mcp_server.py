"""The MCP server, driven by the real client over a real stdio subprocess.

`test_mitos.py` proves the adapter against a test double. These prove the other
half: that there is a server which actually edits KiCAD files, and that driving
the whole pipeline through MCP reaches the same verdict as the local executor.
Without this, `KICAD_MITOS_EXECUTOR=mitos` was a path nothing had ever run.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from app.execution.mitos import DEFAULT_TOOL_MAP, McpStdioClient, MitosExecutor
from app.kicad.checkpoint import hash_tree
from app.kicad.reader import read_project
from app.mcp_server import TOOLS, handle
from app.models import ActionPlan, ConnectPins, ConnectPinToNet, EnsurePullup, Protocol

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "projects"
# sys.executable, not "python3": the suite may well be running under a venv
# whose interpreter is the only one with the dependencies installed.
COMMAND = f"{sys.executable} -m app.mcp_server"

GOLDEN_INSTRUCTION = (
    "Connect these components using I2C with 3.3 V logic.\n"
    "Add the required pull-up resistors.\n"
    "Do not modify the USB circuit."
)


@pytest.fixture()
def demo(tmp_path: Path) -> Path:
    target = tmp_path / "esp32_i2c_demo"
    shutil.copytree(FIXTURES / "esp32_i2c_demo", target)
    return target


# ------------------------------------------------------------------ contract


def test_every_tool_the_executor_maps_is_advertised() -> None:
    """The map is only meaningful if the server really offers those names."""
    advertised = {tool["name"] for tool in TOOLS}
    assert set(DEFAULT_TOOL_MAP.values()) <= advertised


def test_advertised_tools_declare_usable_schemas() -> None:
    for tool in TOOLS:
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "project" in schema["properties"], f"{tool['name']} must take a project"
        assert "project" in schema["required"]
        assert tool["description"].strip()


def test_unknown_method_is_an_error_not_a_crash() -> None:
    response = handle({"jsonrpc": "2.0", "id": 1, "method": "nonsense/method", "params": {}})
    assert response["error"]["code"] == -32601


def test_notifications_get_no_response() -> None:
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) is None


# ------------------------------------------------------- real stdio round trip


def test_client_can_list_tools_over_a_real_subprocess() -> None:
    with McpStdioClient(COMMAND) as client:
        names = [tool["name"] for tool in client.list_tools()]
    assert sorted(names) == sorted(DEFAULT_TOOL_MAP.values())


def test_connect_pins_actually_edits_the_schematic(demo: Path) -> None:
    before = read_project(demo)
    assert before.pin_nets.get("U2.3") != "I2C_SDA"

    with McpStdioClient(COMMAND) as client:
        client.call_tool(
            "kicad_connect_pins",
            {"project": str(demo), "from_pin": "U2.SDA", "to_pin": "U1.GPIO21", "net": "I2C_SDA"},
        )

    after = read_project(demo)
    assert after.pin_nets.get("U2.3") == "I2C_SDA"
    assert after.pin_nets.get("U1.3") == "I2C_SDA"


def test_add_component_places_a_resistor_and_skips_a_duplicate(demo: Path) -> None:
    with McpStdioClient(COMMAND) as client:
        args = {
            "project": str(demo),
            "symbol": "Device:R",
            "value": "4.7k",
            "connect": ["I2C_SDA", "+3V3"],
        }
        first = client.call_tool("kicad_add_component", args)
        second = client.call_tool("kicad_add_component", args)

    assert "added" in first["content"][0]["text"]
    # The second call must recognise its own work rather than stacking a duplicate.
    assert "already pulls" in second["content"][0]["text"]
    resistors = [r for r in read_project(demo).components if r.startswith("R")]
    assert len(resistors) == 1


def test_read_schematic_returns_real_state(demo: Path) -> None:
    with McpStdioClient(COMMAND) as client:
        result = client.call_tool("kicad_read_schematic", {"project": str(demo)})
    payload = json.loads(result["content"][0]["text"])
    assert {"U1", "U2", "J1"} <= {c["reference"] for c in payload["components"]}
    assert "+3V3" in payload["nets"]


def test_a_bad_pin_is_a_tool_error_not_a_dead_server(demo: Path) -> None:
    with McpStdioClient(COMMAND) as client:
        with pytest.raises(RuntimeError, match="NO_SUCH_PIN"):
            client.call_tool(
                "kicad_connect_pins",
                {"project": str(demo), "from_pin": "U2.NO_SUCH_PIN", "to_pin": "U1.GPIO21", "net": "N"},
            )
        # Still alive and serving afterwards.
        assert client.list_tools()


def test_a_missing_project_is_refused(tmp_path: Path) -> None:
    with McpStdioClient(COMMAND) as client, pytest.raises(RuntimeError):
        client.call_tool("kicad_read_schematic", {"project": str(tmp_path / "nope")})


def test_only_a_resistor_can_be_placed(demo: Path) -> None:
    """Refusing beats guessing a footprint for a part no action type asks for."""
    with McpStdioClient(COMMAND) as client, pytest.raises(RuntimeError, match="Device:R"):
        client.call_tool(
            "kicad_add_component",
            {"project": str(demo), "symbol": "Device:C", "value": "100n", "connect": ["A", "B"]},
        )


# --------------------------------------------------- the whole plan over MCP


def _golden_plan() -> ActionPlan:
    return ActionPlan(
        goal="Connect U2 to U1 using I2C with 3.3V logic",
        selected_components=["U1", "U2"],
        protocol=Protocol.I2C,
        actions=[
            ConnectPins(
                id="action-1", **{"from": "U2.SDA", "to": "U1.GPIO21"}, net_name="I2C_SDA"
            ),
            ConnectPins(
                id="action-2", **{"from": "U2.SCL", "to": "U1.GPIO22"}, net_name="I2C_SCL"
            ),
            ConnectPinToNet(id="action-3", pin="U2.VCC", net="+3V3"),
            ConnectPinToNet(id="action-4", pin="U2.GND", net="GND"),
            EnsurePullup(id="action-5", net="I2C_SDA", to_net="+3V3", value="4.7k"),
            EnsurePullup(id="action-6", net="I2C_SCL", to_net="+3V3", value="4.7k"),
        ],
    )


def test_the_whole_plan_executes_over_mcp(demo: Path) -> None:
    result = MitosExecutor(COMMAND).execute(demo, _golden_plan())
    assert result.completed, result.error
    assert [step.status for step in result.steps] == ["applied"] * 6

    after = read_project(demo)
    assert after.pin_nets.get("U2.3") == "I2C_SDA"
    assert after.pin_nets.get("U1.4") == "I2C_SCL"
    assert after.pin_nets.get("U2.1") == "+3V3"
    assert after.pin_nets.get("U2.2") == "GND"
    # Two separate pull-ups: one per bus line, not one part counted twice and not
    # two stacked on the same spot.
    resistors = sorted(r for r in after.components if r.startswith("R"))
    assert len(resistors) == 2
    bridged = {
        frozenset(
            net
            for net in (after.pin_nets.get(f"{r}.{p.number}") for p in after.components[r].pins)
            if net
        )
        for r in resistors
    }
    assert bridged == {frozenset({"I2C_SDA", "+3V3"}), frozenset({"I2C_SCL", "+3V3"})}


def test_a_failed_tool_stops_the_batch_and_leaves_the_rest_undone(demo: Path) -> None:
    """Partial execution must be visible to the decision engine, not smoothed over."""
    executor = MitosExecutor(COMMAND, tool_map={"add_component": "kicad_not_a_tool"})
    result = executor.execute(demo, _golden_plan())
    assert not result.completed
    assert [step.status for step in result.steps] == ["applied"] * 4 + ["failed"]
    assert len([r for r in read_project(demo).components if r.startswith("R")]) == 0


def test_mcp_and_local_reach_the_same_project_state(tmp_path: Path) -> None:
    """Both executors share `app.kicad.edits`; this is what proves they agree."""
    from app.execution.local import LocalExecutor

    via_local = tmp_path / "local" / "esp32_i2c_demo"
    via_mcp = tmp_path / "mcp" / "esp32_i2c_demo"
    for target in (via_local, via_mcp):
        target.parent.mkdir(parents=True)
        shutil.copytree(FIXTURES / "esp32_i2c_demo", target)

    LocalExecutor().execute(via_local, _golden_plan())
    MitosExecutor(COMMAND).execute(via_mcp, _golden_plan())

    local_state, mcp_state = read_project(via_local), read_project(via_mcp)
    assert local_state.pin_nets == mcp_state.pin_nets
    assert sorted(local_state.components) == sorted(mcp_state.components)


def test_the_pipeline_accepts_and_rolls_back_over_mcp(store, requires_kicad: None) -> None:
    """The real thing: checkpoint -> MCP -> KiCAD ERC -> decision -> restore."""
    session = store.create("esp32_i2c_demo")
    plan, problems, _ = store.plan(session, ["U1", "U2"], GOLDEN_INSTRUCTION)
    assert not problems

    accepted = store.execute(session, plan, executor=MitosExecutor(COMMAND))
    assert accepted.decision.value == "accepted", accepted.reason
    assert accepted.validation.requested_connections_created == 6

    # And a mid-batch MCP failure must still restore everything.
    session2 = store.create("esp32_i2c_demo")
    plan2, _, _ = store.plan(session2, ["U1", "U2"], GOLDEN_INSTRUCTION)
    before = hash_tree(session2.project_dir)
    broken = MitosExecutor(COMMAND, tool_map={"add_component": "kicad_not_a_tool"})
    rejected = store.execute(session2, plan2, executor=broken)
    assert rejected.decision.value == "rejected_and_restored"
    assert rejected.restoration_verified is True
    assert hash_tree(session2.project_dir) == before
