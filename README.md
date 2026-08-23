# KiCAD Mitos

Natural-language batch editing for KiCAD. You select components, describe the change in plain
language, review a structured action plan, approve it, and the change is executed, verified with
KiCAD's own ERC and automatically rolled back if it fails verification.

Supported connection patterns: **I2C**, **SPI**, **UART**, a single **GPIO** link and a
**power-only** hookup. A protocol is a data table of per-signal pin aliases, named separately
for the controller and the peripheral — which is how UART's TX/RX crossover falls out with no
special case. All of them decompose into the same three action types.

## How it works

```
read project -> plan (rules or LLM) -> preview + approve -> checkpoint
             -> execute -> re-read + ERC -> deterministic decision -> accept | restore
```

- The LLM (optional) only *proposes* structured actions. It never decides whether a change is accepted.
- The accept/reject decision is deterministic and based on the real project: expected connections,
  supporting components, protected objects, unauthorized changes and new critical ERC violations.
- ERC is compared violation-by-violation against a baseline, not by counting, so pre-existing
  violations are never blamed on the change and a "swapped" violation is still detected.
- DRC runs too when the project has a board, including KiCAD's schematic-parity check, and it is a
  real gate for any run that wrote the board — which a schematic edit does, because the board is
  synced to match. The one carve-out is unconnected items whose every net that sync touched: a synced
  board is unrouted by design, so those are pending work, not a regression. An unconnected item on any
  other net still rejects, so severed copper cannot slip through.
- The DRC baseline is the union of a couple of passes, because KiCAD's own DRC is not deterministic:
  on a byte-identical board it intermittently reports one or two fewer clearance violations. Whichever
  run became the baseline would otherwise decide whether a later, normal run looked like it introduced
  errors, which rejected valid work about one time in twenty.
- The file-level diff is snapshotted straight after execution, before ERC runs, because `kicad-cli`
  rewrites project files as a side effect. Only the schematic may change; a touched `.kicad_pro` or
  symbol library is an unauthorized change.
- Every run is transactional: the whole project is checkpointed and fully restored if any check fails.
- The UI shows the live schematic (and the board, for projects with a `.kicad_pcb`) rendered by
  `kicad-cli` from the session's working copy: `GET /api/sessions/{id}/render?view=schematic|pcb`.
  It renders what is on disk, so a proposed plan changes nothing until you approve it, and a rejected
  run is rolled back before the next render.

## Quick start with Docker (recommended)

`kicad-cli` is the source of truth for ERC/DRC, and the image ships it, so this is the only setup in
which the pipeline can actually accept a change.

```bash
docker compose up --build     # backend :8000, frontend :3000
```

The backend image build **fails** rather than starting degraded if `kicad-cli`, `sch erc` or `pcb drc`
are missing. It is based on Ubuntu 26.04, which packages KiCAD 9 for both amd64 and arm64; note that
`sch erc` / `pcb drc` only exist from KiCAD 8 onward (Ubuntu 24.04 ships KiCAD 7, whose CLI has export
subcommands only) and the KiCAD PPAs publish amd64 only.

## Layout

| Path | Purpose |
| --- | --- |
| `backend/app/kicad` | S-expression parsing, schematic reader/writer, ERC/DRC, checkpoints, state diff |
| `backend/app/planning` | Instruction parsing, deterministic planner, rule validator, optional LLM planner |
| `backend/app/execution` | Local executor and the Mitos MCP executor adapter |
| `backend/app/decision.py` | Deterministic accept/reject logic |
| `backend/app/workflow.py` | Session store and the checkpoint/execute/validate/restore pipeline |
| `backend/app/benchmark.py` | The 10 benchmark scenarios, JSON + HTML report |
| `backend/app/demo.py` | Demo artefacts: accepted run, rolled-back run, benchmark summary |
| `backend/app/probe_mitos.py` | Writes the Mitos capability matrix from a live MCP server |
| `frontend` | Next.js UI: project -> selection + instruction/form -> plan preview -> validation report |
| `fixtures/projects` | Generated KiCAD projects used by tests and benchmarks |

## Running without Docker

```bash
# backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload --port 8000

# frontend
cd frontend && npm install && npm run dev   # http://localhost:3000
```

