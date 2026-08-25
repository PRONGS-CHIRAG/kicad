"use client";

import type { ReactNode } from "react";
import {
  AGENT_BY_ID,
  CheckFinding,
  StageAttempt,
  TeamRunReport,
  TeamStageId,
  TeamFlow,
} from "@/lib/team";
import { Bullets, Chip, DataCell, Notice, Panel } from "./ui";

/**
 * One stage, opened up: what it was given, who ran it, what it proposed, and
 * what the gate made of that. The output renderers below are per stage because
 * each agent has its own typed output model — showing a requirements document
 * and a layout proposal through the same generic viewer would hide exactly the
 * detail worth reading.
 */
export function TeamStageDetail({
  stage,
  flow,
  report,
  onClose,
}: {
  stage: TeamStageId;
  flow: TeamFlow;
  report: TeamRunReport | null;
  onClose: () => void;
}) {
  const agent = AGENT_BY_ID[stage];
  const state = flow.stages[stage];
  const attempt: StageAttempt | undefined = state.attempts[state.attempts.length - 1];
  const result = report?.per_stage_results?.[stage];
  const output = result?.output ?? null;

  return (
    <Panel
      eyebrow={`Agent ${String(Object.keys(AGENT_BY_ID).indexOf(stage) + 1).padStart(2, "0")}`}
      title={agent.name}
      aside={
        <button type="button" className="btn-ghost shrink-0 !px-2.5 !py-1 !text-[0.75rem]" onClick={onClose}>
          Close
        </button>
      }
    >
      <p className="min-w-0 wrap-any text-[0.8125rem] leading-snug text-muted">
        {agent.role}. {agent.produces}.
      </p>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-y border-rule py-3 sm:grid-cols-4">
        <DataCell label="Output model" value={agent.outputModel} />
        <DataCell label="Grounded by" value={agent.gate} />
        <DataCell label="Design files" value={agent.mutates ? "may write" : "read-only"} />
        <DataCell label="Passes" value={String(state.attempts.length)} />
      </dl>

      <section className="mt-4">
        <h3 className="eyebrow">Given to it</h3>
        <div className="mt-2 flex min-w-0 flex-wrap gap-1.5">
          {agent.reads.length === 0 && <Chip>the request and the project itself</Chip>}
          {agent.reads.map((source) => (
            <Chip key={source} tone="wire">
              {AGENT_BY_ID[source].name}
            </Chip>
          ))}
        </div>
        <p className="mt-2 min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
          Only these outputs are passed on — the registry declares each agent&apos;s inputs, so a later stage
          cannot quietly read something it was not given.
        </p>
      </section>

      {attempt?.agent && (
        <section className="mt-4 border-t border-rule pt-4">
          <h3 className="eyebrow">The session</h3>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
            <DataCell label="Runner" value={result?.runner ?? attempt.agent.runner ?? "unknown"} />
            <DataCell label="Status" value={result?.status ?? attempt.agent.result} />
            <DataCell
              label="ACUs"
              value={(result?.acus_consumed ?? attempt.agent.acus_consumed).toFixed(2)}
            />
            <DataCell
              label="Took"
              value={`${(result?.duration_seconds ?? attempt.agent.duration_seconds).toFixed(1)} s`}
            />
          </dl>
          {(result?.session_url ?? attempt.agent.session_url) && (
            <p className="mt-2 min-w-0 wrap-any font-mono text-[0.6875rem] leading-snug text-muted">
              {renderSessionUrl(result?.session_url ?? attempt.agent.session_url)}
            </p>
          )}
          {result?.unresolved_questions?.length ? (
            <Bullets title="Questions it could not settle" items={result.unresolved_questions} tone="copper" />
          ) : null}
        </section>
      )}

      <section className="mt-4 border-t border-rule pt-4">
        <h3 className="eyebrow">The gate</h3>
        {attempt?.gate ? (
          <>
            <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[0.75rem] leading-none">
              <span className="text-muted">{attempt.gate.tool}</span>
              <span aria-hidden className="text-rule">
                →
              </span>
              <span className={attempt.gate.result === "passed" ? "text-wire" : "text-brick"}>
                {attempt.gate.result}
              </span>
            </p>
            <FindingsTable findings={attempt.gateFindings} />
          </>
        ) : state.unresolvedDetail ? (
          <div className="mt-2">
            <Notice
              tone={state.phase === "failed" ? "brick" : state.phase === "passed" ? "wire" : "copper"}
              title={state.phase === "failed" ? "Rejected by the orchestrator" : "Decided by the orchestrator"}
            >
              {state.unresolvedDetail}
            </Notice>
          </div>
        ) : (
          <p className="mt-2 min-w-0 wrap-any text-[0.8125rem] leading-snug text-muted">
            {state.attempts.length === 0
              ? "This stage has not run yet."
              : "The agent has reported; its gate is still running."}
          </p>
        )}
      </section>

      {output !== null && output !== undefined && (
        <section className="mt-4 border-t border-rule pt-4">
          <h3 className="eyebrow">What it proposed</h3>
          <div className="mt-2 min-w-0">
            <StageOutput stage={stage} output={output} />
          </div>
        </section>
      )}
    </Panel>
  );
}

