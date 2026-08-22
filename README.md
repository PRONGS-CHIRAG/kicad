# KiCAD Mitos

Natural-language batch editing for KiCAD. You select components, describe the change in plain
language, review a structured action plan, approve it, and the change is executed, verified with
KiCAD's own ERC and automatically rolled back if it fails verification.

The MVP supports **I2C** connections only.

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
- Every run is transactional: the whole project is checkpointed and fully restored if any check fails.

## Layout

| Path | Purpose |
| --- | --- |
| `backend/app/kicad` | S-expression parsing, schematic reader/writer, ERC/DRC, checkpoints, state diff |
| `backend/app/planning` | Instruction parsing, deterministic planner, rule validator, optional LLM planner |
| `backend/app/execution` | Local executor and the Mitos MCP executor adapter |
| `backend/app/decision.py` | Deterministic accept/reject logic |
| `backend/app/workflow.py` | Session store and the checkpoint/execute/validate/restore pipeline |
| `backend/app/benchmark.py` | The 10 benchmark scenarios, JSON + HTML report |
| `frontend` | Next.js UI: project -> selection + instruction -> plan preview -> validation report |
| `fixtures/projects` | Generated KiCAD projects used by tests and benchmarks |

## Running

```bash
# backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload --port 8000

# frontend
cd frontend && npm install && npm run dev   # http://localhost:3000
```

Requires `kicad-cli` (KiCAD 8) on `PATH` for ERC/DRC. Without it the pipeline still runs, but the
decision engine refuses to accept a change it could not verify.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `KICAD_MITOS_WORKSPACE` | `/tmp/kicad-mitos` | Session copies and checkpoints |
| `KICAD_MITOS_PROJECTS_DIR` | `fixtures/projects` | Projects offered by the UI |
| `KICAD_MITOS_KICAD_CLI` | `kicad-cli` | KiCAD CLI binary |
| `KICAD_MITOS_EXECUTOR` | `local` | `local` or `mitos` |
| `KICAD_MITOS_MITOS_COMMAND` | – | Command that starts the Mitos MCP server (stdio) |
| `OPENAI_API_KEY` | – | Enables LLM planning; falls back to the rule planner on any failure |

## Tests and benchmarks

```bash
cd backend
.venv/bin/pytest -q
.venv/bin/python -m app.benchmark --out reports   # reports/benchmark.{json,html}
```

The benchmark covers the ten scenarios from the product plan: standard connection, existing
pull-ups, missing pin names, wrong voltage, protected USB circuit, pre-existing ERC warnings,
a deliberately wrong connection, partial execution failure, unknown component and an ambiguous
instruction.

## Mitos integration

`backend/app/execution/mitos.py` speaks MCP over stdio and forwards only validated actions. The tool
names in `DEFAULT_TOOL_MAP` are placeholders: no Mitos MCP server was reachable in this environment,
so the mapping has not been verified against a live server. See `docs/mitos.md`.
