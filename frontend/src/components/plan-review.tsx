"use client";

import { ActionPlan, PlanResponse } from "@/lib/api";
import { ActionWire } from "./action-wire";
import { Bullets, Chip, DataCell, Notice, Panel } from "./ui";

export function PlanReview({
  plan,
  meta,
  busy,
  onApprove,
  onBack,
}: {
  plan: ActionPlan;
  meta: PlanResponse | null;
  busy: boolean;
  onApprove: () => void;
  onBack: () => void;
}) {
  const problems = meta?.problems ?? [];
  const executable = Boolean(meta?.executable);

  return (
    <Panel
      eyebrow="Step 03"
      title="Review before anything is written"
      aside={
        <Chip tone="wire">
          {plan.actions.length} {plan.actions.length === 1 ? "action" : "actions"}
        </Chip>
      }
    >
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-b border-rule pb-4 sm:grid-cols-4">
        <DataCell label="Interface" value={plan.protocol} />
        <DataCell label="Logic" value={plan.logic_voltage} />
        <DataCell label="Planned by" value={meta?.source ?? "rules"} />
        <DataCell label="Protected" value={plan.protected_objects.join(", ") || "nothing"} />
      </dl>

      <ol className="mt-1">
        {plan.actions.map((action, index) => (
          <ActionWire key={action.id} action={action} index={index} />
        ))}
      </ol>

      {plan.actions.length === 0 && (
        <p className="min-w-0 wrap-any py-4 text-[0.8125rem] leading-snug text-muted">
          The planner found nothing to change for this instruction.
        </p>
      )}

      <Bullets title="Assumptions" items={plan.assumptions} />
      <Bullets title="Warnings" items={plan.warnings} tone="copper" />
      <Bullets title="Blocking problems" items={problems} tone="brick" />

      {!executable && (
        <div className="mt-4">
          <Notice tone="brick" title="Can't run this plan">
            {problems.length > 0
              ? "Resolve what's listed above, then plan again."
              : "The planner produced no runnable actions. Adjust the selection or the instruction and plan again."}
          </Notice>
        </div>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-rule pt-4">
        <button type="button" className="btn" disabled={busy || !executable} onClick={onApprove}>
          {busy ? "Applying…" : "Approve and apply"}
        </button>
        <button type="button" className="btn-ghost" disabled={busy} onClick={onBack}>
          Back to describe
        </button>
        <p className="min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
          A checkpoint is taken first. If ERC comes back worse, the change is reverted.
        </p>
      </div>
    </Panel>
  );
}
