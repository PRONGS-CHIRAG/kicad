# Demo runbook

Two lanes on one project, in two browser tabs. Every number below was measured on this machine
on 2026-08-23 against the running backend, not estimated. Where a number could not be verified,
it says so.

**The ten-agent lane takes 19-27 minutes. You start it before the room fills, not on stage.**

---

## Before the room fills

### 1. Demo on http://localhost:3001. Not :3000.

Both are running. `:3000` is the Docker stack: it runs **KiCAD 9.0.8**, predates board sync, and
**has no ten-agent lane at all**. `:3001` runs current code against KiCAD 10.0.5 and talks to the
backend on `127.0.0.1:8001`.

### 2. Start the servers — and leave `--reload` off

```bash
# backend. NO --reload: team runs live in a module-level dict in
# backend/app/api/team.py, and a reload mid-run makes the UI poll 404.
cd backend && python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8001

# frontend, second terminal
cd frontend && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8001 npx next dev --port 3001
```

CORS for `:3001` is already in `backend/.env`, so no extra env var is needed.

Restart both even if they look fine. A long-running server serves the code it was started with.

### 3. Confirm you are live

```bash
curl -sS http://127.0.0.1:8001/api/health
```

Must read exactly this shape:

```json
{"status":"ok","kicad_cli":"10.0.5","erc_supported":true,"drc_supported":true,
 "executor":"local","llm_enabled":false,"team_runner":"devin","team_parallel":true}
```

`kicad_cli` must show a version and `erc_supported` must be `true`, or every run comes back
"needs your review" and the demo has no punchline. The top bar in the UI shows the same thing, so
the judges can see it too:

> **KICAD-CLI 10.0.5 · ERC ready · DRC ready · EXECUTOR local · PLANNER rules only**

### 4. T-40 min: open **two** tabs and start the team run in Tab A

Two tabs, because `teamRunId` is React state with no reattach path — anything that resets the page
loses a run you cannot get back.

**Tab A — the ten-agent team. Do this first, then do not touch it.**

1. Click **esp32_i2c_demo** in the project list.
2. Click **TEN-AGENT TEAM** in the Lane toggle (top right of the right-hand column).
3. Under *Who runs the agents*, click **Devin**. **It defaults to Stub — you must change it.**
   Confirm the copper notice now reads *"This spends real Devin sessions."*
4. Leave the request box on its default:
   ```
   Monitor temperature over I2C on a USB-C powered ESP32 board.
   Keep the logic at 3.3 V and stay within a two-layer board.
   ```
5. Click **Start the 10-agent run**.
6. Walk away. Switch to Tab B.

Measured on two full runs in `normal` mode, both **0 ACU**:

| Run | Wall clock | Stages reached | Release |
| --- | --- | --- | --- |
| 08:35 | 1112 s — 18 min 32 s | 8 of 10 | `needs_human_review` |
| 11:1x | 1604 s — 26 min 44 s | 6 of 10 | `needs_human_review` |

Both were measured before the verification gate began failing on unresolved critical findings and
before a stuck stage could ask a question. The wall-clock figures still stand; the release column is
what may differ now — a run that used to end at `needs_human_review` can instead **stop part-way and
wait for you**, which is the `awaiting_human` state described under Step 03.

Devin is not deterministic and the spread is 8 minutes wide on a sample of two. **Start it 40
minutes out.** That is not padding: 40 minutes is what buys you one full restart if the five-minute
checkpoint below comes back dead.

#### T-35: the five-minute checkpoint. Do not skip this.

Devin runs stall. Of six full runs logged on 2026-08-23, **four died at the architecture gate after
three stages**; only the last two went deep. You need to know which one you got while you still have
time to restart.

Five minutes after you press Start, look at Tab A:

- **Healthy:** the rail has moved past **System Architect** into **Component Engineer** or beyond.
- **Dead:** **Step 03 Release status** has already appeared, with three stages done and
  *Needs human review*. That run is over. Close the tab, open a fresh one, and start again — from
  T-35 a second attempt still lands with ~8 minutes to spare. This is the whole reason you started
  at T-40 instead of T-30.

**A red architecture gate is not a stall.** This is the healthy trajectory, timed on this backend
build on 2026-08-23:

```
wall clock   stage                    gate
   ~30s      Project Manager          —
  ~150s      Requirements             passed
  ~200s      System Architect         FAILED
  ~240s      Design Repair            repair-architecture PASSED
  ~470s      Component Engineer       FAILED
  ~520s      Design Repair            repair-components
```