Requires `kicad-cli` from KiCAD 8 or later on `PATH` for ERC/DRC. Without it the pipeline still runs,
but the decision engine refuses to accept a change it could not verify — every run comes back as
`needs_user_review`. The UI says so in a banner rather than looking healthy.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `KICAD_MITOS_WORKSPACE` | `/tmp/kicad-mitos` | Session copies, checkpoints and uploaded projects |
| `KICAD_MITOS_PROJECTS_DIR` | `fixtures/projects` | Read-only project source; the UI offers these *plus* `$KICAD_MITOS_WORKSPACE/projects` |
| `KICAD_MITOS_KICAD_CLI` | `kicad-cli` | KiCAD CLI binary |
| `KICAD_MITOS_EXECUTOR` | `local` | `local` or `mitos` |
| `KICAD_MITOS_MITOS_COMMAND` | – | Command that starts the Mitos MCP server (stdio) |
| `KICAD_MITOS_CORS_ORIGINS` | `["http://localhost:3000"]` | JSON array of allowed browser origins |
| `OPENAI_API_KEY` | – | Enables LLM planning; falls back to the rule planner on any failure |
| `KICAD_MITOS_DEVIN_API_KEY` | – | Devin service-user key (`cog_…`); `DEVIN_API_KEY` also works |
| `KICAD_MITOS_AUTO_RESOLVE` | `false` | Let the Devin agent answer an ambiguity instead of asking you |
| `KICAD_MITOS_DEVIN_BASE_URL` | `https://api.devin.ai/v3` | Enterprise tenant override. `cog_` keys work only on v3 |
| `KICAD_MITOS_DEVIN_ORG_ID` | auto | Discovered from `GET /v3/self`; set only to pin one org |
| `KICAD_MITOS_DEVIN_MODE` | `normal` | `normal｜fast｜lite｜ultra｜fusion`; `fast` is ~2× quicker at ~4× ACU |
| `KICAD_MITOS_DEVIN_TIMEOUT_SECONDS` | `180` | Ceiling before falling back to asking |
| `KICAD_MITOS_DEVIN_POLL_SECONDS` | `5` | Poll interval while the session runs |
| `KICAD_MITOS_DEVIN_MAX_ACU` | `5` | ACU ceiling per session |

Copy `backend/.env.example` to `backend/.env` (git-ignored) and paste the key there, or export it in
the shell.

## The Devin agent: thinking instead of asking

By default the planner refuses to guess — no SDA pin named on the symbol means a question, not an
assumption. Set a Devin key **and** `KICAD_MITOS_AUTO_RESOLVE=true` and a *resolvable* ambiguity goes
to a Devin session instead, so planning continues on its own. The result is a mixture, and the split
is the point:

| | does |
| --- | --- |
| Devin agent | answers one missing detail, from the real pin table and the part's datasheet |
| Deterministic rules | build the plan, validate it, run ERC/DRC, decide, roll back |

The agent never writes an action, never sees the validator, and has no say in accept/reject. Every
answer it gives must be one of the options the planner itself offered — anything else is discarded —
and each one is recorded as a visible assumption on the plan, so the preview says *"3:P3 chosen … by
the Devin agent: the datasheet says pin 3 is SDA"* rather than presenting it as fact. `source` on the
plan response reads `agent+rules` when the agent contributed.

**Three clarifications are never auto-resolved**, because they are refusals rather than ambiguities:
`incompatible_voltage`, `incompatible_logic_voltage` and `unsupported_protocol`. Asking for 5 V must
not quietly become a 3.3 V bus (plan §21: better to reject an uncertain instruction than silently
produce a wrong connection). Neither are the three that pick *which* components get connected. The
resolvable set is an allowlist, so a reason code added later is unresolvable until someone says
otherwise.

If the session times out, errors, answers something that was not on the list, **or leads to a plan
the validator rejects**, the question comes back to you — the agent degrades to the old behaviour
rather than to a dead screen. That last case is not hypothetical: running this against the live API,
the agent picked a pin that the fixture ties to GND, which would have grounded the I2C clock. The
validator caught it (*"refusing to pull up power net GND"*), and the run fell back to asking. The
agent is fallible; the rules are the backstop, which is the whole point of the split.

Measured against the live v3 API: an answer arrives in roughly 20–40 s for 0 ACU, and it arrives
while the session is still `running` — the client reads `structured_output` the moment it appears
instead of waiting for the session to exit.

**This sends your schematic to a third party.** The prompt carries the selected components, their pin
tables and the project's net names to `api.devin.ai` so the agent can reason about the real design.
That data flow does not exist unless you set the key and the opt-in.

## Projects

The UI lists the bundled fixtures plus any project uploaded through it. Uploads are zip archives with
the `.kicad_sch` at the archive root; they are extracted into the server workspace
(`$KICAD_MITOS_WORKSPACE/projects`), never into the git-tracked fixtures, and the extractor rejects
absolute paths, `..` traversal, oversized archives and anything that is not a KiCAD project file.

