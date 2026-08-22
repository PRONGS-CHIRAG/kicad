"""Build the hackathon demo artefacts: a good batch, a rolled-back batch, benchmarks.

Everything here runs the real pipeline. The unsafe demo needs a fault to reject,
and no natural instruction produces one (a wrong-voltage request is caught at
planning time, before anything executes), so it injects a deliberately broken
executor. That is labelled as an injected fault everywhere it is reported.

    python -m app.demo --out reports/demo
"""

from __future__ import annotations

import argparse
import html
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .benchmark import MisconnectingExecutor, run_all, summarize
from .execution.base import Executor
from .kicad.checkpoint import hash_tree
from .models import ActionPlan, Clarification, RunReport
from .workflow import SessionStore

DEMO_INSTRUCTION = (
    "Connect these components using I2C with 3.3 V logic.\n"
    "Add the required pull-up resistors.\n"
    "Do not modify the USB circuit."
)


@dataclass
class Demo:
    key: str
    title: str
    project: str
    instruction: str
    selected: list[str]
    narrative: str
    executor: Executor | None = None
    injected_fault: str | None = None


@dataclass
class DemoResult:
    demo: Demo
    plan: ActionPlan | None = None
    clarification: Clarification | None = None
    problems: list[str] = field(default_factory=list)
    plan_source: str = "rules"
    report: RunReport | None = None
    before_svg: str | None = None
    after_svg: str | None = None
    hashes_match: bool | None = None
    duration_seconds: float = 0.0

    def _erc(self, field: str) -> int | None:
        report = getattr(self.report.validation, field) if self.report else None
        return report.errors if report else None

    def to_dict(self) -> dict:
        return {
            "key": self.demo.key,
            "title": self.demo.title,
            "project": self.demo.project,
            "instruction": self.demo.instruction,
            "injected_fault": self.demo.injected_fault,
            "plan_source": self.plan_source,
            "plan_actions": [a.model_dump(by_alias=True) for a in self.plan.actions] if self.plan else [],
            "assumptions": self.plan.assumptions if self.plan else [],
            "decision": self.report.decision.value if self.report else None,
            "reason": self.report.reason if self.report else None,
            "checks": [c.model_dump() for c in self.report.validation.checks] if self.report else [],
            "erc_before": self._erc("erc_before"),
            "erc_after": self._erc("erc_after"),
            "restoration_verified": self.report.restoration_verified if self.report else None,
            "project_matches_checkpoint": self.hashes_match,
            "duration_seconds": round(self.duration_seconds, 3),
        }


DEMOS = [
    Demo(
        key="accepted",
        title="Demo 1 - a multi-step batch is executed and accepted",
        project="esp32_i2c_demo",
        instruction=DEMO_INSTRUCTION,
        selected=["U1", "U2"],
        narrative=(
            "One instruction becomes six reviewable actions. After execution the project is re-read "
            "from disk and ERC is re-run; the batch is accepted only because every approved "
            "connection exists, nothing else changed, and no new critical ERC violation appeared."
        ),
    ),
    Demo(
        key="rolled_back",
        title="Demo 2 - a bad execution is detected and the project is restored",
        project="esp32_i2c_demo",
        instruction=DEMO_INSTRUCTION,
        selected=["U1", "U2"],
        narrative=(
            "The same approved plan is executed by a deliberately broken executor that ties both bus "
            "signals to GND. The decision engine compares the project against the approved plan, "
            "rejects the batch and restores the checkpoint; the file hashes afterwards are identical "
            "to the ones taken before execution."
        ),
        executor=MisconnectingExecutor(),
        injected_fault=(
            "MisconnectingExecutor - a test double that rewrites every signal net to GND. "
            "This fault is injected, not one the system discovered on its own."
        ),
    ),
]


def run_demo(demo: Demo, store: SessionStore) -> DemoResult:
    result = DemoResult(demo=demo)
    started = time.perf_counter()
    session = store.create(demo.project)
    result.before_svg = store.render(session, "schematic")
    before_hashes = hash_tree(session.project_dir)

    planned, problems, source = store.plan(session, demo.selected, demo.instruction)
    result.problems, result.plan_source = problems, source
    if isinstance(planned, Clarification):
        result.clarification = planned
        result.duration_seconds = time.perf_counter() - started
        return result
    result.plan = planned
    if problems:
        result.duration_seconds = time.perf_counter() - started
        return result

    result.report = store.execute(session, planned, executor=demo.executor)
    result.after_svg = store.render(session, "schematic")
    result.hashes_match = hash_tree(session.project_dir) == before_hashes
    result.duration_seconds = time.perf_counter() - started
    return result


