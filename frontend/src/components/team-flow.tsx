"use client";

import {
  AGENT_BY_ID,
  PARALLEL_PAIR,
  StagePhase,
  TEAM_STAGE_IDS,
  TeamFlow,
  TeamStageId,
} from "@/lib/team";
import { Chip, Panel } from "./ui";

/**
 * The run, drawn as it actually executes: ten agents in a fixed order, each one
 * followed by the deterministic gate that has to accept its output before the
 * next agent starts. Layout and simulation are drawn as a fork because the
 * backend really does run them at the same time.
 *
 * Everything here is read out of the evidence the orchestrator appends as it
 * goes. A stage with no records yet says so.
 */

const PHASE: Record<StagePhase, { label: string; tone: "neutral" | "wire" | "brick" | "copper" }> = {
  waiting: { label: "waiting", tone: "neutral" },
  running: { label: "working", tone: "copper" },
  gating: { label: "at the gate", tone: "copper" },
  passed: { label: "accepted", tone: "wire" },
  failed: { label: "sent back", tone: "brick" },
  unresolved: { label: "no gate recorded", tone: "copper" },
};

export function TeamFlowRail({
  flow,
  status,
  parallel,
  resumed,
  selected,
  onSelect,
}: {
  flow: TeamFlow;
  status: "running" | "completed" | "failed";
  parallel: boolean;
  /** How many times a human answered and handed this run back. */
  resumed: number;
  selected: TeamStageId | null;
  onSelect: (stage: TeamStageId) => void;
}) {
  const sequence = TEAM_STAGE_IDS.filter((id) => !PARALLEL_PAIR.includes(id));
  const done = TEAM_STAGE_IDS.filter((id) => flow.stages[id].phase === "passed").length;
  const trips = Object.values(flow.returnTrips).reduce((total, count) => total + (count ?? 0), 0);

  return (
    <Panel
      eyebrow="Step 02"
      title="The run, stage by stage"
      aside={
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          <Chip tone={done === TEAM_STAGE_IDS.length ? "wire" : "neutral"}>
            {done}/{TEAM_STAGE_IDS.length} gates passed
          </Chip>
          {trips > 0 && (
            <Chip tone="brick">
              {trips} return {trips === 1 ? "trip" : "trips"}
            </Chip>
          )}
        </div>
      }
    >
      <ol className="min-w-0">
        {sequence.map((id) => {
          const forkHere = parallel && TEAM_STAGE_IDS[TEAM_STAGE_IDS.indexOf(id) + 1] === "pcb_layout";
          return (
            <li key={id} className="min-w-0">
              <StageRow
                stage={id}
                number={TEAM_STAGE_IDS.indexOf(id) + 1}
                flow={flow}
                selected={selected === id}
                onSelect={onSelect}
                lastOfBranch={false}
              />
              {forkHere && (
                <ParallelGroup flow={flow} selected={selected} onSelect={onSelect} parallel={parallel} />
              )}
            </li>
          );
        })}
        {!parallel &&
          PARALLEL_PAIR.map((id) => (
            <li key={id} className="min-w-0">
              <StageRow
                stage={id}
                number={TEAM_STAGE_IDS.indexOf(id) + 1}
                flow={flow}
                selected={selected === id}
                onSelect={onSelect}
                lastOfBranch={false}
              />
            </li>
          ))}
      </ol>

      {flow.routing && (
        <p className="mt-3 min-w-0 wrap-any border-t border-rule pt-3 text-[0.75rem] leading-snug text-copper">
          A gate rejected its stage. The orchestrator is picking which agent owns the fix from the finding
          itself — every stage after that one is replayed once it lands.
        </p>
      )}

      {status === "running" && !flow.routing && flow.inFlight.length === 0 && (
        <p className="mt-3 min-w-0 wrap-any border-t border-rule pt-3 text-[0.75rem] leading-snug text-muted">
          Waiting on the first agent to report.
        </p>
      )}

      {resumed > 0 && (
        <p className="mt-3 min-w-0 wrap-any border-t border-rule pt-3 text-[0.75rem] leading-snug text-muted">
          Handed back {resumed === 1 ? "once" : `${resumed} times`} with an answer. A re-run appends to the same
          evidence file rather than starting a new one, so the pass counts above include the attempts made
          before the answer.
        </p>
      )}

      <p className="mt-3 min-w-0 wrap-any border-t border-rule pt-3 text-[0.75rem] leading-snug text-muted">
        Agents only propose. Each gate is a deterministic check — the schematic and layout gates are KiCAD&apos;s
        own ERC and DRC — and a rejected stage is rolled back from a checkpoint before the work is handed on.
      </p>
    </Panel>
  );
}