## Planning: form or prose

Both entry points produce the same schema and run through the same validator:

- **Fields** — pick interface, logic voltage, SDA/SCL pins and pull-up value. Never calls the LLM.
- **Words** — the written instruction is parsed into the same fields.

## Tests and benchmarks

```bash
# with the real kicad-cli, via the image
docker compose run --rm --no-deps backend pytest -q
docker compose run --rm --no-deps backend python -m app.benchmark --out /workspace/reports
docker compose run --rm --no-deps backend python -m app.demo --out /workspace/reports/demo

# or locally
cd backend
.venv/bin/pytest -q
.venv/bin/python -m app.benchmark --out reports   # reports/benchmark.{json,html}
.venv/bin/python -m app.demo --out reports/demo   # index.html with before/after renders
```

`app.demo` builds the three demo artefacts from §28 of the plan: an accepted batch, a batch that is
rejected and restored, and the benchmark table. The rejected run needs a fault to reject and no
instruction produces one organically (a wrong-voltage request is caught at planning time, before
anything executes), so it injects a deliberately broken executor — labelled as an injected fault in
both the JSON and the HTML.

The benchmark covers the ten scenarios from the product plan: standard connection, existing
pull-ups, missing pin names, wrong voltage, protected USB circuit, pre-existing ERC warnings,
a deliberately wrong connection, partial execution failure, unknown component and an ambiguous
instruction.

## Mitos integration

`backend/app/execution/mitos.py` speaks MCP over stdio and forwards only validated actions, and
`backend/app/mcp_server.py` is a real MCP server implementing the four tools it calls. So the MCP
path is not decorative — it runs the whole pipeline for real:

```bash
KICAD_MITOS_EXECUTOR=mitos KICAD_MITOS_MITOS_COMMAND="python3 -m app.mcp_server" \
  python -m app.benchmark --out reports        # 10/10, every edit over JSON-RPC
```

Both executors call the same edit primitives (`app/kicad/edits.py`), so the local and MCP paths
cannot drift: driving the golden plan through either leaves byte-identical pin-to-net state, and a
tool failure mid-batch still rolls the whole project back, hash-verified.

What this does *not* claim: the third-party Mitos server is still unconfirmed — nothing reachable
here speaks for it, and searching turns up no public Mitos. `DEFAULT_TOOL_MAP` is configurable
precisely so its real names can be dropped in. See `docs/mitos.md`, which is generated from a live
`tools/list` and says which server it was generated from.

## Ten-agent engineering team

The team coordinates a project manager, requirements, architecture, component, schematic, PCB
layout, simulation, verification, manufacturing, and QA/release agent. The canonical workflow runs
layout and simulation as siblings, then verifies, checks manufacturing, and prepares release.
Failed gates route to the responsible stage and replay the downstream remainder; two return trips end
in human review.

Agents receive parsed schematic and board context. Devin runs use validated structured output and
one session per invocation. Keyless runs use the explicitly labelled `STUB` runner, which is
deterministic and never contacts Devin. Agents do not edit files or open pull requests; schematic
and placement proposals are applied only through checkpointed, deterministic gates.

The background API provides `POST /api/team/runs` and status, report, evidence, and answer endpoints
at `/api/team/runs/{run_id}`, `/report`, `/evidence`, and `/answer`. The equivalent CLI is:

```text
python -m app.team.cli --project fixtures/projects/esp32_i2c_demo \
  "Create and validate a small ESP32 temperature-monitoring PCB with an I²C sensor and USB-C power."
```

Evidence is append-only under `settings.workspace_dir/team/<run_id>/`, including stage outputs,
gate results, events, checkpoints, and artifacts. The current MVP does not route copper or run
ngspice; simulation is closed-form arithmetic, and footprint extents are checked only where the
parser exposes them. `ready for engineering review` is not a guarantee that a board works: KiCAD
checks cannot replace physical prototyping and lab validation.

Point the probe at a running server and it writes the capability matrix from what the server actually
advertises, including each tool's input schema:

```bash
python -m app.probe_mitos --command "<command that starts the Mitos MCP server>"
```

It exits non-zero and writes nothing when no server answers, so an unverified matrix can never
masquerade as a verified one. The adapter itself is covered by tests that run it against
`backend/tests/mitos_stub.py`, a stdio MCP test double — those prove the client, the action-to-tool
mapping and the probe work, and prove nothing about the real Mitos server.
