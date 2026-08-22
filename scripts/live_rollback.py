#!/usr/bin/env python3
"""Show the rollback live: an executor wires the I2C bus to GND instead of what
was approved, and the decision engine catches it and restores the project.

The planner will not produce a bad plan on purpose, and a synced board cannot
fail DRC on a valid plan either, so there is no way to see a rejection through
the UI from an honest instruction. This swaps in `MisconnectingExecutor` — the
same injected fault the benchmark uses for scenario 7, modeling the real,
disclosed risk that the Mitos MCP executor's tool mapping is unverified (see
docs/mitos.md) — while leaving every other stage untouched, so what you are
watching is the real decision engine and the real checkpoint restore.

    python3 scripts/live_rollback.py [project]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.benchmark import MisconnectingExecutor  # noqa: E402
from app.kicad.checkpoint import hash_tree  # noqa: E402
from app.workflow import store  # noqa: E402

INSTRUCTION = "Connect these using I2C with 3.3 V logic. Add the required pull-up resistors."


def main() -> int:
    project = sys.argv[1] if len(sys.argv) > 1 else "esp32_i2c_demo"
    session = store.create(project)
    references = [ref for ref, c in session.state.components.items() if not c.is_power][:2]
    plan, problems, source = store.plan(session, references, INSTRUCTION)
    if problems or not hasattr(plan, "actions"):
        print(f"planning did not produce a runnable plan: {problems or plan}")
        return 1

    print(f"project      {project}")
    print(f"plan         {len(plan.actions)} actions from the {source} planner, {len(problems)} problems")
    print("executor     MisconnectingExecutor (injected fault: applies different wiring than approved,")
    print("             modeling an unverified Mitos MCP tool mapping — ties the bus to GND)\n")

    before = hash_tree(session.project_dir)
    report = store.execute(session, plan, executor=MisconnectingExecutor())

    print(f"DECISION     {report.decision.value}")
    print(f"restore      hash-verified: {report.restoration_verified}")
    print("\nrefused because:")
    for check in report.validation.checks:
        if not check.passed:
            print(f"  FAIL  {check.name}: {check.detail}")
    identical = hash_tree(session.project_dir) == before
    print(f"\nproject on disk byte-identical to before the run: {identical}")
    return 0 if report.restoration_verified and identical else 1


if __name__ == "__main__":
    raise SystemExit(main())