/** Layout and simulation, drawn as the concurrent pair the backend runs. */
function ParallelGroup({
  flow,
  selected,
  parallel,
  onSelect,
}: {
  flow: TeamFlow;
  selected: TeamStageId | null;
  parallel: boolean;
  onSelect: (stage: TeamStageId) => void;
}) {
  if (!parallel) return null;
  return (
    <div className="relative min-w-0 pl-[1.5625rem]">
      {/* The trunk continues behind the fork so the two branches read as one step. */}
      <span aria-hidden className="absolute left-[0.375rem] top-0 h-full w-[1.5px] bg-rule" />
      <div className="min-w-0 border-l-[1.5px] border-dashed border-wire/50 pl-3">
        <p className="eyebrow py-2">Concurrent — one step, two agents</p>
        <div className="min-w-0 divide-y divide-rule/60 border-y border-rule/60">
          {PARALLEL_PAIR.map((id) => (
            <StageRow
              key={id}
              stage={id}
              number={TEAM_STAGE_IDS.indexOf(id) + 1}
              flow={flow}
              selected={selected === id}
              onSelect={onSelect}
              lastOfBranch
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function StageRow({
  stage,
  number,
  flow,
  selected,
  lastOfBranch,
  onSelect,
}: {
  stage: TeamStageId;
  number: number;
  flow: TeamFlow;
  selected: boolean;
  /** Inside the fork the trunk is drawn by the group, not the row. */
  lastOfBranch: boolean;
  onSelect: (stage: TeamStageId) => void;
}) {
  const agent = AGENT_BY_ID[stage];
  const state = flow.stages[stage];
  const phase = PHASE[state.phase];
  const attempt = state.attempts[state.attempts.length - 1];
  const trips = flow.returnTrips[stage] ?? 0;
  const errors = attempt?.gateFindings.filter((finding) => finding.severity === "error").length ?? 0;
  const gateResult = attempt?.gate?.result ?? null;
  const live = flow.inFlight.includes(stage);
  const last = number === TEAM_STAGE_IDS.length;

  return (
    <button
      type="button"
      onClick={() => onSelect(stage)}
      aria-pressed={selected}
      className={`relative flex w-full min-w-0 items-start gap-3 py-2.5 text-left transition-colors ${
        selected ? "bg-wire-soft/60" : "hover:bg-paper/70"
      }`}
    >
      {/* The wire down the gutter: teal behind stages the run has cleared. */}
      {!lastOfBranch && !last && (
        <span
          aria-hidden
          className={`absolute left-[0.375rem] top-[1.125rem] h-full w-[1.5px] ${
            state.phase === "passed" ? "bg-wire" : "bg-rule"
          }`}
        />
      )}

      <span className="relative z-10 mt-[0.3125rem] shrink-0">
        <FlowNode phase={state.phase} live={live} />
      </span>

      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="font-mono text-[0.6875rem] text-muted">
            {String(number).padStart(2, "0")}
          </span>
          <span className="min-w-0 wrap-any text-[0.875rem] font-semibold leading-tight tracking-[-0.012em] text-ink">
            {agent.name}
          </span>
          {agent.mutates && (
            <span className="chip border-copper-line/45 bg-copper-soft !py-0 text-copper">writes files</span>
          )}
          {trips > 0 && (
            <span className="chip border-brick/35 bg-brick-soft !py-0 text-brick">
              attempt {state.attempts.length}
            </span>
          )}
        </span>

        <span className="mt-1 block min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
          {agent.produces}
        </span>

        {/* agent → gate → verdict, spelled out on one line. */}
        <span className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[0.6875rem] leading-none">
          <span className={`${phase.tone === "wire" ? "text-wire" : phase.tone === "brick" ? "text-brick" : phase.tone === "copper" ? "text-copper" : "text-muted"}`}>
            {phase.label}
          </span>
          <span aria-hidden className="text-rule">
            →
          </span>
          <span className="min-w-0 wrap-any text-muted">gate: {agent.gate}</span>
          {gateResult && (
            <span className={gateResult === "passed" ? "text-wire" : "text-brick"}>· {gateResult}</span>
          )}
          {errors > 0 && (
            <span className="text-brick">
              · {errors} {errors === 1 ? "finding" : "findings"}
            </span>
          )}
        </span>
      </span>
    </button>
  );
}

/** A solder junction on the wire: hollow until the stage has cleared its gate. */
function FlowNode({ phase, live }: { phase: StagePhase; live: boolean }) {
  const base = "block h-[13px] w-[13px] rounded-full border-[3px]";
  if (phase === "passed") return <span aria-hidden className={`${base} border-wire bg-wire`} />;
  if (phase === "failed") return <span aria-hidden className={`${base} border-brick bg-brick`} />;
  if (phase === "gating" || phase === "unresolved")
    return <span aria-hidden className={`${base} border-copper-line bg-copper-soft`} />;
  if (phase === "running" || live)
    return (
      <span aria-hidden className={`${base} animate-pulse border-copper-line bg-paper`} />
    );
  return <span aria-hidden className={`${base} border-rule bg-paper`} />;
}