So at the five-minute mark you should be looking at a **red System Architect row, a Design Repair
row below it, and Component Engineer running**. That is the machine working exactly as pitched — the
gate rejected the architect, the repair agent fixed it, the gate accepted the fix, and the run
carried on. Don't read the red as a failure, and don't restart on account of it. The only thing that
means "dead" is **Step 03 having appeared**.

If the second attempt also stalls at three stages, don't gamble a third time. Present Beat 6 off the
stalled run: the gate rejecting the architect twice and refusing to continue is still the point, and
"the gates stopped it at stage three" is a true sentence you can say with a straight face.

**Tab B — the single-change lane.** Click **esp32_i2c_demo**. Leave it on **ONE CHANGE**. This is
its own backend session; nothing you do here reaches Tab A.

### 5. Have these open too

`reports/benchmark.html` and `reports/demo/index.html`.

---

## Do not press these

| Control | Why |
| --- | --- |
| **Change project** (bottom right of the sheet) in Tab A | Calls `closeProject`, which wipes the team run |
| Browser refresh / reopen on Tab A | Same — the run id is React state only |
| **Stub** in the runner toggle | The on-screen notice calls it *"a wiring test, not a demo"*, in front of the judges |
| **Answer and re-run** in the release panel *of a finished run* | It restarts the **whole run from the project manager** — another 19-27 minutes |
| **Send to the repair engineer** on a run that stopped mid-flight | Safe, and much cheaper: it resumes the paused stage instead of restarting. Different box, different button — read which one you are looking at |
| A second team run in Tab A | `startTeamRun` calls `resetTeam()` first; the finished one is gone |

Also: don't demo the same single-change session twice. After an accepted run the baseline moves, so
the second run has nothing left to resolve and the 10 → 1 headline disappears. Use **Plan another
change**, or reopen the project.

---

## The line to open with

> "KiCAD tells you the state of your board. It won't tell you what your last edit did to it, and it
> won't undo that edit when the answer is 'made it worse.' That's the gap we built into."

Do not pitch this as electrical expertise. The whole design is that it *defers* every electrical
judgment to KiCAD's own rule checker. Pitch the rigor: **the machine that writes the change is never
the machine that judges it.**

---

# Lane 1 — one verified change (Tab B, ~4 min)

## Beat 1 — Admit the mess first (30s)

Point at the card under the schematic before touching anything:

> **PROJECT** esp32_i2c_demo · **REVISION** 0 · **BASELINE ERC** 10 err · 8 warn ·
> **BASELINE DRC** 5 err · 13 warn

> "This project is already broken in ten ways. Watch what we don't get blamed for."

This is the beat everyone skips and it is the one that buys you the rest. Naming your own baseline is
what makes the later numbers credible.

## Beat 2 — The plan, before anything is written (90s)

Tick **U1** and **U2** in the component list. (**J1** is the third; leave it.) Leave the default
instruction in the box:

```
Connect these components using I2C with 3.3 V logic.
Add the required pull-up resistors.
Do not modify the USB circuit.
```

Click **Plan the change**. You get exactly six actions, each drawn as a little wire diagram:

| # | Action |
| --- | --- |
| 1 | `U2.SDA ↔ U1.GPIO21` as **I2C_SDA** |
| 2 | `U2.SCL ↔ U1.GPIO22` as **I2C_SCL** |
| 3 | `U2.VCC` → **+3V3** |
| 4 | `U2.GND` → **GND** |
| 5 | 4.7k pull-up, **I2C_SDA** → **+3V3** |
| 6 | 4.7k pull-up, **I2C_SCL** → **+3V3** |

Read the **assumptions** out loud — all four are on screen:

> "U1 is the I2C controller, U2 is the peripheral, GPIO21 for SDA, GPIO22 for SCL — the ESP32
> defaults. It says so, so you can catch it if it's wrong. And 'do not modify the USB circuit' became
> three protected nets it is now forbidden to touch: **USB_D+, USB_D-, VBUS**."

Point at the footer: *"A checkpoint is taken first. If ERC comes back worse, the change is reverted."*
Then at the schematic: *"6 proposed actions are not on the sheet yet."* **Nothing has happened.**

## Beat 3 — Approve (60s)

Click **Approve and apply**. It lands in **0.6 s**:

| What lands | Number |
| --- | --- |
| Verdict | **Change applied** |
| Checks | **9 / 9 passed** |
| ERC errors | **10 → 1** (warnings 8 → 10) |
| DRC errors | 5 → 13 — *see the question below, you have the answer* |
| Files touched | `esp32_i2c_demo.kicad_sch` and `esp32_i2c_demo.kicad_pcb`, nothing else |
| Board | 2 nets added, 6 pads rebound, 2 footprints placed |

The panel is **Step 04 — "What KiCAD says about it"**, with a `0.6 s` badge. The nine checks, in the
order they appear on screen:

```
only expected files changed      2 expected file(s) changed
execution completed              6 action(s) executed
project readable                 schematic parsed after execution
requested connections created    6/6 created
supporting components present    all required pull-ups present
protected objects preserved      no protected object changed
no unauthorized changes          only approved changes were applied
no new critical erc violations   no new ERC errors
no new critical drc violations   no new DRC errors; 4 new non-critical …
                                 I2C_SCL, I2C_SDA synced to the board and still need routing
```

That last detail line is the answer to the hardest question you will get. It is already on screen —
read it, don't paraphrase it.

> "Nine independent checks, none of them run by a language model. Every requested connection exists,
> nothing unapproved moved, the protected nets are untouched, and KiCAD reports no new errors. It also
> synced the board, because we implemented KiCAD's 'Update PCB from Schematic' ourselves — `kicad-cli`
> doesn't ship it."

Read the line under the before/after table verbatim: *"Violations that were already there are never
blamed on this change."* That sentence is the product.

The **RULE CHECKS, BEFORE AND AFTER** table is the one to point at. It reads exactly:

```
        BEFORE            AFTER              NEW
ERC     10 err ·  8 warn  1 err · 10 warn    0 errors
DRC      5 err · 13 warn  13 err · 11 warn   6 violations
```

**Know this before someone spots it:** ERC warnings went **up**, 8 → 10, while the NEW column says
*0 errors*. Both are true, and the two new warnings are identical and harmless:

> `lib_symbol_issues` — *"The current configuration does not include the symbol library 'Device'"* —
> one for **Symbol R1 [R]**, one for **Symbol R2 [R]**.

That is KiCAD noting that the two pull-ups it just added come from a symbol library this environment
hasn't been pointed at. It's a library-path warning about the new parts, not an electrical finding,
and the gate is on errors. Say exactly that if asked; don't get caught mid-sentence.

Also worth pointing at, under the schematic: *"Footprints and nets are on the board, but the new
connections aren't routed yet. Open the project in KiCAD to lay the copper."* The app volunteers its
own limitation before a judge has to find it.

## Beat 4 — Make it say no (45s)

Click **Plan another change**, keep U1 and U2 ticked, and type:

```
Connect these using I2C with 5V logic
```

It refuses, with zero actions planned:

> **"The project has no 5V rail, so U2 cannot be powered at 5V. Use 3.3V instead?"**

and a single clickable option, **3.3V**, which re-plans instantly.

> "It read the actual schematic, found no 5 V rail, and asked instead of guessing. A chatbot would
> have written you a 5 V bus."

Only if you have spare time, and only in place of something else — **these two were not re-verified
on 2026-08-23, unlike everything else on this sheet**: `esp32_spi_display` with
`Connect these over SPI at 3.3V` (four signals, power, ground, and *no* pull-ups, because SPI doesn't
want them), or `esp32_uart_module` with `Connect these over UART at 3.3V` (crosses the pair — the
controller's TX to the module's RX, on two distinct nets).

## Beat 5 — Break it on purpose (45s)

Terminal:

```bash
python3 scripts/live_rollback.py
```

```
project      esp32_i2c_demo
plan         6 actions from the rules planner, 0 problems
executor     MisconnectingExecutor (injected fault: ties the bus to GND)

DECISION     rejected_and_restored
restore      hash-verified: True

refused because:
  FAIL  requested_connections_created: 2/6 created; missing U1.3, U1.4, U2.3, U2.4
  FAIL  no_unauthorized_changes: U1.3 joined GND; U1.4 joined GND; U2.3 joined GND; U2.4 joined GND

project on disk byte-identical to before the run: True
```

Say the honest part out loud, because a judge will ask:

> "We had to *inject* a saboteur to show you this. No honest instruction produces a rejection — bad
> requests get caught at planning time, before anything is written. So we swapped in an executor that
> deliberately wires the bus to ground. It got caught, and the project came back byte-identical."

---

# Lane 2 — the ten-agent team (Tab A, ~2 min)

Switch to Tab A. The run you started 30 minutes ago is finished, or nearly.

## Beat 6 — Ten agents, ten gates, and a refusal to sign off (2 min)