/** Devin sessions are real links; the stub's `stub://` marker is not. */
function renderSessionUrl(url: string | null | undefined): ReactNode {
  if (!url) return null;
  if (url.startsWith("http")) {
    return (
      <a href={url} target="_blank" rel="noreferrer" className="text-wire underline">
        {url}
      </a>
    );
  }
  return url;
}

function FindingsTable({ findings }: { findings: CheckFinding[] }) {
  if (findings.length === 0) {
    return (
      <p className="mt-2 min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
        Nothing to report — the check found no findings at all.
      </p>
    );
  }
  const tone = (severity: string) =>
    severity === "error" ? "text-brick" : severity === "warning" ? "text-copper" : "text-muted";

  return (
    <ul className="mt-2 divide-y divide-rule/60 border-y border-rule/60">
      {findings.map((finding, index) => (
        <li key={`${finding.rule}-${index}`} className="min-w-0 py-2">
          <p className="flex flex-wrap items-baseline gap-x-2">
            <span className={`font-mono text-[0.6875rem] uppercase tracking-[0.08em] ${tone(finding.severity)}`}>
              {finding.severity}
            </span>
            <span className="min-w-0 wrap-any text-[0.8125rem] font-medium leading-snug text-ink">
              {finding.rule}
            </span>
          </p>
          <dl className="mt-1.5 grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-2">
            <Pair label="found" value={format(finding.actual)} />
            <Pair label="expected" value={format(finding.expected)} />
            <Pair label="object" value={finding.kicad_object} />
            <Pair label="source" value={finding.evidence_source} />
          </dl>
        </li>
      ))}
    </ul>
  );
}

function Pair({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 gap-2">
      <dt className="eyebrow shrink-0 pt-[0.15rem]">{label}</dt>
      <dd className="min-w-0 wrap-any font-mono text-[0.75rem] leading-snug text-ink">{value}</dd>
    </div>
  );
}

function format(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value || "—";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

/* ------------------------------ stage outputs ----------------------------- */

type Row = Record<string, unknown>;

function asRows(value: unknown): Row[] {
  return Array.isArray(value) ? (value.filter((item) => typeof item === "object" && item !== null) as Row[]) : [];
}

function asObject(value: unknown): Row {
  return typeof value === "object" && value !== null ? (value as Row) : {};
}

function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => (typeof item === "string" ? item : format(item))) : [];
}

