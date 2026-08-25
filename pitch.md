# KiCAD Mitos — pitch deck content

Twelve slides. Each one is written as **headline** (the claim, not a topic), 3–5 bullets, and one
*say this out loud* line. Every number traces to a file in this repo — sources are named in
[Number provenance](#number-provenance) at the bottom. Nothing here is estimated.

The four judging criteria map to slides like this:

| Criterion | Slide |
| --- | --- |
| Problem | 2 |
| Approach (and how it changed) | 3 |
| Solution | 4 |
| Autonomy | 5 |
| Verification | 6, 7 |
| Artifacts | 8 |
| Clarity | 1, and the repeated one-liner on 5–6 |
| Tech stack + Devin | 10 |

---

## Slide 1 — Title

# KiCAD Mitos
### Natural-language PCB edits that verify themselves — and undo themselves when they're wrong.

- One line, and it is the whole architecture: **the machine that writes the change is never the
  machine that judges it.**
- Two lanes on one project: **one verified edit**, and a **ten-agent engineering team** that
  composes those edits into a board.
- Live: Next.js on Vercel, FastAPI + real `kicad-cli` on a Fly.io machine.

> *Say out loud:* "Everything after this slide is one idea applied twice — the writer and the judge
> are different machines."

---

## Slide 2 — Problem

# Hardware has ground truth. Nobody has automated against it.

- **Industry:** PCB / electronics design. **Who's stuck:** the engineer making the hundredth wiring
  change — hand-drawing wires and labels pin by pin, then hand-reviewing what they just did.
- The cost of a miss isn't a failing test, it's a **fab spin**: weeks and real money before you
  find out.
- KiCAD will tell you the *state* of your board. It will not tell you **what your last edit did to
  it**, and it will not undo that edit when the answer is "made it worse."
- **Why this domain has a real feedback loop:** KiCAD ships its own electrical and design rule
  checkers (`kicad-cli sch erc`, `pcb drc`). Every proposed change has a machine-checkable verdict.
  That is the loop — we did not have to invent a judge, we had to *wire one in and refuse to
  overrule it*.
- Judging by rule-checker output alone isn't enough either: our demo project is **already broken in
  ten ways** before anyone touches it. You need the *diff*, not the count.

> *Say out loud:* "This industry is one of the few where an agent's work can be graded by a machine
> the industry already trusts. That's why we picked it."

---

## Slide 3 — Approach, and how it changed

# We went in thinking the hard part was making the model good. It was making the judge independent.

The arc, straight off the commit log:

- **Started:** one protocol (I2C), schematic only, LLM proposes → we execute. *(`59c3969`)*
- **Protocols became data, not code.** A protocol is a table of per-signal pin aliases named
  separately for controller and peripheral — so UART's TX/RX crossover falls out with **no special
  case**. Five patterns, three action types. *(`31dc7be`)*
- **Found a hole:** `kicad-cli` has no equivalent of Pcbnew's *Update PCB from Schematic*. We wrote
  board sync ourselves — so the board is actually written, and **DRC becomes a real gate, not
  decoration**. *(`51e5bed`)*
- **Found a harder hole:** KiCAD's own DRC is **non-deterministic** — on a byte-identical board it
  intermittently reports one or two fewer clearance violations. Whichever run became the baseline
  decided whether valid work looked like a regression. It rejected good changes ~1 in 20. Fix: the
  baseline is the **union of multiple passes**. *(`58b9902`)*
- **Devin flipped roles:** from a thing that *asks you* a question to a thing that *answers* one —
  but only from the planner's own option list, and never with a vote on accept/reject. *(`1633d70`,
  `8caca8e`)*
- **Then we scaled the primitive:** one verified edit → a **ten-agent team** where every stage has
  to clear the same kind of deterministic gate. *(`8e822fe`)*

> *Say out loud:* "Every one of those turns came from the tooling being worse than we assumed —
> and each time the answer was to give the judge more teeth, not the model more freedom."

---

## Slide 4 — Solution

# One pipeline. The model is inside it, not on top of it.

```
read project → plan (rules or LLM) → clarify if ambiguous → preview → checkpoint
             → execute → re-read + ERC/DRC → deterministic decision → accept | restore
```

- **Rules plan first, every time.** The LLM is an optional refinement on top. If the model's plan
  fails validation on a single point, it is **discarded** and the rule plan is used.
- **The model can only emit the plan schema** — a Pydantic discriminated union of three action
  types. There is no free-text path from a model into your project.
- **It declines rather than guesses.** Ten distinct clarification paths exist exactly where a
  generic assistant would invent a pin number or assume a voltage.
