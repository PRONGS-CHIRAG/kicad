# Demo runbook

Six minutes, five beats. Every number below was measured on this machine, not estimated.

## Before the room fills

**Demo on http://localhost:3001. Not :3000.**

Both are running. `:3000` is the Docker stack, built before the board-sync feature landed, and it runs
KiCAD 9 — the board half of the demo silently does nothing there, with no error to explain why.
`:3001` runs current code against KiCAD 10.0.5. Verified: the browser on `:3001` calls the backend on
`127.0.0.1:8001`.

**Restart both dev servers before you start, even if they look fine.** A long-running server
serves the code it was started with; one left up from an earlier session will happily answer
requests with pre-SPI behaviour and no error to explain why. This bit me during testing.

```bash
# backend — the CORS origin is required, or the browser on :3001 is blocked
cd backend && KICAD_MITOS_CORS_ORIGINS='["http://localhost:3001"]' \
  python3 -m uvicorn app.main:app --reload --port 8001

# frontend, in a second terminal
cd frontend && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8001 npx next dev --port 3001
```

Then confirm you are live:

```bash
curl -sS http://127.0.0.1:8001/api/health
# {"status":"ok","kicad_cli":"10.0.5","erc_supported":true,"drc_supported":true,...}
```

`kicad_cli` must show a version and `erc_supported` must be `true`. If either is missing, every run
comes back "needs your review" and the demo has no punchline. The top bar in the UI shows the same
thing — **KICAD-CLI 10.0.5 · ERC ready · DRC ready** — so the judges can see it too.

Have these open in tabs before you start: `reports/benchmark.html` and `reports/demo/index.html`.

## The line to open with

> "KiCAD tells you the state of your board. It won't tell you what your last edit did to it, and it
> won't undo that edit when the answer is 'made it worse.' That's the gap we built into."

Do not pitch this as electrical expertise. The whole design is that it *defers* every electrical
judgment to KiCAD's own rule checker. Pitch the rigor: **the machine that writes the change is never
the machine that judges it.**

## Beat 1 — Admit the mess first (30s)

Open project **esp32_i2c_demo**. Point at the panel on the left before touching anything:

**BASELINE ERC 10 err · 8 warn — BASELINE DRC 5 err · 13 warn**

> "This project is already broken in ten ways. Watch what we don't get blamed for."

This is the beat everyone skips and it is the one that buys you the rest. Naming your own baseline is
what makes the later numbers credible.

## Beat 2 — The plan, before anything is written (90s)

Tick **U1** and **U2** in the component list. Leave the default instruction in the box:

```
Connect these components using I2C with 3.3 V logic.
Add the required pull-up resistors.
Do not modify the USB circuit.
```

Click **Plan the change**. You get six actions, each drawn as a little wire diagram — two pin-to-pin
connections, power, ground, and two 4.7k pull-ups with the resistor symbol. Read the **assumptions**
out loud:

> "U1 is the controller, U2 is the peripheral, GPIO21 for SDA, GPIO22 for SCL — the ESP32 defaults.
> It says so, so you can catch it if it's wrong. And 'do not modify the USB circuit' became three
> protected nets it is now forbidden to touch."

Point at the footer: *"A checkpoint is taken first. If ERC comes back worse, the change is reverted."*
Then point at the schematic: *"6 proposed actions are not on the sheet yet."* **Nothing has happened.**

## Beat 3 — Approve (60s)

Click **Approve and apply**. In about a second:

| What lands | Number |
| --- | --- |
| Verdict | **Change applied** |
| Checks | **9 / 9 passed** |
| ERC | 10 err → **1 err**, 0 new errors, **9 pre-existing violations resolved** |
| Files touched | `.kicad_sch` and `.kicad_pcb`, nothing else |
| Board | 2 nets added, 6 pads rebound, 2 footprints placed |

> "Nine independent checks, none of them run by a language model. Every requested connection exists,
> nothing unapproved moved, the protected nets are untouched, and KiCAD reports no new errors. It also
> synced the board, because we implemented KiCAD's 'Update PCB from Schematic' ourselves — `kicad-cli`
> doesn't ship it."

Read the line under the before/after table verbatim: *"Violations that were already there are never
blamed on this change."* That sentence is the product.

## Beat 4 — Make it say no (45s)

Click **Plan another change**, keep U1 and U2, and type:

```
Connect these using I2C with 5V logic
```

It refuses:

> **"The project has no 5V rail, so U2 cannot be powered at 5V. Use 3.3V instead?"**

with **3.3V** as a clickable option that re-plans instantly.

> "It read the actual schematic, found no 5 V rail, and asked instead of guessing. A chatbot would
> have written you a 5 V bus."

If you have time, open `esp32_spi_display` and say `Connect these over SPI at 3.3V` — four signals,
power and ground, and *no* pull-ups, because SPI doesn't want them. Or `esp32_uart_module` with
`Connect these over UART at 3.3V`, which crosses the pair for you: the controller's TX goes to the
module's RX and vice versa, on two distinct nets. `esp32_gpio_peripheral` is the best one for this
beat though — the relay's pins are called IN1/IN2/FAULT, nothing it can pattern-match, so it asks
which pin carries the signal rather than picking one.

## Beat 5 — Break it on purpose (45s)

Terminal:

```bash
python3 scripts/live_rollback.py
```

```
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

## Beat 6 — The scoreboard (30s)

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
no model in it at all. Right now the top bar says **PLANNER rules only** — no API key is set, and the
entire demo you just watched ran with no model whatsoever.

**"What if ERC can't run?"**
Then nothing is ever accepted. Every run returns "needs your review" and the UI says so in a banner
rather than looking healthy.

**"Does it work on my project?"**
Upload a zipped KiCAD project in the picker. I2C, SPI, UART, a single GPIO link or a power-only
hookup, and the schematic must be at the archive root.

## If it breaks

| Symptom | Fix |
| --- | --- |
| Page won't load on :3001 | `cd frontend && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8001 npx next dev --port 3001` |
| "Request failed" in the UI | Backend down, or CORS: `cd backend && KICAD_MITOS_CORS_ORIGINS='["http://localhost:3001"]' python3 -m uvicorn app.main:app --port 8001` |
| Top bar shows no KiCAD version | `kicad-cli` fell off `PATH`; expect "needs your review" until fixed |
| Numbers don't match this sheet | You're on :3000 (stale Docker), or a server predates the code. Switch to :3001 and restart both |
| SPI/UART refused as "not supported" | The backend predates §7. Restart it |
| You must use Docker | `docker compose up --build` — minutes, not live. Then :3000 is current |

## Don't

- Don't demo the same session twice. After an accepted run the baseline moves, so the second run has
  nothing left to resolve and the 10 → 1 headline disappears. Click **Plan another change** or reopen
  the project.
- Don't claim it routes the board. It doesn't, by design.
- Don't claim the Mitos MCP path is verified. The adapter is tested against a stub; no live server was
  ever reachable, and `docs/mitos.md` says so.
