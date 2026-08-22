# Mitos capability matrix (unverified)

No Mitos MCP server was reachable while this MVP was built, so this matrix records what the backend
*expects*. Each row must be confirmed against a live server before switching
`KICAD_MITOS_EXECUTOR=mitos` in anything but a scratch project.

**This file is meant to be generated, not hand-edited.** Point the probe at a running server and it
rewrites this document from what the server actually advertises, tool input schemas included:

```bash
python -m app.probe_mitos --command "<command that starts the Mitos MCP server>"
```

The probe exits non-zero and writes nothing when no server answers, so this file cannot be made to
look confirmed when it is not. Until it has been run, everything below is an expectation.

| Backend action | Expected Mitos tool | Inputs sent | Needed result | Status |
| --- | --- | --- | --- | --- |
| `connect_pins` | `kicad_connect_pins` | `{from, to, net_name}` with `REF.PIN` endpoints | net exists and both pins joined | unverified |
| `connect_pin_to_net` | `kicad_connect_pin_to_net` | `{pin, net}` | pin joined to the named net | unverified |
| `ensure_pullup` | `kicad_add_component` | `{lib_id, value, connections}` | resistor placed and both ends connected | unverified |
| read-back | `kicad_read_schematic` | `{}` | components, pins and nets | unverified |

Fallback behaviour today: `KICAD_MITOS_EXECUTOR=local` edits the `.kicad_sch` file directly
(global labels for connections, `Device:R` symbols for pull-ups). Both executors implement the same
`Executor` interface and produce the same `ExecutionResult`, so the validation, decision and rollback
stages are identical whichever one runs.

The adapter is not untested, only unverified against the real thing: `backend/tests/test_mitos.py`
drives it against `backend/tests/mitos_stub.py`, a stdio MCP test double, covering the JSON-RPC
client, the action-to-tool mapping, first-failure abort and the probe itself.

Open questions for the Mitos team:

1. Exact tool names, argument schemas and error shapes.
2. Whether Mitos can add a symbol and its connections in one call, or needs place + connect.
3. Whether Mitos writes to disk immediately (required — ERC runs on the saved file).
4. Whether a Mitos-side undo exists, or whether the file-level checkpoint here stays the rollback.
5. Whether PCB edits are supported, which is what would make the DRC gate meaningful.

## Blocked on the above: live KiCAD selection

Plan §5.2 offers three ways to choose components: the current KiCAD selection, typed references, or a
selection UI. The last two are built. Reading the user's live selection needs a Mitos tool that may
not exist, so no endpoint has been written for it — a speculative one against a guessed tool name
would be indistinguishable from a working feature until someone tried it. `probe_mitos` asks this
question explicitly (item 4 of its "still to confirm" list); if such a tool is advertised, wiring it
up is small.
