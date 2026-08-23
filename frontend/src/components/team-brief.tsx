"use client";

import { HealthResponse, SessionResponse } from "@/lib/api";
import { TEAM_AGENTS } from "@/lib/team";
import { Chip, Notice, Panel, Segmented } from "./ui";

/**
 * The brief handed to the team. One prose request, and the choice of who
 * actually does the thinking — which is the only decision here that costs
 * money, so it is stated in full next to the button rather than buried.
 */
export function TeamBrief({
  session,
  health,
  request,
  runner,
  busy,
  onRequest,
  onRunner,
  onStart,
}: {
  session: SessionResponse;
  health: HealthResponse | null;
  request: string;
  runner: "devin" | "stub";
  busy: boolean;
  onRequest: (next: string) => void;
  onRunner: (next: "devin" | "stub") => void;
  onStart: () => void;
}) {
  const live = runner === "devin";
  const stages = TEAM_AGENTS.length;

  return (
    <Panel
      eyebrow="Step 01"
      title="Brief the team"
      aside={<Chip>{stages} agents</Chip>}
    >
      <p className="min-w-0 wrap-any text-[0.8125rem] leading-snug text-muted">
        One request, worked through {stages} stages in a fixed order. Every stage&apos;s output is checked by a
        deterministic tool before the next one starts, and a failed check sends the work back to whichever agent
        is responsible.
      </p>

      <label className="mt-4 block">
        <span className="eyebrow">What should the board do</span>
        <textarea
          className="field mt-2 min-h-[5.5rem] resize-y font-sans leading-snug"
          value={request}
          disabled={busy}
          onChange={(event) => onRequest(event.target.value)}
          placeholder="Monitor temperature over I2C on a USB-C powered ESP32 board."
        />
      </label>

      <div className="mt-4 flex flex-wrap items-end justify-between gap-x-4 gap-y-3 border-t border-rule pt-4">
        <div className="min-w-0">
          <span className="eyebrow">Who runs the agents</span>
          <div className="mt-2">
            <Segmented
              label="Team runner"
              value={runner}
              onChange={onRunner}
              disabled={busy}
              options={[
                { value: "stub", label: "Stub" },
                { value: "devin", label: "Devin" },
              ]}
            />
          </div>
        </div>
        <dl className="min-w-0 text-right">
          <dt className="eyebrow">Manufacturer profile</dt>
          <dd className="mt-1 min-w-0 wrap-any font-mono text-[0.75rem] text-ink">generic_two_layer</dd>
        </dl>
      </div>

      <div className="mt-3">
        {live ? (
          <Notice tone="copper" title="This spends real Devin sessions">
            Each stage opens its own Devin session against the key in <span className="font-mono">.env</span>, capped
            at {" "}3 ACU apiece, so a full pass is up to {stages} sessions. Expect minutes, not seconds.
            {health?.team_runner === "devin" && " This is also the backend's own default."}
          </Notice>
        ) : (
          <Notice tone="neutral" title="Offline and free">
            The stub runner is deterministic and opens no sessions. It is a wiring test, not a demo: its agents
            return near-empty output, so the architecture gate fails and the run ends at{" "}
            <span className="font-mono">needs_human_review</span> after two return trips. That path is worth
            watching — it is exactly what a real failure looks like.
          </Notice>
        )}
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-rule pt-4">
        <button type="button" className="btn" disabled={busy || request.trim().length === 0} onClick={onStart}>
          {busy ? "Starting…" : `Start the ${stages}-agent run`}
        </button>
        <p className="min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
          Runs on a fresh copy of <span className="font-mono">{session.project}</span>. Nothing touches the
          project you opened.
        </p>
      </div>
    </Panel>
  );
}
