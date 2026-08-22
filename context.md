# KiCAD Mitos — context

## Overview

KiCAD Mitos is a natural-language batch editor for KiCAD schematics whose central claim is a
separation of powers: **the language model only ever proposes, KiCAD itself verifies, and a
deterministic rule engine decides.** You open a project, select components, describe the change in
prose or fill in a short form, review a typed action plan, approve it — and the edit is applied to a
checkpointed working copy, re-read from disk, checked with `kicad-cli`'s own ERC, and either accepted
or rolled back to the byte.

```
read project → plan (rules or LLM) → clarify if ambiguous → preview + approve → checkpoint
             → execute → re-read + ERC/DRC → deterministic decision → accept | restore
```

The supported patterns are I2C, SPI, UART, a single GPIO link and a power-only hookup, built on
exactly three action types —
`connect_pins`, `connect_pin_to_net`, `ensure_pullup` (see [models.py](backend/app/models.py)).
Everything else in the pipeline — checkpointing, ERC diffing, the decision engine, rollback — is
protocol-agnostic and is the part that carries the design weight.

For setup, configuration and commands, see the [README](README.md); this document is the conceptual
map.

## Features implemented

**Reading and understanding a project.** A hand-written S-expression parser
([sexpr.py](backend/app/kicad/sexpr.py)) feeds a reader ([reader.py](backend/app/kicad/reader.py))
that resolves symbols, pins, pin geometry and label positions into a normalized `ProjectState` —
components, pins, and a `pin_nets` map computed from label positions rather than guessed from names.
That normalized state is what makes before/after comparison possible.

**Two ways to describe a change, one schema.**
- *Words* — free prose, parsed deterministically by
  [instruction.py](backend/app/planning/instruction.py): protocol, logic voltage, pull-up value,
  SDA/SCL pin assignments in either word order, mentioned references, and protection clauses
  ("do not modify the USB circuit" → protected objects `USB_D+`, `USB_D-`, `VBUS`, plus any
  reference named in that sentence).
- *Fields* — a seven-field form (interface, logic voltage, controller SDA/SCL, peripheral SDA/SCL,
  pull-up value) in [composer.tsx](frontend/src/components/composer.tsx). This path never calls the
  LLM at all.

Both produce the same `ActionPlan` and both go through the same validator.

**A deterministic planner that refuses to guess.** [generator.py](backend/app/planning/generator.py)
returns a `Clarification` instead of a plan wherever the schematic is genuinely ambiguous: unknown
component, fewer than two selected, protocol unspecified, unsupported protocol, ambiguous
controller/peripheral roles, logic voltage unspecified with no inferable rail, a requested voltage
the project has no rail for, a controller supply that would need level shifting, a peripheral symbol
that doesn't name its SDA/SCL pins, and an unresolvable controller pin. Each clarification carries an
`answer_key` naming which `PlanAnswers` field a chosen option fills, so answering is a click and
planning resumes with that answer folded in.

**Optional LLM planning, structurally subordinate.** [llm.py](backend/app/planning/llm.py) sends the
project state and instruction to an OpenAI-compatible chat endpoint constrained to `json_object` at
temperature 0. The model may only emit the same Pydantic schema. Its output then runs through
`validate_plan`, and **if validation finds a single problem the model's plan is discarded** and the
deterministic plan is used instead. The rule planner is computed *first*, every time, as the standing
answer — the LLM is an optional refinement on top, not the primary path.

**A validator that runs before anything touches KiCAD.**
[validator.py](backend/app/planning/validator.py) enforces electrical sanity on the plan itself:
no pin connected to itself, no missing components or pins, no pull-up on a power net, no pull-up to
ground, no ground pin tied to a power rail (or vice versa), no output pin shared on an I2C bus,
no protected reference/pin/net touched, no SDA/SCL net collision, no signal net colliding with a
power net.

**Transactional execution.** [checkpoint.py](backend/app/kicad/checkpoint.py) snapshots every
project file (`.kicad_pro`, `.kicad_sch`, `.kicad_pcb`, `.kicad_sym`, `.kicad_prl`, `.pretty/`) with
SHA-256 hashes, and `restore()` returns `True` only when the restored tree hashes identically to the
original — restoration is *verified*, not assumed. The `LocalExecutor`
([local.py](backend/app/execution/local.py)) makes connections by placing global labels on pin
connection points, which is what KiCAD's own netlister resolves into nets, and adds `Device:R`
symbols for pull-ups.