def _svg_block(title: str, svg: str | None) -> str:
    if not svg:
        return (
            f"<figure><figcaption>{html.escape(title)}</figcaption>"
            "<p class='missing'>kicad-cli is not available, so no render was produced.</p></figure>"
        )
    return f"<figure><figcaption>{html.escape(title)}</figcaption><div class='svg'>{svg}</div></figure>"


def render_html(results: list[DemoResult], benchmark: dict) -> str:
    sections = []
    for result in results:
        report = result.report
        checks = "".join(
            f"<li class='{'pass' if c.passed else 'fail'}'><code>{html.escape(c.name)}</code>"
            f" - {html.escape(c.detail)}</li>"
            for c in (report.validation.checks if report else [])
        )
        actions = "".join(
            f"<tr><td>{html.escape(a.id)}</td><td>{html.escape(a.type.value)}</td>"
            f"<td>{html.escape(a.purpose or '-')}</td></tr>"
            for a in (result.plan.actions if result.plan else [])
        )
        fault = (
            f"<p class='fault'><strong>Injected fault:</strong> {html.escape(result.demo.injected_fault)}</p>"
            if result.demo.injected_fault
            else ""
        )
        decision = report.decision.value if report else "not executed"
        restored = ""
        if report and report.restoration_verified is not None:
            restored = (
                f"<p>Checkpoint restored: <strong>{'verified' if report.restoration_verified else 'FAILED'}"
                f"</strong>. Project matches the pre-execution hashes: "
                f"<strong>{'yes' if result.hashes_match else 'no'}</strong>.</p>"
            )
        sections.append(f"""
<section>
  <h2>{html.escape(result.demo.title)}</h2>
  <p>{html.escape(result.demo.narrative)}</p>
  {fault}
  <pre class="instruction">{html.escape(result.demo.instruction)}</pre>
  <p>Plan source: <code>{html.escape(result.plan_source)}</code> ·
     Decision: <strong class="{decision}">{html.escape(decision.replace('_', ' '))}</strong> ·
     {result.duration_seconds:.2f}s</p>
  <p class="reason">{html.escape(report.reason if report else '')}</p>
  <table><thead><tr><th>Action</th><th>Type</th><th>Purpose</th></tr></thead><tbody>{actions}</tbody></table>
  <ul class="checks">{checks}</ul>
  {restored}
  <div class="views">
    {_svg_block('Before', result.before_svg)}
    {_svg_block('After', result.after_svg)}
  </div>
</section>""")

    rows = "".join(
        f"<tr class='{'pass' if r['passed'] else 'fail'}'><td>{html.escape(r['name'])}</td>"
        f"<td>{html.escape(r['expected'])}</td>"
        f"<td>{html.escape(str(r['decision'] or ('clarification' if r['clarification'] else '-')))}</td>"
        f"<td>{'PASS' if r['passed'] else 'FAIL'}</td></tr>"
        for r in benchmark["results"]
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>KiCAD Mitos demo</title><style>
body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 2rem auto;
        max-width: 1100px; color: #111; line-height: 1.5; }}
section {{ border: 1px solid #ddd; border-radius: 10px; padding: 1.25rem; margin-bottom: 2rem; }}
h1 {{ margin-bottom: 0.25rem; }}
pre.instruction {{ background: #f6f6f6; padding: 0.75rem; border-radius: 6px; font-size: 13px; }}
p.fault {{ background: #fff6e5; border-left: 3px solid #d99100; padding: 0.6rem 0.8rem; font-size: 14px; }}
p.reason {{ font-size: 14px; color: #444; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin: 0.75rem 0; }}
th, td {{ border: 1px solid #ddd; padding: 5px 8px; text-align: left; }}
ul.checks {{ list-style: none; padding: 0; font-size: 13px; }}
ul.checks li {{ padding: 2px 0; }}
ul.checks li.pass::before {{ content: "pass "; color: #087443; font-weight: 600; }}
ul.checks li.fail::before {{ content: "fail "; color: #b42318; font-weight: 600; }}
strong.accepted {{ color: #087443; }}
strong.rejected_and_restored {{ color: #b42318; }}
strong.needs_user_review {{ color: #b25e00; }}
.views {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }}
figure {{ margin: 0; }}
figcaption {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em;
              color: #777; margin-bottom: 0.35rem; }}
.svg {{ border: 1px solid #ddd; border-radius: 6px; background: #fff;
       overflow: auto; max-height: 26rem; }}
.svg svg {{ width: 100%; height: auto; }}
.missing {{ border: 1px dashed #ccc; border-radius: 6px; padding: 2rem;
            text-align: center; color: #888; font-size: 13px; }}
tr.pass td:nth-child(4) {{ color: #087443; font-weight: 600; }}
tr.fail td:nth-child(4) {{ color: #b42318; font-weight: 600; }}
.metrics {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
.metric {{ border: 1px solid #ddd; border-radius: 8px; padding: 0.6rem 0.9rem; font-size: 13px; }}
.metric b {{ display: block; font-size: 1.3rem; }}
</style></head><body>
<h1>KiCAD Mitos demo</h1>
<p>Generated {html.escape(benchmark["generated_at"])}. Every result below comes from running the
real pipeline against real KiCAD projects; the schematics are rendered by <code>kicad-cli</code>
from the session's working copy.</p>
{"".join(sections)}
<section>
  <h2>Demo 3 - benchmark suite</h2>
  <div class="metrics">
    <div class="metric"><b>{benchmark["passed"]}/{benchmark["total"]}</b>benchmarks passed</div>
    <div class="metric"><b>{benchmark["plan_accuracy"] * 100:.0f}%</b>plan accuracy</div>
    <div class="metric"><b>{benchmark["unsafe_change_rejection"] * 100:.0f}%</b>unsafe rejected</div>
    <div class="metric"><b>{benchmark["rollback_success"] * 100:.0f}%</b>rollbacks verified</div>
    <div class="metric"><b>{benchmark["unauthorized_changes"]}</b>unauthorized changes</div>
    <div class="metric"><b>{benchmark["duplicate_component_rate"] * 100:.0f}%</b>duplicate components</div>
    <div class="metric"><b>{benchmark["project_corruption_rate"] * 100:.0f}%</b>corrupted projects</div>
  </div>
  <table><thead><tr><th>Benchmark</th><th>Expected</th><th>Result</th><th>Status</th></tr></thead>
  <tbody>{rows}</tbody></table>
</section>
</body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("reports/demo"))
    parser.add_argument("--skip-benchmark", action="store_true")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    store = SessionStore()
    results = [run_demo(demo, store) for demo in DEMOS]
    for result in results:
        for label, svg in (("before", result.before_svg), ("after", result.after_svg)):
            if svg:
                (args.out / f"{result.demo.key}-{label}.svg").write_text(svg)
        if result.report:
            (args.out / f"{result.demo.key}-report.json").write_text(
                result.report.model_dump_json(indent=2)
            )

    benchmark = (
        {"generated_at": "", "total": 0, "passed": 0, "plan_accuracy": 0, "unsafe_change_rejection": 0,
         "rollback_success": 0, "unauthorized_changes": 0, "duplicate_component_rate": 0,
         "project_corruption_rate": 0, "results": []}
        if args.skip_benchmark
        else summarize(run_all(store))
    )

    (args.out / "demo.json").write_text(
        json.dumps({"demos": [r.to_dict() for r in results], "benchmark": benchmark}, indent=2)
    )
    (args.out / "index.html").write_text(render_html(results, benchmark))

    print(f"demo artefacts -> {args.out}")
    for result in results:
        decision = result.report.decision.value if result.report else "not executed"
        print(f"  {result.demo.key}: {decision}")
        if result.demo.injected_fault:
            print(f"    (injected fault: {result.demo.injected_fault.split(' - ')[0]})")
    if not args.skip_benchmark:
        print(f"  benchmark: {benchmark['passed']}/{benchmark['total']} passed")

    ok = (
        results[0].report is not None
        and results[0].report.decision.value == "accepted"
        and results[1].report is not None
        and results[1].report.decision.value == "rejected_and_restored"
        and results[1].hashes_match is True
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
