"use client";

import { ErcReport, RunReport } from "@/lib/api";
import { Bullets, Chip, Notice, Panel, StatusDot } from "./ui";

/** Decisions named from the user's side of the screen, not by their enum. */
const DECISION: Record<RunReport["decision"], { label: string; tone: "wire" | "brick" | "copper" }> = {
  accepted: { label: "Change applied", tone: "wire" },
  rejected_and_restored: { label: "Change reverted", tone: "brick" },
  needs_user_review: { label: "Needs your review", tone: "copper" },
};

export function Verdict({
  report,
  onBackToPlan,
  onNewChange,
}: {
  report: RunReport;
  onBackToPlan: () => void;
  onNewChange: () => void;
}) {
  const decision = DECISION[report.decision];
  const validation = report.validation;
  const failed = validation.checks.filter((check) => !check.passed).length;
  const newErrors = validation.violation_diff?.new.filter((v) => v.severity === "error").length ?? 0;
  /** Whether a named gate passed, so the delta table's tone matches the verdict. */
  const passed = (name: string) => validation.checks.find((c) => c.name === name)?.passed !== false;

  return (
    <Panel
      eyebrow="Step 04"
      title="What KiCAD says about it"
      aside={<Chip>{report.duration_seconds.toFixed(1)} s</Chip>}
    >
      <Notice tone={decision.tone}>
        <p className="flex items-center gap-2 text-[0.9375rem] font-semibold tracking-[-0.012em] text-ink">
          <StatusDot tone={decision.tone} />
          {decision.label}
        </p>
        <p className="mt-1 min-w-0 wrap-any text-[0.8125rem] leading-snug text-muted">{report.reason}</p>
      </Notice>

      <div className="mt-5">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <h3 className="eyebrow">Checks</h3>
          <span className="shrink-0 font-mono text-[0.6875rem] text-muted">
            {validation.checks.length - failed}/{validation.checks.length} passed
          </span>
        </div>
        <ul className="mt-2 divide-y divide-rule/60 border-y border-rule/60">
          {validation.checks.map((check) => (
            <li key={check.name} className="flex min-w-0 items-start gap-2.5 py-2">
              <span
                aria-hidden
                className={`mt-px shrink-0 font-mono text-[0.6875rem] font-semibold ${
                  check.passed ? "text-wire" : "text-brick"
                }`}
              >
                {check.passed ? "✓" : "✕"}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block min-w-0 wrap-any font-mono text-[0.75rem] leading-snug text-ink">
                  {check.name.replace(/_/g, " ")}
                </span>
                {check.detail && (
                  <span className="mt-0.5 block min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
                    {check.detail}
                  </span>
                )}
              </span>
              <span className="sr-only">{check.passed ? "passed" : "failed"}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-5">
        <h3 className="eyebrow">Rule checks, before and after</h3>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[20rem] border-collapse text-left">
            <thead>
              <tr>
                {["", "Before", "After", "New"].map((head) => (
                  <th key={head} scope="col" className="eyebrow border-b border-rule pb-1.5 pr-3 font-medium">
                    {head}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <DeltaRow
                label="ERC"
                before={validation.erc_before}
                after={validation.erc_after}
                added={newErrors}
                addedLabel="errors"
                blamed={!passed("no_new_critical_erc_violations")}
              />
              <DeltaRow
                label="DRC"
                before={validation.drc_before}
                after={validation.drc_after}
                added={validation.drc_diff?.new.length ?? 0}
                addedLabel="violations"
                blamed={!passed("no_new_critical_drc_violations")}
              />
            </tbody>
          </table>
        </div>
        <p className="mt-2 min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
          Violations that were already there are never blamed on this change.
        </p>
      </div>

      <div className="mt-5">
        <h3 className="eyebrow">Files touched</h3>
        <div className="mt-2 flex min-w-0 flex-wrap gap-1.5">
          {validation.files_changed.length === 0 && <Chip>none</Chip>}
          {validation.files_changed.map((file) => (
            <Chip key={file} tone="wire" title={file}>
              {file}
            </Chip>
          ))}
          {validation.unexpected_files.map((file) => (
            <Chip key={file} tone="brick" title={file}>
              unexpected: {file}
            </Chip>
          ))}
        </div>
      </div>

      <Bullets title="Changes applied" items={report.changes} tone="wire" />

      {report.execution.error && (
        <div className="mt-4">
          <Notice tone="brick" title="Execution error">
            {report.execution.error}
          </Notice>
        </div>
      )}

      {report.restoration_verified !== null && (
        <p className="mt-4 flex flex-wrap items-center gap-2 text-[0.75rem] text-muted">
          <StatusDot tone={report.restoration_verified ? "wire" : "brick"} />
          Checkpoint restore {report.restoration_verified ? "verified" : "failed"}
        </p>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-rule pt-4">
        <button type="button" className="btn" onClick={onNewChange}>
          Plan another change
        </button>
        <button type="button" className="btn-ghost" onClick={onBackToPlan}>
          Back to the plan
        </button>
      </div>
    </Panel>
  );
}

function DeltaRow({
  label,
  before,
  after,
  added,
  addedLabel,
  blamed,
}: {
  label: string;
  before: ErcReport | null;
  after: ErcReport | null;
  added: number;
  addedLabel: string;
  /** Did the matching gate fail? New violations it excused are not alarming. */
  blamed: boolean;
}) {
  const ran = Boolean(after?.ran);
  const format = (report: ErcReport | null) =>
    report?.ran ? `${report.errors} err · ${report.warnings} warn` : "not run";

  return (
    <tr>
      <th scope="row" className="border-b border-rule/60 py-2 pr-3 font-mono text-[0.75rem] font-medium text-ink">
        {label}
      </th>
      <td className="border-b border-rule/60 py-2 pr-3 font-mono text-[0.75rem] text-muted">{format(before)}</td>
      <td className="border-b border-rule/60 py-2 pr-3 font-mono text-[0.75rem] text-ink">{format(after)}</td>
      <td
        className={`border-b border-rule/60 py-2 font-mono text-[0.75rem] ${
          added > 0 && blamed ? "font-semibold text-brick" : "text-muted"
        }`}
      >
        {ran ? `${added} ${addedLabel}` : "—"}
      </td>
    </tr>
  );
}
