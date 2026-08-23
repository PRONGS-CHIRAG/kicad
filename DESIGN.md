# Design notes

Rationale behind the non-obvious decisions in KiCAD Mitos. The README covers what to run; this
covers why it works this way.

## Decision engine

- **ERC/DRC baseline is violation-by-violation, not a count.** Pre-existing violations are never
  blamed on a change, and a violation that got "swapped" for a different one at the same location is
  still detected — a raw count could hide that.
- **DRC baseline is the union of a couple of passes.** KiCAD's own DRC is not deterministic: on a
  byte-identical board it intermittently reports one or two fewer clearance violations. Whichever
  single run became the baseline would otherwise decide whether a later, normal run looked like it
  introduced errors — that flakiness rejected valid work about one time in twenty before the union
  was taken.
- **DRC is a real gate for any run that wrote the board**, which a schematic edit does, because the
  board is synced to match. The one carve-out is unconnected items on nets that the sync touched: a
  freshly synced board is unrouted by design, so those are pending work, not a regression. An
  unconnected item on any other net still rejects, so severed copper cannot slip through.
- **The file-level diff is snapshotted straight after execution, before ERC runs**, because
  `kicad-cli` rewrites project files as a side effect of running ERC/DRC. Snapshotting after would
  make `kicad-cli`'s own rewrites look like unauthorized changes. Only the schematic may change; a
  touched `.kicad_pro` or symbol library is treated as unauthorized.
- **Every run is transactional.** The whole project is checkpointed and fully restored if any check
  fails, so a rejected run leaves the project byte-identical to before the attempt.

## Protocols as data, not code

A protocol is a data table of per-signal pin aliases, named separately for the controller and the
peripheral. UART's TX/RX crossover falls out of that table with no special-cased logic — it's just
what the controller's "TX" alias maps to on the peripheral side. All five supported patterns (I2C,
SPI, UART, single GPIO link, power-only) decompose into the same three action types, so adding a
protocol is adding a table, not a code path.

## The Devin agent: why the split, and where it broke once

By default the planner refuses to guess — no SDA pin named on the symbol means a question, not an
assumption. The alternative to asking the user is a Devin session, but only for genuine ambiguities,
never for refusals:

| | does |
| --- | --- |
| Devin agent | answers one missing detail, from the real pin table and the part's datasheet |
| Deterministic rules | build the plan, validate it, run ERC/DRC, decide, roll back |

The agent never writes an action, never sees the validator, and has no say in accept/reject. Every
answer it gives must be one of the options the planner itself offered — anything else is discarded —
and each one is recorded as a visible assumption on the plan (the preview says *"3:P3 chosen … by
the Devin agent: the datasheet says pin 3 is SDA"* rather than presenting it as fact). `source` on
the plan response reads `agent+rules` when the agent contributed.

**Three clarifications are never auto-resolved**, because they are refusals rather than ambiguities:
`incompatible_voltage`, `incompatible_logic_voltage` and `unsupported_protocol`. Asking for 5V must
not quietly become a 3.3V bus — better to reject an uncertain instruction than silently produce a
wrong connection. Neither are the three that pick *which* components get connected. The resolvable
set is an allowlist, so a reason code added later is unresolvable until someone says otherwise.

If the session times out, errors, answers something that was not on the list, or leads to a plan the
validator rejects, the question comes back to the user — the agent degrades to the old behaviour
rather than to a dead screen. That last case is not hypothetical: running this against the live API,
the agent once picked a pin that the fixture ties to GND, which would have grounded the I2C clock.
The validator caught it (*"refusing to pull up power net GND"*), and the run fell back to asking.
The agent is fallible; the rules are the backstop, which is the whole point of the split.

Measured against the live v3 API: an answer arrives in roughly 20–40s for 0 ACU, and it arrives while
the session is still `running` — the client reads `structured_output` the moment it appears instead
of waiting for the session to exit.

## Mitos integration: what's proven and what isn't

`backend/app/execution/mitos.py` and `backend/app/mcp_server.py` make the MCP path real, not
decorative — it runs the whole pipeline over JSON-RPC. Both the local and MCP executors call the
same edit primitives (`app/kicad/edits.py`), so the two paths cannot drift: driving the golden plan
through either leaves byte-identical pin-to-net state, and a tool failure mid-batch still rolls the
whole project back, hash-verified.

What this does *not* claim: the third-party Mitos server is still unconfirmed — nothing reachable
here speaks for it, and searching turns up no public Mitos. `DEFAULT_TOOL_MAP` is configurable
precisely so its real names can be dropped in. `docs/mitos.md` is generated from a live `tools/list`
call and says which server it was generated from, so a stale capability matrix can't be mistaken for
a current one.

The adapter is covered by tests that run it against `backend/tests/mitos_stub.py`, a stdio MCP test
double — those prove the client, the action-to-tool mapping, and the probe work, and prove nothing
about the real Mitos server. `probe_mitos.py` exits non-zero and writes nothing when no server
answers, so an unverified matrix can never masquerade as a verified one.

## Ten-agent engineering team

The team coordinates a project manager, requirements, architecture, component, schematic, PCB
layout, simulation, verification, manufacturing, and QA/release agent. The canonical workflow runs
layout and simulation as siblings, then verifies, checks manufacturing, and prepares release. Failed
gates route back to the responsible stage and replay the downstream remainder; two return trips end
in human review rather than looping indefinitely.

Agents receive parsed schematic and board context. Devin runs use validated structured output and one
session per invocation. Keyless runs use the explicitly labelled `STUB` runner, which is deterministic
and never contacts Devin — useful for tests and for anyone without a Devin key. Agents do not edit
files or open pull requests; schematic and placement proposals are applied only through the same
checkpointed, deterministic gates the rest of the tool uses.

**Advisory vs. authoritative findings.** The architecture gate's block-diagram connectivity and
signal-name findings are advisory warnings: the block's free-text lists and connection labels are not
authoritative design evidence — they're an LLM's summary of a diagram, not a parsed netlist.
Authoritative connectivity comes from KiCAD ERC and DRC plus the verification stage against the real
schematic and board. Structural findings such as invalid references, unmapped functional
requirements, and clear power-capacity violations remain hard errors; a power estimate within 10%
over capacity is reported as marginal rather than stopping the run, since closed-form power estimates
carry enough slop that a hard cutoff there would reject viable designs.

**Current MVP limits:** it does not route copper or run ngspice — simulation is closed-form
arithmetic, and footprint extents are checked only where the parser exposes them. `ready for
engineering review` is not a guarantee that a board works: KiCAD checks cannot replace physical
prototyping and lab validation.