**Verification against KiCAD, not a reimplementation.** [erc.py](backend/app/kicad/erc.py) shells out
to `kicad-cli sch erc` / `pcb drc` and normalizes the JSON. Reports are compared
**violation-by-violation against the session baseline** — new / resolved / unchanged — so pre-existing
violations are never blamed on the change, and a violation that was *swapped* for a different one is
still caught. Counting would miss both. The file-level diff is snapshotted immediately after
execution, before ERC runs, because `kicad-cli` rewrites project files as a side effect; `.kicad_prl`
is ignored entirely since it holds only UI state. Violation identity is keyed on the nets involved
for unconnected-items violations, because KiCAD names an arbitrary representative pair from the
cluster and does not pick the same one twice — without that, a DRC gate would reject valid changes at
random ([models.py](backend/app/models.py)).

**Board sync.** `kicad-cli` has no equivalent of Pcbnew's *Update PCB from Schematic*, so
[board.py](backend/app/kicad/board.py) implements it against the same S-expression reader/writer the
schematic edits use: append nets the schematic has and the board lacks (keeping existing net numbers,
since tracks reference nets by index), rebind every pad to the net its schematic pin now carries, and
synthesise a footprint for any symbol that has none. It deliberately does not route — a synced board
is unrouted by design, exactly as Pcbnew leaves it. The sync runs inside the checkpoint envelope, so a
bad board write is reverted with everything else, and what it changed is reported alongside the
executor's own steps rather than left implicit.

**The decision engine.** [decision.py](backend/app/decision.py) — no model involvement anywhere in
it — runs nine named checks:

| Check | What it asserts |
| --- | --- |
| `only_expected_files_changed` | Only the schematic changed (plus the board, for a plan that edits it) |
| `execution_completed` | Every action applied, no partial run |
| `project_readable` | The schematic still parses after the edit |
| `requested_connections_created` | Every approved pin→net assignment actually exists |
| `supporting_components_present` | Each planned pull-up is a real resistor tying net to rail |
| `protected_objects_preserved` | Nothing named as protected moved |
| `no_unauthorized_changes` | No extra pin joined/moved/left a net, no component added or removed, no value changed |
| `no_new_critical_erc_violations` | Baseline-diffed ERC errors |
| `no_new_critical_drc_violations` | Baseline-diffed DRC, a real gate for any run that wrote the board |

Any failure ⇒ `rejected_and_restored`. All pass but ERC couldn't run ⇒ `needs_user_review`. All pass
with conclusive ERC ⇒ `accepted`.

**Live rendering.** `GET /api/sessions/{id}/render?view=schematic|pcb` renders the session's working
copy with `kicad-cli`. It renders what is *on disk*, so a proposed plan changes nothing on screen and
a rejected run is rolled back before the next render. Export timestamps are stripped so a render is a
pure function of the file and before/after images compare meaningfully.

**Project upload.** Zipped projects are extracted into the server workspace — never into the
git-tracked fixtures — by [upload.py](backend/app/kicad/upload.py), which rejects absolute paths,
`..` traversal, oversized archives (>50 MB) and non-KiCAD files.

**Benchmark and demo.** [benchmark.py](backend/app/benchmark.py) runs ten scenarios from the product
plan — standard connection, existing pull-ups reused, missing pin names, wrong voltage, protected USB
circuit, pre-existing ERC warnings not blamed, a deliberately wrong connection, partial execution
failure, unknown component, ambiguous instruction — and emits JSON + HTML.
[demo.py](backend/app/demo.py) builds three artefacts: an accepted batch, a rejected-and-restored
batch, and the benchmark table.

**Mitos MCP adapter.** [mitos.py](backend/app/execution/mitos.py) speaks MCP over stdio behind the
same `Executor` interface as the local executor, so validation, decision and rollback are identical
whichever runs. [probe_mitos.py](backend/app/probe_mitos.py) regenerates the capability matrix from a
live server's advertised tools and input schemas.

### Not built, and current limits

Stated plainly, because several of these are easy to mistake for finished features:

- **The MCP path is real, but the third-party Mitos server is still unconfirmed.**
  [mcp_server.py](backend/app/mcp_server.py) is a genuine stdio MCP server implementing the four
  tools the adapter calls, against real files and sharing
  [edits.py](backend/app/kicad/edits.py) with the local executor. `KICAD_MITOS_EXECUTOR=mitos` runs
  the full pipeline — checkpoint, execute over JSON-RPC, KiCAD ERC, decision, rollback — and the
  benchmark passes 10/10 through it. What remains unconfirmed is *Mitos itself*: no such server was
  ever reachable and no public one appears to exist, so `DEFAULT_TOOL_MAP` stays configurable and
  [docs/mitos.md](docs/mitos.md) records which server it was generated from.
- **Reading the user's live KiCAD selection is not implemented.** It needs a Mitos tool that may not
  exist; a speculative endpoint against a guessed name would be indistinguishable from a working
  feature until someone tried it. Typed references and the selection UI are both built.
- **No action type edits the PCB directly** — `PCB_ACTION_TYPES` is still an empty frozenset. The
  board is nonetheless written, by the sync, so a run that changed the board counts as touching it and
  DRC becomes a genuine gate: clearance and crossing regressions on the layout now reject. The one
  carve-out is unconnected items whose *every* named net was touched by this sync — that is the
  unrouted-by-design state, not a regression — and it is reported in the check detail rather than
  hidden. An unconnected item naming any other net still rejects, so severed copper cannot slip
  through.
- **Without `kicad-cli` the pipeline still plans and applies, but nothing can be called safe** — every
  run returns `needs_user_review`, and the UI says so in a banner rather than looking healthy.
- **No protocol beyond the five.** A sixth needs a `ProtocolSpec` entry, not new plumbing; anything
  the parser cannot place is declined explicitly rather than mis-planned.
- **Sessions are in-memory** (`SessionStore._sessions`); only the project working copies and
  checkpoints are on disk. A backend restart drops session state. There is no database.
- **The demo's rejected run injects a deliberately broken executor**, labelled as such in both the
  JSON and the HTML — no honest instruction produces a rejection organically, because a wrong-voltage
  request is caught at planning time, before anything executes.

## Tech stack

**Backend** — Python ≥3.10, FastAPI + uvicorn, Pydantic v2 for every schema on the wire,
pydantic-settings for configuration, httpx for the LLM call, python-multipart for uploads.
pytest + ruff (E, F, I, UP, B, SIM) for tests and lint. No ORM, no message queue, no ERC library:
the KiCAD layer is hand-written S-expression parsing plus subprocess calls to `kicad-cli`.

**The trust anchor is `kicad-cli` itself.** Electrical correctness is never re-derived in Python — it
is asked of the same binary KiCAD ships, and its JSON output is the source of truth.

**Frontend** — Next.js 14.2.35 (App Router), React 18, TypeScript 5, Tailwind 3.4. No component
library, no state management library: flow state is plain `useState` in
[page.tsx](frontend/src/app/page.tsx) and the current stage is *derived*, never stored.

**Integrations** — MCP over stdio for the Mitos executor; an OpenAI-compatible chat-completions
endpoint (`gpt-4o-mini`, temperature 0, `response_format: json_object`) for optional planning.

**Deployment** — Docker Compose, backend on 8000 and frontend on 3000. The backend image is based on
Ubuntu 26.04, which packages KiCAD 9 for both amd64 and arm64, and **the build fails rather than
starting degraded** if `kicad-cli`, `sch erc` or `pcb drc` are missing. (`sch erc` / `pcb drc` only
exist from KiCAD 8 onward; Ubuntu 24.04 ships KiCAD 7, whose CLI has export subcommands only.) The
CLI wrapper detects capabilities at runtime rather than assuming a version, because the usage-string
and timestamp formats both changed between KiCAD 9 and 10.

## User flow

Four stages, derived in [page.tsx](frontend/src/app/page.tsx) from what exists rather than tracked as
a state machine: `report ? "report" : plan ? "preview" : session ? "select" : "project"`.