/** Compact table for a list of typed records. Columns are named, never guessed. */
function MiniTable({
  columns,
  rows,
  empty,
}: {
  columns: { key: string; label: string; render?: (row: Row) => ReactNode }[];
  rows: Row[];
  empty: string;
}) {
  if (rows.length === 0) {
    return <p className="min-w-0 wrap-any text-[0.8125rem] leading-snug text-muted">{empty}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col" className="eyebrow border-b border-rule pb-1.5 pr-3 font-medium">
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td
                  key={column.key}
                  className="min-w-0 border-b border-rule/60 py-2 pr-3 align-top font-mono text-[0.75rem] leading-snug text-ink [overflow-wrap:anywhere]"
                >
                  {column.render ? column.render(row) : format(row[column.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Facts({ items }: { items: { label: string; value: string }[] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
      {items.map((item) => (
        <DataCell key={item.label} label={item.label} value={item.value} />
      ))}
    </dl>
  );
}

/**
 * `from` and `to` are Pydantic *aliases* on these models, and the API dumps by
 * field name — so the wire carries `from_pin`/`from_block`, while a payload
 * echoed back through an alias-aware path carries `from`. Read both, or the
 * dominant intent type renders as a pair of dashes and says nothing.
 */
function edge(row: Row, side: "from" | "to"): string {
  return format(row[side] ?? row[`${side}_pin`] ?? row[`${side}_block`]);
}

/** Small maps read better spelled out than as raw JSON: a rail test's
    {min: 3.1, max: 3.5}, or a requirement's evidence {net: "I2C_SDA"}. */
function describeMap(value: unknown): string {
  if (typeof value !== "object" || value === null) return format(value);
  return Object.entries(value as Row)
    .map(([key, item]) => `${key} ${format(item)}`)
    .join(", ");
}

/** The three action types the whole pipeline is built on, in words. */
function describeIntent(row: Row): string {
  switch (row.type) {
    case "connect_pins":
      return `${edge(row, "from")} ↔ ${edge(row, "to")} as ${format(row.net_name)}`;
    case "connect_pin_to_net":
      return `${format(row.pin)} → ${format(row.net)}`;
    case "ensure_pullup":
      return `${format(row.value)} pull-up from ${format(row.net)} to ${format(row.to_net)}`;
    default:
      return format(row);
  }
}

function StageOutput({ stage, output }: { stage: TeamStageId; output: unknown }) {
  const data = asObject(output);

  switch (stage) {
    case "project_manager":
      return (
        <div className="space-y-3">
          <p className="min-w-0 wrap-any text-[0.875rem] font-medium leading-snug text-ink">
            {format(data.project_goal)}
          </p>
          <div>
            <h4 className="eyebrow">Stage order it proposed</h4>
            <div className="mt-1.5 flex min-w-0 flex-wrap gap-1.5">
              {asStrings(data.workflow).length === 0 && <Chip>none — the canonical order is used</Chip>}
              {asStrings(data.workflow).map((item, index) => (
                <Chip key={`${item}-${index}`} tone="wire">
                  {index + 1}. {item}
                </Chip>
              ))}
            </div>
          </div>
          <Facts items={[{ label: "Status", value: format(data.status) }]} />
        </div>
      );

    case "requirements": {
      const power = asObject(data.power);
      const mechanical = asObject(data.mechanical);
      return (
        <div className="space-y-4">
          <MiniTable
            rows={asRows(data.requirements)}
            empty="No requirements were produced."
            columns={[
              { key: "id", label: "ID" },
              { key: "statement", label: "Statement" },
              {
                key: "value",
                label: "Value",
                render: (row) => `${format(row.value)}${row.unit ? ` ${row.unit}` : ""}`,
              },
              { key: "category", label: "Category" },
              { key: "source", label: "Source" },
            ]}
          />
          <Facts
            items={[
              { label: "Input", value: format(power.input) },
              { label: "Logic", value: format(power.logic_voltage) },
              { label: "Max current", value: format(power.maximum_current_ma) },
              { label: "Max width", value: format(mechanical.maximum_width_mm) },
              { label: "Max height", value: format(mechanical.maximum_height_mm) },
              { label: "Layers", value: format(mechanical.layers) },
            ]}
          />
          <MiniTable
            rows={asRows(data.interfaces)}
            empty="No interfaces were named."
            columns={[
              { key: "type", label: "Interface" },
              { key: "voltage", label: "Voltage" },
              { key: "devices", label: "Devices" },
            ]}
          />
          <Bullets title="Constraints" items={asStrings(data.constraints)} />
          <Bullets title="Acceptance tests" items={asStrings(data.acceptance_tests)} tone="wire" />
        </div>
      );
    }

    case "architecture":
      return (
        <div className="space-y-4">
          <MiniTable
            rows={asRows(data.blocks)}
            empty="No functional blocks were produced — which is what the architecture gate rejects."
            columns={[
              { key: "id", label: "Block" },
              { key: "type", label: "Type" },
              {
                key: "requirement_ids",
                label: "Serves",
                render: (row) => asStrings(row.requirement_ids).join(", ") || "—",
              },
              {
                key: "power",
                label: "Power",
                render: (row) =>
                  row.power_required_ma || row.power_available_ma
                    ? `${format(row.power_required_ma)} / ${format(row.power_available_ma)} mA`
                    : "—",
              },
            ]}
          />
          <MiniTable
            rows={asRows(data.connections)}
            empty="No block connections were produced."
            columns={[
              { key: "from", label: "From", render: (row) => edge(row, "from") },
              { key: "to", label: "To", render: (row) => edge(row, "to") },
              { key: "signal", label: "Signal" },
            ]}
          />
          <Bullets title="Open questions" items={asStrings(data.unresolved_questions)} tone="copper" />
        </div>
      );

    case "components":
      return (
        <div className="space-y-3">
          <MiniTable
            rows={asRows(data.components)}
            empty="No parts were selected."
            columns={[
              { key: "reference_group", label: "Group" },
              { key: "manufacturer_part", label: "Part" },
              { key: "quantity", label: "Qty" },
              { key: "symbol", label: "Symbol" },
              { key: "footprint", label: "Footprint" },
            ]}
          />
          {asRows(data.components).map((component, index) => (
            <div key={index} className="min-w-0 border-t border-rule/60 pt-2">
              <p className="min-w-0 wrap-any text-[0.8125rem] leading-snug text-ink">
                <span className="font-mono text-[0.75rem] text-muted">{format(component.reference_group)} — </span>
                {format(component.reason)}
              </p>
              <Bullets title="Checked against" items={asStrings(component.verified_constraints)} tone="wire" />
            </div>
          ))}
        </div>
      );

    case "schematic_design":
      return (
        <div className="space-y-4">
          <Facts
            items={[
              { label: "Protocol", value: format(data.protocol) },
              { label: "Logic", value: format(data.logic_voltage) },
              { label: "Pull-up", value: format(data.pullup_value) },
            ]}
          />
          <MiniTable
            rows={asRows(data.intents)}
            empty="No connection intents were produced."
            columns={[
              { key: "type", label: "Intent" },
              { key: "detail", label: "What it does", render: (row) => describeIntent(row) },
              { key: "purpose", label: "Why" },
            ]}
          />
          <Bullets title="Protected" items={asStrings(data.protected_objects)} tone="copper" />
          <Bullets title="Assumptions" items={asStrings(data.assumptions)} />
        </div>
      );

    case "pcb_layout":
      return (
        <div className="space-y-4">
          <MiniTable
            rows={asRows(data.placements)}
            empty="No footprints were placed."
            columns={[
              { key: "reference", label: "Ref" },
              {
                key: "position",
                label: "Position",
                render: (row) => `${format(row.x)}, ${format(row.y)} mm @ ${format(row.rotation)}°`,
              },
              { key: "rationale", label: "Why" },
            ]}
          />
          <div>
            <h4 className="eyebrow">Critical nets</h4>
            <div className="mt-1.5 flex min-w-0 flex-wrap gap-1.5">
              {asStrings(data.critical_nets).length === 0 && <Chip>none named</Chip>}
              {asStrings(data.critical_nets).map((net) => (
                <Chip key={net} tone="wire">
                  {net}
                </Chip>
              ))}
            </div>
          </div>
          <div>
            <h4 className="eyebrow">Left unrouted</h4>
            <div className="mt-1.5 flex min-w-0 flex-wrap gap-1.5">
              {asStrings(data.unrouted_nets).length === 0 && <Chip>none</Chip>}
              {asStrings(data.unrouted_nets).map((net) => (
                <Chip key={net} tone="copper">
                  {net}
                </Chip>
              ))}
            </div>
            <p className="mt-2 min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
              This lane places footprints and binds nets; it does not route copper. An unrouted net here is
              pending work, not a fault.
            </p>
          </div>
        </div>
      );

    case "simulation":
      return (
        <div className="space-y-4">
          <MiniTable
            rows={asRows(data.tests)}
            empty="No tests were run."
            columns={[
              { key: "name", label: "Test" },
              {
                key: "expected",
                label: "Expected",
                render: (row) =>
                  row.expected !== undefined
                    ? describeMap(row.expected)
                    : `${format(row.required_ma)} mA required`,
              },
              {
                key: "measured",
                label: "Measured",
                render: (row) =>
                  row.measured_v !== undefined
                    ? format(row.measured_v)
                    : `${format(row.available_ma)} mA available`,
              },
              {
                key: "status",
                label: "Result",
                render: (row) => (
                  <span
                    className={
                      String(row.status).toLowerCase().startsWith("pass") ? "text-wire" : "text-brick"
                    }
                  >
                    {format(row.status)}
                  </span>
                ),
              },
            ]}
          />
          <Bullets title="Models" items={asStrings(data.models)} />
          <Bullets title="Assumptions" items={asStrings(data.assumptions)} tone="copper" />
          <p className="min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
            Closed-form arithmetic, not ngspice. These are sanity margins, not a simulated circuit.
          </p>
        </div>
      );

    case "verification":
      return (
        <div className="space-y-4">
          <Facts
            items={[
              { label: "Decision", value: format(data.decision) },
              { label: "Passed", value: format(data.requirements_passed) },
              { label: "Failed", value: format(data.requirements_failed) },
              { label: "Unverified", value: format(data.requirements_unverified) },
              { label: "Total", value: format(data.requirements_total) },
            ]}
          />
          <MiniTable
            rows={asRows(data.requirement_outcomes)}
            empty="No per-requirement outcomes were recorded."
            columns={[
              { key: "requirement_id", label: "Requirement" },
              {
                key: "status",
                label: "Outcome",
                render: (row) => (
                  <span
                    className={
                      row.status === "passed"
                        ? "text-wire"
                        : row.status === "failed"
                          ? "text-brick"
                          : "text-copper"
                    }
                  >
                    {format(row.status)}
                  </span>
                ),
              },
              {
                key: "evidence",
                label: "Evidence",
                render: (row) => describeMap(row.evidence),
              },
            ]}
          />
          <MiniTable
            rows={asRows(data.critical_findings)}
            empty="No critical findings."
            columns={[
              { key: "requirement_id", label: "Requirement" },
              { key: "finding", label: "Finding" },
              { key: "severity", label: "Severity" },
            ]}
          />
        </div>
      );

    case "manufacturing": {
      const rules = asObject(data.profile_rules);
      return (
        <div className="space-y-4">
          <Facts
            items={[
              { label: "Profile", value: format(data.manufacturer_profile) },
              { label: "DFM", value: format(data.dfm_status) },
              { label: "Fab ready", value: format(data.fabrication_ready) },
            ]}
          />
          <MiniTable
            rows={asRows(data.findings)}
            empty="No DFM findings against this profile."
            columns={[
              { key: "type", label: "Type" },
              { key: "net", label: "Net" },
              { key: "severity", label: "Severity" },
              { key: "recommendation", label: "Recommendation" },
            ]}
          />
          <div>
            <h4 className="eyebrow">Profile rules it was checked against</h4>
            <div className="mt-1.5 flex min-w-0 flex-wrap gap-1.5">
              {Object.entries(rules).map(([key, value]) => (
                <Chip key={key}>
                  {key.replace(/_/g, " ")}: {format(value)}
                </Chip>
              ))}
              {Object.keys(rules).length === 0 && <Chip>none reported</Chip>}
            </div>
          </div>
          {data.profile_provenance ? (
            <Notice tone="copper" title="Where these numbers come from">
              {format(data.profile_provenance)}
            </Notice>
          ) : null}
        </div>
      );
    }

    case "qa_release": {
      const checklist = asObject(data.checklist);
      const entries = Object.entries(checklist);
      return (
        <div className="space-y-4">
          <Facts
            items={[
              { label: "Release", value: format(data.release_status) },
              { label: "Version", value: format(data.project_version) },
              { label: "Open criticals", value: format(data.open_critical_findings) },
            ]}
          />
          <div>
            <h4 className="eyebrow">Checklist</h4>
            <ul className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2">
              {entries.length === 0 && (
                <li className="text-[0.8125rem] text-muted">No checklist was recorded.</li>
              )}
              {entries.map(([key, value]) => (
                <li key={key} className="flex min-w-0 items-start gap-2">
                  <span
                    aria-hidden
                    className={`mt-px shrink-0 font-mono text-[0.6875rem] font-semibold ${
                      value ? "text-wire" : "text-brick"
                    }`}
                  >
                    {value ? "✓" : "✕"}
                  </span>
                  <span className="min-w-0 wrap-any font-mono text-[0.75rem] leading-snug text-ink">
                    {key.replace(/_/g, " ")}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h4 className="eyebrow">Files in the release</h4>
            <div className="mt-1.5 flex min-w-0 flex-wrap gap-1.5">
              {asStrings(data.included_files).length === 0 && <Chip>none</Chip>}
              {asStrings(data.included_files).map((file) => (
                <Chip key={file} tone="wire" title={file}>
                  {file.split("/").pop()}
                </Chip>
              ))}
            </div>
          </div>
          <p className="min-w-0 wrap-any font-mono text-[0.6875rem] leading-snug text-muted">
            hash {format(data.release_hash)}
          </p>
        </div>
      );
    }
  }
}