- **Lane 2 — the ten-agent team:** PM → Requirements → Architecture → Components → Schematic →
  (PCB Layout ∥ Simulation) → Verification → Manufacturing → QA/Release. Each stage is gated;
  a failed gate routes back to the responsible agent and replays the downstream remainder.

> *Say out loud:* "Same skeleton twice. One edit, or ten agents — the gate is the load-bearing part."

---

## Slide 5 — Autonomy

# Trigger to artifact, nobody in the middle.

- `POST /api/team/runs` with one sentence — *"Monitor temperature over I2C on a USB-C powered ESP32
  board, 3.3 V logic, two layers"* — and ten agents run to a release verdict with **no human input**.
- Each agent gets only the prior-stage outputs it declares it reads (`AgentSpec.reads`), plus parsed
  schematic and board context. No shared scratchpad, no prompt soup.
- **Layout and simulation run as siblings.** Failed gates route back automatically and replay the
  remainder; **two return trips is the cap**, and then it escalates — it does not spin.
- **Ambiguity is resolved without you too:** `KICAD_MITOS_AUTO_RESOLVE=true` sends a resolvable
  question to a Devin session instead of stopping to ask. ~20–40 s, 0 ACU.
- The human appears at exactly one place: `needs_human_review`. That's a **designed** exit, not a
  fallback — and it's answerable from the UI, which appends the answer and re-runs.

> *Say out loud:* "The only thing a human does in the team lane is answer a question the system
> decided it was not allowed to answer for itself."

---

## Slide 6 — Verification

# The system knows a good result from a bad one because it never grades its own work.

- **The trust anchor is `kicad-cli` itself.** Electrical correctness is never re-derived in Python.
  We shell out to the same binary KiCAD ships and take its JSON as truth.
- **Violation-by-violation baseline diff — never counting.** New / resolved / unchanged. So
  pre-existing violations are never blamed on this change, and a violation *swapped* for a different
  one is still caught. Counting misses both.
- **Nine named checks in `decision.py`, zero LLM involvement:** only expected files changed,
  execution completed, project still parses, requested connections created, supporting components
  present, protected objects preserved, no unauthorized changes, no new critical ERC, no new
  critical DRC.
- **Rollback is verified, not assumed.** `Checkpoint.restore()` returns `True` only when the
  restored tree **re-hashes identically** (SHA-256, every project file).
- **A stage that cannot be grounded fails.** The team orchestrator's default branch is literally
  *"stage output cannot be grounded by available tools"* → **fail**. We do not bless what we cannot
  check.
- **No `kicad-cli` on the box?** Every run returns `needs_user_review` and the UI says so in a
  banner rather than looking healthy. The Docker build **fails** rather than starting degraded.

> *Say out loud:* "Same line as slide one: the machine that writes the change is never the machine
> that judges it. This slide is that sentence in code."

---

## Slide 7 — Proof: it caught our own agent, twice, on live sessions

# Two real saves. Neither was staged.

**Save #1 — the planner caught Devin.** Running against the live v3 API, the Devin agent resolved an
ambiguous pin by picking one the fixture ties to **GND** — which would have grounded the I2C clock.
The validator refused it (*"refusing to pull up power net GND"*), the run degraded to asking the
human, and nothing was written.

**Save #2 — KiCAD caught the schematic agent.** Live ten-agent run `515dc87b825e`, request:
*"Monitor temperature over I2C on a USB-C powered ESP32 board."* It cleared **7 gated stages**, then
the schematic agent's proposal produced a **new ERC error — "Pins of type Power output and Power
output are connected."** The gate restored the checkpoint, routed the work back to the schematic
agent, it failed again, hit the **two-return-trip cap**, and the run ended
`needs_human_review` with **24 open critical findings enumerated**.

- Nothing bad was written to disk in either case.
- Read the findings and the causal chain is visible: one of them is *"unknown board footprint
  reference(s): R1, R2"* — the layout stage couldn't place resistors the rejected schematic never
  put on the board. That's **one root cause with a downstream consequence**, not two independent
  defects, and the evidence file shows which is which.
- The failure is **itemized and attributable**, not "the agent said it went fine."

> *Say out loud:* "Nothing here was staged — the schematic agent introduced a real ERC error, KiCAD
> caught it, and the run refused to ship."

---

## Slide 8 — Artifacts

# Real KiCAD files, and the paper trail an engineer would need to sign off.

- **The design files themselves** — `.kicad_sch` and `.kicad_pcb`, written in place and openable in
  KiCAD. Board sync appends nets, rebinds every pad to the net its schematic pin now carries, and
  synthesises footprints for symbols that have none.
- **`RunReport`** — the plan, every executed step, all nine checks with detail, the ERC violation
  diff, the state diff, files changed, and whether restoration was hash-verified.
