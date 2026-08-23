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
- The accept/reject decision is deterministic, based on the real project state after execution, and
  compares ERC/DRC violation-by-violation against a baseline rather than by count.
- Every run is transactional: the whole project is checkpointed and fully restored if any check fails.
- The UI shows the live schematic (and board, for projects with a `.kicad_pcb`) rendered by
  `kicad-cli` from the session's working copy: `GET /api/sessions/{id}/render?view=schematic|pcb`.

See [DESIGN.md](DESIGN.md) for why the decision engine works this way — baseline handling, DRC
non-determinism, and the file-diff ordering.

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

### Core

| Variable | Default | Meaning |
| --- | --- | --- |
| `KICAD_MITOS_WORKSPACE` | `/tmp/kicad-mitos` | Session copies, checkpoints and uploaded projects |
| `KICAD_MITOS_PROJECTS_DIR` | `fixtures/projects` | Read-only project source; the UI offers these *plus* `$KICAD_MITOS_WORKSPACE/projects` |
| `KICAD_MITOS_KICAD_CLI` | `kicad-cli` | KiCAD CLI binary |
| `KICAD_MITOS_EXECUTOR` | `local` | `local` or `mitos` |
| `KICAD_MITOS_MITOS_COMMAND` | – | Command that starts the Mitos MCP server (stdio) |
| `KICAD_MITOS_CORS_ORIGINS` | `["http://localhost:3000"]` | JSON array of allowed browser origins |
| `OPENAI_API_KEY` | – | Enables LLM planning; falls back to the rule planner on any failure |

### Devin agent (optional — see below)

| Variable | Default | Meaning |
| --- | --- | --- |
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
to a Devin session instead, so planning continues on its own. The agent only answers one missing
detail from the real pin table and datasheet; it never writes an action and has no say in
accept/reject. Every answer becomes a visible assumption on the plan (`source: agent+rules`), and
three refusal-type clarifications — `incompatible_voltage`, `incompatible_logic_voltage`,
`unsupported_protocol` — plus the three "which components" questions are never auto-resolved.
If the session times out, errors, or leads to a plan the validator rejects, it falls back to asking
you. See [DESIGN.md](DESIGN.md) for the reasoning behind that split and a real example of the
validator catching a bad answer.

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

The benchmark covers ten scenarios: standard connection, existing pull-ups, missing pin names, wrong
voltage, protected USB circuit, pre-existing ERC warnings, a deliberately wrong connection, partial
execution failure, unknown component and an ambiguous instruction. `app.demo` builds three artefacts
from those runs: an accepted batch, a batch that is rejected and restored, and the benchmark table.

## Mitos integration

`backend/app/execution/mitos.py` speaks MCP over stdio and forwards only validated actions, and
`backend/app/mcp_server.py` is a real MCP server implementing the four tools it calls:

```bash
KICAD_MITOS_EXECUTOR=mitos KICAD_MITOS_MITOS_COMMAND="python3 -m app.mcp_server" \
  python -m app.benchmark --out reports        # 10/10, every edit over JSON-RPC
```

Both executors call the same edit primitives (`app/kicad/edits.py`), so the local and MCP paths
cannot drift. The third-party Mitos server itself is unconfirmed — nothing here speaks for it, and
`DEFAULT_TOOL_MAP` is configurable so real tool names can be dropped in when it exists. See
`docs/mitos.md`, generated from a live `tools/list`, and [DESIGN.md](DESIGN.md) for what that
does and doesn't prove.

## Ten-agent engineering team

An optional multi-agent pipeline (project manager, requirements, architecture, component, schematic,
PCB layout, simulation, verification, manufacturing, QA/release) that runs the same checkpointed,
deterministic gates as the rest of the tool — agents propose, they never write files or open PRs
directly. See [DESIGN.md](DESIGN.md) for the gate logic, error-routing rules, and current MVP limits.

```bash
python -m app.team.cli --project fixtures/projects/esp32_i2c_demo \
  "Create and validate a small ESP32 temperature-monitoring PCB with an I²C sensor and USB-C power."
```

API: `POST /api/team/runs`, with status/report/evidence/answer at
`/api/team/runs/{run_id}[/report|/evidence|/answer]`. Evidence (stage outputs, gate results, events,
checkpoints, artifacts) is append-only under `settings.workspace_dir/team/<run_id>/`.