**1. Project.** Pick a bundled fixture or upload a zipped project. `POST /api/sessions` copies it into
a private working directory and records a baseline ERC (and DRC, if there's a board) — everything
later is measured against that baseline, not against zero.

**2. Describe.** The schematic renders on the left; components are selectable. Choose *Words* or
*Fields*, then plan. `POST /api/sessions/{id}/plan` returns either an `ActionPlan` with any blocking
problems, or a `Clarification`.

**3. Clarify, if needed.** A clarification is one question with clickable options. The chosen option
is written into the `PlanAnswers` field named by `answer_key` and planning re-runs immediately with it.
Any hand edit to the selection, the instruction or the mode **clears the accumulated answers** —
otherwise stale answers leak into the next plan and it stops matching what's on screen. Answering also
switches planning to the rule path, so an answered question is honoured literally rather than
re-interpreted.

**4. Review.** The plan preview shows the goal, every action with its purpose, the assumptions the
planner made explicit ("GPIO21 used for SDA (default ESP32 I2C pin)", "I2C_SDA already has pull-up R4
(4.7k) to +3V3; none added"), warnings, protected objects, and whether the plan came from rules or the
LLM. Nothing has been executed at this point — the render still shows the unmodified project.

**5. Verdict.** Approving calls `POST /api/sessions/{id}/execute`, which checkpoints, executes,
re-reads, runs ERC/DRC and decides. The verdict panel shows the decision, the reason, every check with
its detail, the ERC violation diff, the state diff and the list of files changed. On rejection it also
reports whether restoration was hash-verified. The render is keyed to the session `revision`, so it
refreshes to the accepted state — or back to the original one.

## How it differs from KiCAD

KiCAD is not the competitor here; it's the verifier. The value is the layer above it.

| | KiCAD | KiCAD Mitos |
| --- | --- | --- |
| Making a connection | Draw wires and labels by hand, per pin | Describe the intent once; the plan is derived from the actual schematic |
| Ambiguity | You resolve it as you draw | Surfaced as an explicit question with options, before anything is applied |
| ERC | Gives you a violation list for the current state | Diffs that list against a pre-edit baseline and attributes new violations to *this* change |
| Pre-existing problems | Indistinguishable from new ones in the list | Explicitly separated: new / resolved / unchanged |
| Undo | Editor-level undo, in the GUI, if you notice in time | Whole-project checkpoint with hash-verified restore, applied automatically on any failed check |
| Scope enforcement | None — nothing stops an edit from touching the USB circuit | Protected objects are enforced at plan time *and* re-checked against the post-edit state diff |
| Accountability | Your memory of what you changed | A `RunReport`: plan, every executed step, nine checks, ERC diff, state diff, files changed |

KiCAD tells you the state of the board. It does not tell you *what your last edit did to it*, and it
will not undo that edit for you when the answer is "made it worse". That attribution and that rollback
are the whole product.

## How it differs from a generic LLM

A generic assistant asked to wire up an I2C bus produces prose, or edits a file and tells you it
worked. The differences here are structural, not tonal — each one is enforced in code:

- **The model cannot emit anything but the plan schema.** Output is parsed into the same Pydantic
  `ActionPlan` the deterministic planner produces, with a discriminated union of three allowed action
  types. There is no free-text path from the model into the project.
- **The model's plan is checked and can be thrown away.** `validate_plan` runs on it; one problem and
  the deterministic plan is used instead ([llm.py](backend/app/planning/llm.py)). The rule plan is
  always computed, so there is always something correct to fall back to.
- **The model never touches the decision.** [decision.py](backend/app/decision.py) has no LLM
  involvement at all. Acceptance depends on real re-read state, real `kicad-cli` output, and the
  baseline diff — not on anyone's assessment of whether the edit looks right.
- **It declines rather than guesses.** Ten distinct clarification paths exist precisely where a
  generic model would invent a pin number or assume a voltage.
- **"Don't touch X" is enforced twice.** Protected objects block the plan at validation *and* are
  re-checked against the post-execution state diff, so a change that slips past the plan is still
  caught.
- **Failure is undone, not narrated.** A rejected run is restored from a hash-verified checkpoint. The
  report says what was attempted and why it was refused, and the project on disk is unchanged.
- **It works with no model at all.** Set no API key, or use the Fields form, and the entire pipeline
  runs deterministically end to end. The LLM is a convenience for parsing intent — never a dependency
  for correctness.
