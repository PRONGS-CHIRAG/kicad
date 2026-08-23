"use client";

import { AGENT_BY_ID, TeamEvent, TeamFlow, TeamStageId } from "@/lib/team";
import { Chip, Panel } from "./ui";

/**
 * The run in the order it happened, straight off the append-only evidence file.
 * The flow rail above says where the work stands; this says how it got there —
 * the same stage appearing twice is a return trip, and it reads as one.
 *
 * Routing decisions are listed separately because the orchestrator writes them
 * once, at the end, so there is no honest way to interleave them by time.
 */
export function TeamTrace({
  flow,
  events,
  onSelect,
}: {
  flow: TeamFlow;
  events: TeamEvent[];
  onSelect: (stage: TeamStageId) => void;
}) {
  const entries = [...flow.trace].reverse();

  return (
    <Panel
      eyebrow="Evidence"
      title="What happened, in order"
      aside={<Chip>{flow.trace.length} records</Chip>}
    >
      {entries.length === 0 ? (
        <p className="min-w-0 wrap-any text-[0.8125rem] leading-snug text-muted">
          Nothing recorded yet. Each agent and each gate appends one record as it finishes.
        </p>
      ) : (
        <ol className="divide-y divide-rule/60 border-y border-rule/60">
          {entries.map((entry) => {
            const gate = entry.kind === "gate";
            const passed = entry.record.result === "passed";
            const errors = entry.findings.filter((finding) => finding.severity === "error").length;
            return (
              <li key={entry.seq}>
                <button
                  type="button"
                  onClick={() => onSelect(entry.stage)}
                  className="flex w-full min-w-0 items-start gap-2.5 py-2 text-left hover:bg-paper/70"
                >
                  <span className="shrink-0 font-mono text-[0.6875rem] leading-5 text-muted">
                    {String(entry.seq + 1).padStart(2, "0")}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                      <span className="min-w-0 wrap-any text-[0.8125rem] font-medium leading-snug text-ink">
                        {AGENT_BY_ID[entry.stage].name}
                      </span>
                      <span className="font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-muted">
                        {gate ? "gate" : "agent"}
                      </span>
                      {entry.attempt > 1 && (
                        <span className="chip border-brick/35 bg-brick-soft !py-0 text-brick">
                          pass {entry.attempt}
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-[0.6875rem] leading-snug">
                      <span className="min-w-0 wrap-any text-muted">{entry.record.tool}</span>
                      <span
                        className={
                          gate ? (passed ? "text-wire" : "text-brick") : "text-muted"
                        }
                      >
                        · {entry.record.result}
                      </span>
                      {errors > 0 && (
                        <span className="text-brick">
                          · {errors} {errors === 1 ? "error" : "errors"}
                        </span>
                      )}
                      <span className="min-w-0 wrap-any text-muted">
                        · {entry.record.timestamp.slice(11, 19)}
                      </span>
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      )}

      {events.length > 0 && (
        <div className="mt-4 border-t border-rule pt-4">
          <h3 className="eyebrow">Routing decisions</h3>
          <ul className="mt-2 space-y-1.5">
            {events.map((event, index) => (
              <li
                key={index}
                className="min-w-0 wrap-any text-[0.8125rem] leading-snug text-muted"
              >
                {describeEvent(event)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

function name(stage: string | undefined): string {
  if (!stage) return "an agent";
  return AGENT_BY_ID[stage as TeamStageId]?.name ?? stage;
}

/** The orchestrator's own event names, said plainly. */
function describeEvent(event: TeamEvent): string {
  switch (event.event) {
    case "project_manager_workflow_accepted":
      return event.workflow?.length
        ? `The project manager's stage order was accepted: ${event.workflow.join(" → ")}.`
        : "The project manager proposed no stage order, so the canonical one was used.";
    case "invalid_project_manager_workflow":
      return `The project manager proposed an out-of-order workflow (${
        event.workflow?.join(" → ") || "empty"
      }); the canonical order was enforced instead.`;
    case "route_failure":
      return `${name(event.from)} failed its gate, so the work went back to ${name(event.to)} — and every stage after it is replayed.`;
    case "return_trip_cap":
      return `${name(event.agent)} was sent back twice and still did not clear its gate, so the run stops for human review.`;
    case "read_only_violation":
      return `${name(event.stage)} is a read-only agent and tried to change the design. The run was failed outright.`;
    case "human_review_answer":
      return "A human answered the open question; the run was restarted with that answer folded into the request.";
    default:
      return event.event.replace(/_/g, " ");
  }
}
