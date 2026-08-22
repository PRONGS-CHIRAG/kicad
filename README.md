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

`backend/app/execution/mitos.py` speaks MCP over stdio and forwards only validated actions. The tool
names in `DEFAULT_TOOL_MAP` are placeholders: no Mitos MCP server was reachable in this environment,
so the mapping has not been verified against a live server. See `docs/mitos.md`.

Point the probe at a running server and it writes the capability matrix from what the server actually
advertises, including each tool's input schema:

```bash
python -m app.probe_mitos --command "<command that starts the Mitos MCP server>"
```

It exits non-zero and writes nothing when no server answers, so an unverified matrix can never
masquerade as a verified one. The adapter itself is covered by tests that run it against
`backend/tests/mitos_stub.py`, a stdio MCP test double — those prove the client, the action-to-tool
mapping and the probe work, and prove nothing about the real Mitos server.
