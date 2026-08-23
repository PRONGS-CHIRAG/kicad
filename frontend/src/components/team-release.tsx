"use client";

import { useState } from "react";
import { RELEASE_TONE, TeamRunStatus, releaseLabel } from "@/lib/team";
import { Chip, Notice, Panel, StatusDot } from "./ui";

/**
 * The release panel. It reports exactly what the backend reported: the tool
 * statuses are prose the orchestrator composed ("failed (baseline; layout stage
 * did not run)"), so they are printed verbatim rather than reduced to a colour.
 *
 * "Ready for engineering review" is the best verdict the pipeline can give, and
 * it is not a claim that the board works — so the panel says so where the
 * verdict is, not in a footnote somewhere else.
 */
export function TeamRelease({
  run,
  busy,
  onAnswer,
  onNewRun,
}: {
  run: TeamRunStatus;
  busy: boolean;
  onAnswer: (answer: string) => void;
  onNewRun: () => void;
}) {
  const [answer, setAnswer] = useState("");
  const report = run.report;

  if (run.status === "failed") {
    return (
      <Panel eyebrow="Step 03" title="The run did not finish">
        <Notice tone="brick" title="Backend error">
          {run.error ?? "The run failed without reporting a reason."}
        </Notice>
        <div className="mt-5 border-t border-rule pt-4">
          <button type="button" className="btn" onClick={onNewRun}>
            Start over
          </button>
        </div>
      </Panel>
    );
  }

  if (!report) return null;

  const tone = RELEASE_TONE[report.release_status] ?? "copper";
  const needsHuman = report.release_status === "needs_human_review";

  return (
    <Panel
      eyebrow="Step 03"
      title="Release status"
      aside={<Chip tone={tone}>{report.critical_issues} critical</Chip>}
    >
      <Notice tone={tone}>
        <p className="flex items-center gap-2 text-[0.9375rem] font-semibold tracking-[-0.012em] text-ink">
          <StatusDot tone={tone} />
          {releaseLabel(report.release_status)}
        </p>
        <p className="mt-1 min-w-0 wrap-any text-[0.8125rem] leading-snug text-muted">
          {report.release_status === "ready for engineering review"
            ? "Every gate accepted its stage. This is a reviewable package, not a board known to work — physical prototyping and lab validation are still required."
            : needsHuman
              ? "A gate kept rejecting its stage, or the run reached a question no agent could settle. Nothing further is decided automatically."
              : report.summary}
        </p>
      </Notice>

      <dl className="mt-5 grid grid-cols-2 gap-px border border-rule bg-rule sm:grid-cols-3">
        <Cell
          label="Requirements met"
          value={
            report.requirements_status === "not run"
              ? "not run"
              : `${report.requirements_satisfied}/${report.requirements_total}`
          }
          tone={
            report.requirements_total > 0 && report.requirements_satisfied === report.requirements_total
              ? "wire"
              : "copper"
          }
        />
        <Cell
          label="Power tests"
          value={
            report.power_tests_status === "not run"
              ? "not run"
              : `${report.power_tests_passed}/${report.power_tests_total}`
          }
          tone={report.power_tests_total > 0 && report.power_tests_passed === report.power_tests_total ? "wire" : "neutral"}
        />
        <Cell
          label="DFM warnings"
          value={
            report.manufacturing_status === "not run" ? "not run" : String(report.manufacturing_warnings)
          }
          tone={report.manufacturing_warnings > 0 ? "copper" : "wire"}
        />
        <Cell label="Schematic ERC" value={report.erc_status} tone={statusTone(report.erc_status)} />
        <Cell label="Board DRC" value={report.drc_status} tone={statusTone(report.drc_status)} />
        <Cell
          label="Critical issues"
          value={String(report.critical_issues)}
          tone={report.critical_issues > 0 ? "brick" : "wire"}
        />
      </dl>

      {/* ERC/DRC are absolute, gates are relative to the baseline: both can be
          true at once, and pretending otherwise would be the dishonest part. */}
      {(report.erc_status.startsWith("failed") || report.drc_status.startsWith("failed")) &&
        report.release_status === "ready for engineering review" && (
          <p className="mt-2 min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
            The gates passed while ERC or DRC still reports errors: gates compare against the baseline this
            project already had, so violations that were there before the run are not blamed on it. The absolute
            counts above are the project&apos;s own state.
          </p>
        )}

      {/* A "ready" verdict next to unmet requirements is not a contradiction —
          the gates and the traceability count measure different things. */}
      {report.release_status === "ready for engineering review" &&
        report.requirements_total > 0 &&
        report.requirements_satisfied < report.requirements_total && (
          <p className="mt-2 min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
            A requirement counts as met only where the verification stage recorded evidence against it. The
            release gate asks whether every stage cleared its own check, not whether every requirement was
            traced — so these two can disagree, and both are shown rather than reconciled.
          </p>
        )}

      {report.open_critical_findings.length > 0 && (
        <section className="mt-5">
          <h3 className="eyebrow">Open critical findings</h3>
          <ul className="mt-2 divide-y divide-rule/60 border-y border-rule/60">
            {report.open_critical_findings.map((finding, index) => (
              <li key={index} className="min-w-0 py-2">
                <p className="min-w-0 wrap-any text-[0.8125rem] font-medium leading-snug text-ink">
                  {finding.rule}
                </p>
                <p className="mt-0.5 min-w-0 wrap-any font-mono text-[0.75rem] leading-snug text-muted">
                  {String(finding.actual)} — expected {String(finding.expected)} · {finding.kicad_object}
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {run.answers.length > 0 && (
        <section className="mt-5">
          <h3 className="eyebrow">Answers already given</h3>
          <ul className="mt-2 space-y-1.5">
            {run.answers.map((item, index) => (
              <li key={index} className="min-w-0 wrap-any text-[0.8125rem] leading-snug text-muted">
                {index + 1}. {item}
              </li>
            ))}
          </ul>
        </section>
      )}

      {needsHuman && (
        <section className="mt-5 border-t border-rule pt-4">
          <h3 className="eyebrow">Answer it and hand it back</h3>
          <p className="mt-1.5 min-w-0 wrap-any text-[0.8125rem] leading-snug text-muted">
            Your answer is appended to the request and the whole run starts again from the project manager, with
            the answer on the record.
          </p>
          <textarea
            className="field mt-2 min-h-[4.5rem] resize-y font-sans leading-snug"
            value={answer}
            disabled={busy}
            onChange={(event) => setAnswer(event.target.value)}
            placeholder="e.g. Use 3.3 V logic and the SHT31 on the existing I2C bus."
          />
          <button
            type="button"
            className="btn mt-2"
            disabled={busy || answer.trim().length === 0}
            onClick={() => {
              onAnswer(answer.trim());
              setAnswer("");
            }}
          >
            {busy ? "Sending…" : "Answer and re-run"}
          </button>
        </section>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-rule pt-4">
        <button type="button" className="btn-ghost" onClick={onNewRun}>
          Brief a new run
        </button>
        <p className="min-w-0 wrap-any font-mono text-[0.6875rem] leading-snug text-muted">
          run {report.run_id} · {run.runner}
        </p>
      </div>
    </Panel>
  );
}

/** The tool statuses are prose; only their leading word is safe to colour by. */
function statusTone(status: string): "wire" | "brick" | "copper" | "neutral" {
  if (status.startsWith("passed")) return "wire";
  if (status.startsWith("failed")) return status.includes("baseline") ? "copper" : "brick";
  return "neutral";
}

function Cell({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "wire" | "brick" | "copper" | "neutral";
}) {
  const tones = {
    neutral: "text-ink",
    wire: "text-wire",
    brick: "text-brick",
    copper: "text-copper",
  };
  return (
    <div className="min-w-0 bg-sheet px-3 py-2.5">
      <dt className="eyebrow">{label}</dt>
      <dd className={`mt-1 min-w-0 wrap-any font-mono text-[0.75rem] leading-snug ${tones[tone]}`} title={value}>
        {value}
      </dd>
    </div>
  );
}