- **Append-only team evidence** under `team/<run_id>/`: per-stage output JSON, per-stage gate JSON,
  `events.jsonl`, `evidence.jsonl`, checkpoints, and `final-report.json`. The UI is drawn *from*
  this file — what's on screen is what the run recorded.
- **`ReleaseRecord`** — release status, project version, included files, `release_hash`, open
  critical findings, checklist. **`ManufacturingReport`** — DFM status against a named manufacturer
  profile, with findings and `fabrication_ready`.
- **`benchmark.html`** and **`demo/index.html`** — ten scenarios in a table, and before/after SVG
  renders of both an accepted run and a rolled-back one.
- **Live rendering** from the working copy on disk, with export timestamps stripped so a render is a
  pure function of the file and before/after images actually compare.

> *Say out loud:* "An electrical engineer could open every one of these in the tool they already use."

---

## Slide 9 — Numbers

# 10/10 scenarios, 235 passing tests, and one number that isn't 1.0 on purpose.

From `reports/benchmark.json`, ten scenarios from the product plan:

| Metric | Value |
| --- | --- |
| Scenarios passed | **10 / 10** |
| Plan accuracy | **1.0** |
| Unsafe-change rejection | **1.0** |
| Rollback success | **1.0** |
| Unauthorized changes | **0** |
| Duplicate components / project corruption | **0 / 0.0** |
| Median run | **1.12 s** |
| Execution success | **0.833 — one scenario is a deliberate partial-execution failure** |

- That 0.833 is scenario 8 injecting a mid-batch executor failure to prove the rollback path. It is
  *supposed* to be below 1.0; if it were 1.0 the test wasn't testing anything.
- **235 tests, all passing** on the current working tree (`pytest -q`, 85 s) — parser, planner,
  validator, decision engine, board sync, MCP, Devin client and the whole team stack.
- **10/10 also passed over MCP** — `KICAD_MITOS_EXECUTOR=mitos`, every edit over JSON-RPC, same
  result. Measured at commit `75e0138`; the benchmark table above is the local-executor run.
- The demo project's honest starting point, re-measured on the demo machine (KiCAD 10.0.5, three
  identical passes): **ERC 10 errors / 8 warnings, DRC 5 errors / 13 warnings, before we touch it.**
- **If a judge reads the PM output in run `515dc87b825e`, it says "6 DRC errors / 14 warnings."**
  That's the drift — KiCAD version and DRC's own nondeterminism — that made us union multiple passes
  into one baseline (`58b9902`). Own it on the spot; it's the bug we already fixed, not a
  contradiction.

> *Say out loud:* "We open the demo by naming our own baseline. That's what makes the numbers after
> it worth anything."

---

## Slide 10 — Tech stack & Devin

# Boring stack, deliberately. The interesting part is where Devin sits.

**Stack**
- **Backend:** Python 3.10+, FastAPI + uvicorn, Pydantic v2 on every wire schema, httpx, pytest +
  ruff. **No ORM, no queue, no ERC library** — a hand-written S-expression parser plus subprocess
  calls to `kicad-cli`.
- **Frontend:** Next.js 14 App Router, React 18, TypeScript, Tailwind. **No component library, no
  state library** — flow state is `useState` and the current stage is *derived*, never stored.
- **Integrations:** MCP over stdio (real server + real client, both sharing the same edit
  primitives); an OpenAI-compatible endpoint at temperature 0, `json_object`, for optional planning.
- **Deploy:** Fly.io, **one stateful machine, never auto-stopped** (runs live in RAM), 4 GB, a real
  volume for workspace; frontend on Vercel; deploy is a manual GitHub Action, not on push, because a
  redeploy would kill in-flight runs.

**Devin, used two ways**
- **As build labor:** parts of this repo were written by Devin — the main branch and PR #2 are
  `devin/…` branches (`devin/1787413084-kicad-mitos-mvp`, `devin/1787443288-ten-agent-team`).
- **As runtime agents:** one Devin session per agent invocation, each constrained to that agent's
  **JSON schema**. Invalid structured output is retried **with the validation errors fed back**,
  then falls back to a deterministic fallback function. ACU ceiling per session. The client reads
  `structured_output` the moment it appears — while the session is still `running` — so an answer
  lands in ~20–40 s instead of waiting for exit.
- **Three things are never auto-resolved**, because they're refusals not ambiguities:
  `incompatible_voltage`, `incompatible_logic_voltage`, `unsupported_protocol`. Asking for 5 V must
  never quietly become 3.3 V.
- **Keyless runs use an explicitly labelled `STUB` runner** — deterministic, offline, never contacts
  Devin. It says `STUB` on screen. No demo of ours has ever been able to pretend a stub was live.

> *Say out loud:* "Devin proposes, in a schema, with a fallback. It never gets a vote on whether the
> result ships."