**Say the transition first:**

> "Same idea, scaled. One verified edit becomes a team: ten agents in a fixed order, and every stage's
> output is checked by a deterministic tool before the next one starts."

**Show, in this order:**

1. **The header of Step 02** — *"The run, stage by stage"*, with **N/10 gates passed** and
   **N return trips** as chips. The return-trip count is the point: work went *back*.
2. **The rail itself.** Eleven rows: Project Manager, Requirements, System Architect, Component
   Engineer, Schematic Design, PCB Layout, Simulation, Verification, Manufacturing, QA and Release,
   and **Design Repair** at the bottom — *"called in when a gate rejected a stage."* Note the bracket
   over rows 6 and 7: **CONCURRENT — ONE STEP, TWO AGENTS**. Point at the `writes files` chips on
   Schematic Design and PCB Layout — *"only those two touch the project."*
3. **Click System Architect.** Pick it deliberately over a clean stage: it carries an `attempt N`
   chip, a failed gate, and a Design Repair hand-off, so its panel shows the whole loop rather than
   a green tick. (Component Engineer usually repairs too, if you'd rather show that one.) The panel
   that opens is the proof:
   - **OUTPUT MODEL / GROUNDED BY / DESIGN FILES / PASSES** — the schema it was forced into, the
     tool that judged it, and whether it could write.
   - **GIVEN TO IT** — *"the registry declares each agent's inputs, so a later stage cannot quietly
     read something it was not given."*
   - **THE SESSION** — runner, status, ACU, duration, and **a clickable link to the real Devin
     session**, of the form `https://app.devin.ai/sessions/…`. Open it in a new tab. This is your
     strongest "it actually ran" evidence; nothing else you show beats a live third-party session log.
     (Have that tab ready — don't log into Devin for the first time on stage.)
   - **THE GATE** — the deterministic verdict, with each error's FOUND / EXPECTED / OBJECT / SOURCE.
4. **Scroll to Step 03.** Check which of the two panels you have before you point at anything.

   If the run **stopped to ask you something**, the panel is headed *"<stage> needs a decision"*
   rather than *Release status*: a gate rejected that stage, the repair engineer tried and the same
   gate rejected its work too, so the run is holding for an instruction. That is a better story than
   the verdict, not a worse one — the pipeline is refusing to walk past a document it cannot
   justify, and it says exactly which rule is unsatisfied. Answer it and the paused stage picks up;
   don't answer it and the run finishes on its own as it always did.

   Otherwise the panel is **Release status**. Point at the **Needs human review** notice and the
   six-cell grid — *Requirements met · Power tests · DFM warnings · Schematic ERC · Board DRC ·
   Critical issues*. **Don't scroll through OPEN CRITICAL FINDINGS.** Observed counts across runs
   were 1, 24 and 33 — you won't know in advance which you got, and scrolling a 33-row wall in front
   of a judge reads as failure rather than as rigour. Take the grid, then jump to the bottom. Then
   say the honest sentence, and only this one:

> "Ten agents got N stages deep, and the gates would not let it release. That is the system
> working. The same rule as the first lane — the thing that writes is never the thing that judges —
> and here the judge said no."

**Read N off the rail on the day — don't memorise it.** Observed depths on this project were 6 and 8
of 10 stages across two full runs. Saying "eight" when the screen shows six is the one unforced
error available to you in this beat.

5. **Scroll to Evidence — "What happened, in order"** and the **ROUTING DECISIONS** prose at the
   bottom. It is written in plain English: *"System Architect failed its gate, so the work went back
   to Requirements — and every stage after it is replayed."*

**Never say** the team produces a board, a finished design, or a manufacturable package. The deepest
run observed reached **8 of 10 stages** (never Manufacturing or QA/Release), with **0/16 requirements
satisfied**, ERC failed, DRC failed, 33 open critical findings, and `needs_human_review`. If a judge
asks whether it has ever produced a clean release: **no, and say so.** The lane's claim is the gating,
not the output.

**If it is still running at Beat 6:** don't apologise, narrate it. The rail updates every second —
show the gates passing live, the `stage_repaired` and `route_failure` entries appearing in the trace,
and click into a finished stage for the Devin session link. Then say: *"It runs about nineteen minutes
end to end; you're watching minute fourteen."*

**If it failed with a backend error:** skip the lane. Say the primitive is lane 1 and the team is the
same primitive ten times over, and go straight to the scoreboard. Do not start a fresh run on stage.

---

## Beat 7 — The scoreboard (30s)

```bash
open reports/benchmark.html      # 10/10 scenarios pass
open reports/demo/index.html     # before/after renders, accepted + rolled back
```

Ten scenarios from the product spec: standard connection, pull-ups reused, missing pin names, wrong
voltage, protected circuit, pre-existing warnings not blamed, a deliberately wrong connection, a
partial execution failure, unknown component, ambiguous instruction. Regenerate any time:

```bash
cd backend && python3 -m app.benchmark --out ../reports && python3 -m app.demo --out ../reports/demo
```

---

## Optional beat — it really runs over MCP (30s)

Only if someone asks how the KiCAD edits actually happen. The default executor writes the files
directly; this one does every edit as an MCP tool call over JSON-RPC to `backend/app/mcp_server.py`.

```bash
cd backend && KICAD_MITOS_EXECUTOR=mitos \
  KICAD_MITOS_MITOS_COMMAND="python3 -m app.mcp_server" \
  python3 -m app.benchmark --out ../reports
# 10/10 passed — every edit over the wire
```

Both executors call the same primitives, so the outcome is identical: same 9/9 checks, same ERC
10 → 1, same change list. A tool failure mid-batch still rolls the whole project back, hash-verified —
the transaction holds across a subprocess boundary.

Be precise if asked about Mitos itself: this proves *our adapter and server* work end to end. The
third-party Mitos server was never reachable and there is no public one, so its tool names stay
configurable and `docs/mitos.md` records which server the capability matrix was generated from.
Don't claim more than that.

---

## Questions you will get

**"DRC errors went from 5 to 13 and you still passed it — explain."**
The strongest question on the board, and you have the answer on screen. The sync put the new nets and
footprints on the board but laid no copper, so KiCAD correctly reports those pins as unconnected —
exactly the state KiCAD's own *Update PCB from Schematic* leaves behind. The check detail names them:
*"I2C_SCL, I2C_SDA synced to the board and still need routing."* The gate exempts only unconnected
items whose every net this sync touched. An unconnected item on any other net still rejects, so
severed copper cannot slip through.

**"So the AI decides whether it's correct?"**
No. The model only proposes, in a fixed schema. If a rule validator finds one problem with its plan,
the plan is thrown away and the deterministic planner's version is used. The accept/reject engine has
no model in it at all. The top bar says **PLANNER rules only** — no chat-model key is set, and the
entire single-change demo you just watched ran with no model whatsoever.

**"Then what is Devin doing?"**
Two separate things, and neither is a vote. In lane 1 it can answer *one* missing detail from the real
pin table when `KICAD_MITOS_AUTO_RESOLVE=true` — and only from the planner's own option list. In lane 2
it is the ten agents themselves. In both, the gate is deterministic and Devin has no say in accept or
reject.

**"Did the ten-agent run ever finish clean?"**
No. Best observed is 8 of 10 stages and `needs_human_review`. The gates are the deliverable. A run
can now also stop part-way and ask you a question rather than carrying on to a verdict — same
answer, one step earlier: the gate would not certify what it was handed.

**"What if ERC can't run?"**
Then nothing is ever accepted. Every run returns "needs your review" and the UI says so in a banner
rather than looking healthy.

**"Does it work on my project?"**
Upload a zipped KiCAD project in the picker. I2C, SPI, UART, a single GPIO link or a power-only
hookup, and the schematic must be at the archive root.

---

## If it breaks

| Symptom | Fix |
| --- | --- |
| Page won't load on :3001 | `cd frontend && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8001 npx next dev --port 3001` |
| "Request failed" in the UI | Backend down, or CORS. Restart the backend command in step 2 |
| Top bar shows no KiCAD version | `kicad-cli` fell off `PATH`; expect "needs your review" until fixed |
| Team panel 404s / goes blank | The backend restarted mid-run. The run is unrecoverable — skip to Beat 7 |
| Numbers don't match this sheet | You're on :3000 (Docker, KiCAD 9.0.8, no team lane). Switch to :3001 |
| SPI/UART refused as "not supported" | The backend predates §7. Restart it |
| You must use Docker | `docker compose up --build` — minutes, not live, and **no team lane** |

## Don't

- Don't claim it routes the board. It doesn't, by design.
- Don't claim the ten-agent lane produces a board. It produces gated stages and a release record that
  says no.
- Don't claim the Mitos MCP path is verified against a third-party server. The adapter is tested
  against a stub; no live server was ever reachable, and `docs/mitos.md` says so.