---

## Slide 11 — What we did not build

# Saying this first is worth more than you finding it.

- **No routed copper and no ngspice.** Simulation is closed-form arithmetic; layout checks use
  footprint extents only where the parser exposes trustworthy geometry.
- **No Gerber/BOM export yet.** The artifacts are KiCAD-native plus the evidence trail.
- **No PCB-editing action type** — `PCB_ACTION_TYPES` is still empty. The board is written by the
  *sync*, which is why DRC is a genuine gate anyway.
- **The third-party Mitos server is unconfirmed.** Our MCP path is real and passes 10/10 end to end
  against our own server; we've never reached theirs, so the tool map stays configurable and the docs
  record which server the capability matrix came from.
- **Sessions are in-memory.** A backend restart drops them. No database.
- **`ready for engineering review` is not a guarantee that a board works.** KiCAD checks cannot
  replace prototyping and lab validation. We say that in the product, not just in the deck.

> *Say out loud:* "None of these are surprises to us, and none of them are hidden in the UI either."

---

## Slide 12 — Close

# KiCAD tells you the state of your board. We tell you what your last edit did to it — and take it back when the answer is "made it worse."

- **Autonomy:** one sentence → ten agents → a release verdict, nobody in the middle.
- **Verification:** KiCAD's own checkers, baseline-diffed, nine deterministic checks, hash-verified
  rollback, and a gate that fails when it can't ground the claim.
- **Artifacts:** files an engineer opens in the tool they already have, plus the trail that says why
  they should believe them.

> *Say out loud:* "The machine that writes the change is never the machine that judges it."

---

## Demo logistics (not a slide — read before you present)

- **Demo on `:3001`, not `:3000`.** The Docker stack on `:3000` was built before board sync and runs
  KiCAD 9: the board half of the demo silently does nothing there, with no error to explain why.
- **Restart both dev servers first**, even if they look fine — a long-running server serves the code
  it was started with.
- Confirm `curl http://127.0.0.1:8001/api/health` shows `kicad_cli` version and
  `erc_supported: true`. The UI top bar shows the same thing, so the judges can see it too.
- Have `reports/benchmark.html` and `reports/demo/index.html` open in tabs.
- Full six-minute, five-beat runbook is in [DEMO.md](DEMO.md) — this deck does not duplicate it.

## Anticipated questions

- **"Did a full ten-agent run ever reach release on live Devin?"** Not in the runs we have on disk —
  the furthest live run cleared 7 gated stages and correctly escalated to `needs_human_review`
  (slide 7). The complete canonical path to a `ReleaseRecord` is exercised end to end on the labelled
  `STUB` runner and covered by the orchestrator test suite. We'd rather say that than dress up a
  screenshot.
- **"Isn't the approve button a hole in your autonomy?"** It's a product choice on the *single-edit*
  lane — an engineer editing their own board wants the preview. The team lane has no approve step.
- **"Are those 235 tests actually passing right now?"** Yes — `pytest -q` on the working tree,
  235 passed in 85 s, run while writing this deck.
- **"How do you know rollback worked?"** `restore()` compares SHA-256 hashes of the whole restored
  tree against the original and returns `False` if they differ. The verdict panel reports it.
- **"What if the LLM is just better than your rules?"** Then it wins — but only after
  `validate_plan` agrees. One problem and its plan is thrown away, and the rule plan is always
  computed as the standing answer.

## Number provenance

| Claim | Source |
| --- | --- |
| 10/10, 1.0 plan accuracy, 0.833 execution success, 1.12 s median | `reports/benchmark.json`, generated 2026-08-22 |
| 235 passing tests | `pytest -q` on the working tree — 235 passed in 84.68 s |
| ERC 10/8, DRC 5/13 baseline on `esp32_i2c_demo` | re-measured via `app.kicad.erc.KicadCli` against KiCAD 10.0.5, three identical passes; agrees with [DEMO.md](DEMO.md) |
| The 6/14 DRC figure in the live run's PM output | `/tmp/kicad-mitos/team/515dc87b825e/project_manager.json` — different environment, cited as drift |
| 7 gated stages, 24 critical findings, power-output ERC error | `/tmp/kicad-mitos/team/515dc87b825e/final-report.json` + `events.jsonl` |
| GND-pin save by the validator | [README.md](README.md), measured against the live v3 API |
| ~20–40 s / 0 ACU Devin answer | [README.md](README.md), measured against the live v3 API |
| DRC nondeterminism rejecting ~1 run in 20 | [README.md](README.md), commit `58b9902` |
| Devin-authored branches | `git log` — `devin/1787413084-kicad-mitos-mvp`, PR #2 `devin/1787443288-ten-agent-team` |
