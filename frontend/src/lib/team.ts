import { request } from "./api";

/**
 * The ten-agent lane. Everything here mirrors the backend it describes:
 * `TEAM_AGENTS` is `app/team/registry.py`'s roster in `CANONICAL_ORDER`, and
 * `deriveFlow` reconstructs a run's shape from the append-only evidence the
 * orchestrator writes as it goes. Nothing is inferred that the backend does not
 * actually report — a stage the evidence hasn't reached yet is shown as waiting,
 * not guessed at.
 */

export const CANONICAL_STAGE_IDS = [
  "project_manager",
  "requirements",
  "architecture",
  "components",
  "schematic_design",
  "pcb_layout",
  "simulation",
  "verification",
  "manufacturing",
  "qa_release",
] as const;

/**
 * The repair stage owns no place in the canonical order — it runs when another
 * stage's gate rejects it, corrects that stage's own document, and is gated by
 * the very same check. So it is a stage the run can show, but never one of the
 * ten the run walks through: every count and every "what comes next" below is
 * over `CANONICAL_STAGE_IDS`, and only the lane and the trace know about this.
 */
export const REPAIR_STAGE_ID = "repair";

export const TEAM_STAGE_IDS = [...CANONICAL_STAGE_IDS, REPAIR_STAGE_ID] as const;

export type TeamStageId = (typeof TEAM_STAGE_IDS)[number];
export type CanonicalStageId = (typeof CANONICAL_STAGE_IDS)[number];

export type TeamAgent = {
  id: TeamStageId;
  /** Roster name, as the backend registry names it. */
  name: string;
  role: string;
  /** What this agent is asked to produce, in one line. */
  produces: string;
  /** The Pydantic model its output must parse into. */
  outputModel: string;
  /** Prior stage outputs it is handed — and nothing else. */
  reads: TeamStageId[];
  /** The deterministic tool that grounds its output at the gate. */
  gate: string;
  /** Whether this agent is allowed to change design files. */
  mutates: boolean;
};

export const TEAM_AGENTS: TeamAgent[] = [
  {
    id: "project_manager",
    name: "Project Manager",
    role: "Hardware project manager",
    produces: "The goal in one line, and the stage order to work in",
    outputModel: "PMPlan",
    reads: [],
    gate: "orchestrator",
    mutates: false,
  },
  {
    id: "requirements",
    name: "Requirements",
    role: "Hardware requirements engineer",
    produces: "Numbered, testable requirements with units and a source",
    outputModel: "RequirementsDoc",
    reads: ["project_manager"],
    gate: "requirements parser",
    mutates: false,
  },
  {
    id: "architecture",
    name: "System Architect",
    role: "Electronics system architect",
    produces: "Functional blocks, their connections, and which requirement each serves",
    outputModel: "Architecture",
    reads: ["requirements"],
    gate: "architecture checker",
    mutates: false,
  },
  {
    id: "components",
    name: "Component Engineer",
    role: "Component/application engineer",
    produces: "Real parts with symbols, footprints and the constraints they were checked against",
    outputModel: "ComponentSelection",
    reads: ["requirements", "architecture"],
    gate: "component checker",
    mutates: false,
  },
  {
    id: "schematic_design",
    name: "Schematic Design",
    role: "Electronics design engineer",
    produces: "Connection intents: pin to pin, pin to net, pull-ups",
    outputModel: "SchematicIntents",
    reads: ["requirements", "architecture", "components"],
    gate: "decision/state diff + KiCAD ERC",
    mutates: true,
  },
  {
    id: "pcb_layout",
    name: "PCB Layout",
    role: "PCB layout engineer",
    produces: "Footprint placements, critical nets, and what is left unrouted",
    outputModel: "LayoutProposal",
    reads: ["schematic_design"],
    gate: "board writer + KiCAD DRC",
    mutates: true,
  },
  {
    id: "simulation",
    name: "Simulation",
    role: "Circuit simulation engineer",
    produces: "Rail and current-margin tests, by closed-form arithmetic",
    outputModel: "SimulationReport",
    reads: ["schematic_design", "components"],
    gate: "simulation checker",
    mutates: false,
  },
  {
    id: "verification",
    name: "Verification",
    role: "Hardware verification engineer",
    produces: "A pass/fail/unverified outcome per requirement, with its evidence",
    outputModel: "VerificationReport",
    reads: ["schematic_design", "pcb_layout", "simulation"],
    gate: "verification checker",
    mutates: false,
  },
  {
    id: "manufacturing",
    name: "Manufacturing",
    role: "DFM/DFA engineer",
    produces: "DFM findings against the named manufacturer profile",
    outputModel: "ManufacturingReport",
    reads: ["pcb_layout", "components", "verification"],
    gate: "manufacturer checker",
    mutates: false,
  },
  {
    id: "qa_release",
    name: "QA and Release",
    role: "Hardware QA/release engineer",
    produces: "A release record: checklist, files, and a hash of what shipped",
    outputModel: "ReleaseRecord",
    reads: [
      "requirements",
      "architecture",
      "components",
      "schematic_design",
      "pcb_layout",
      "simulation",
      "verification",
      "manufacturing",
    ],
    gate: "release checker",
    mutates: false,
  },
];

export const REPAIR_AGENT: TeamAgent = {
  id: REPAIR_STAGE_ID,
  name: "Design Repair",
  role: "Design repair engineer",
  produces: "The rejected document, corrected — nothing else changed",
  outputModel: "the rejected stage's own model",
  reads: [],
  gate: "the rejected stage's own gate",
  mutates: false,
};

export const AGENT_BY_ID: Record<TeamStageId, TeamAgent> = Object.fromEntries(
  [...TEAM_AGENTS, REPAIR_AGENT].map((agent) => [agent.id, agent]),
) as Record<TeamStageId, TeamAgent>;

/** These two overlap when the backend runs with `team_parallel` on. */
export const PARALLEL_PAIR: CanonicalStageId[] = ["pcb_layout", "simulation"];

export type CheckFinding = {
  rule: string;
  actual: unknown;
  expected: unknown;
  kicad_object: string;
  evidence_source: string;
  severity: "error" | "warning" | "info";
};

export type TeamEvidenceRecord = {
  check: string;
  tool: string;
  project_version: string;
  timestamp: string;
  result: string;
  findings: unknown[];
  runner: string | null;
  session_url: string | null;
  status: string | null;
  acus_consumed: number;
  duration_seconds: number;
};

export type TeamAgentResult = {
  task_id: string;
  agent: string;
  status: string;
  output: unknown;
  output_artifacts: string[];
  unresolved_questions: string[];
  session_url: string | null;
  acus_consumed: number;
  duration_seconds: number;
  runner: string;
};

export type TeamRunReport = {
  run_id: string;
  request: string;
  project: {
    project_id: string;
    manufacturer_profile: Record<string, unknown>;
    design_context: {
      components: { reference: string; value: string; lib_id: string }[];
      nets: { name: string; pins: string[] }[];
      erc_baseline: { errors: number; warnings: number };
      drc_baseline: { errors: number; warnings: number } | null;
      board: { width_mm: number; height_mm: number } | null;
    } | null;
  };
  per_stage_results: Partial<Record<TeamStageId, TeamAgentResult>>;
  requirements_satisfied: number;
  requirements_total: number;
  requirements_status: string;
  erc_status: string;
  drc_status: string;
  power_tests_passed: number;
  power_tests_total: number;
  power_tests_status: string;
  manufacturing_warnings: number;
  manufacturing_status: string;
  critical_issues: number;
  open_critical_findings: CheckFinding[];
  release_status: string;
  summary: string;
};

/** Orchestrator decisions. Written once, when the run ends. */
export type TeamEvent = {
  event: string;
  from?: string;
  to?: string;
  agent?: string;
  stage?: string;
  workflow?: string[];
  findings?: CheckFinding[];
};

export type TeamRunStatus = {
  run_id: string;
  status: "running" | "completed" | "failed";
  runner: string;
  project: string;
  request?: string;
  manufacturer_profile?: string;
  session_id?: string;
  answers: string[];
  error?: string;
  events: TeamEvent[];
  report: TeamRunReport | null;
};

export const teamApi = {
  start: (body: { project: string; request: string; runner: "devin" | "stub" }) =>
    request<{ run_id: string; status: string }>("/api/team/runs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  status: (runId: string) => request<TeamRunStatus>(`/api/team/runs/${runId}`),
  evidence: (runId: string) =>
    request<{ run_id: string; status: string; records: TeamEvidenceRecord[] }>(
      `/api/team/runs/${runId}/evidence`,
    ),
  answer: (runId: string, answer: string) =>
    request<{ run_id: string; accepted: boolean; answers: string[] }>(`/api/team/runs/${runId}/answer`, {
      method: "POST",
      body: JSON.stringify({ answer }),
    }),
};

/* -------------------------------------------------------------------------- */

export type StagePhase = "waiting" | "running" | "gating" | "passed" | "failed" | "unresolved";

/** One pass of an agent through its stage. A second attempt means it was sent back. */
export type StageAttempt = {
  agent: TeamEvidenceRecord | null;
  gate: TeamEvidenceRecord | null;
  gateFindings: CheckFinding[];
};

export type StageState = {
  id: TeamStageId;
  phase: StagePhase;
  attempts: StageAttempt[];
  /** Set when the stage ended without a gate record the evidence could carry. */
  unresolvedDetail: string | null;
};

export type TraceEntry = {
  seq: number;
  stage: TeamStageId;
  kind: "agent" | "gate";
  /** Which pass through this stage, 1-based. */
  attempt: number;
  record: TeamEvidenceRecord;
  findings: CheckFinding[];
};

export type TeamFlow = {
  stages: Record<TeamStageId, StageState>;
  /** Stages the repair agent corrected, in the order it corrected them. */
  repaired: CanonicalStageId[];
  trace: TraceEntry[];
  /** Stages an agent or gate is working on right now. Empty once the run ends. */
  inFlight: TeamStageId[];
  /** True while the orchestrator is deciding where a failed gate goes back to. */
  routing: boolean;
  /** How many times each agent was handed the work again. */
  returnTrips: Partial<Record<TeamStageId, number>>;
};

const AGENT_CHECK = /^team agent (.+)$/;
const GATE_CHECK = /^team (.+) gate$/;

function isStage(value: string): value is TeamStageId {
  return (TEAM_STAGE_IDS as readonly string[]).includes(value);
}

function asFindings(values: unknown[]): CheckFinding[] {
  return values.filter(
    (value): value is CheckFinding =>
      typeof value === "object" && value !== null && "rule" in value && "severity" in value,
  );
}

/** The stage(s) the orchestrator dequeues after `stage` clears its gate. */
function successors(stage: TeamStageId, parallel: boolean): TeamStageId[] {
  const index = (CANONICAL_STAGE_IDS as readonly string[]).indexOf(stage);
  const next = CANONICAL_STAGE_IDS[index + 1];
  if (next === undefined) return [];
  if (next === "pcb_layout" && parallel) return [...PARALLEL_PAIR];
  return [next];
}

export function deriveFlow(
  records: TeamEvidenceRecord[],
  runStatus: TeamRunStatus["status"],
  report: TeamRunReport | null,
  parallel: boolean,
): TeamFlow {
  const stages = {} as Record<TeamStageId, StageState>;
  for (const id of TEAM_STAGE_IDS) {
    stages[id] = { id, phase: "waiting", attempts: [], unresolvedDetail: null };
  }
  const trace: TraceEntry[] = [];
  const repaired: CanonicalStageId[] = [];

  /** The trace entry for each stage's currently open pass, so a merge updates it. */
  const openEntry = new Map<TeamStageId, TraceEntry>();

  /**
   * The repair stage's records name itself, not the stage it was called in for.
   * The orchestrator only ever calls it straight after a gate rejects a stage,
   * so the stage it is correcting is the one that gate belonged to.
   */

  for (const record of records) {
    const agentMatch = AGENT_CHECK.exec(record.check);
    const gateMatch = GATE_CHECK.exec(record.check);
    const written = agentMatch?.[1] ?? gateMatch?.[1] ?? "";
    /**
     * The repair stage writes its records as `repair-<stage>`: the gate that
     * judges a correction is the corrected stage's own gate, so without the
     * name in the record a repaired pass would be indistinguishable from one
     * that passed first try.
     */
    const corrected = written.startsWith(`${REPAIR_STAGE_ID}-`)
      ? written.slice(REPAIR_STAGE_ID.length + 1)
      : null;
    const name = corrected === null ? written : REPAIR_STAGE_ID;
    if (!isStage(name)) continue;
    if (corrected !== null && !isStage(corrected)) continue;
    if (gateMatch && corrected !== null && record.result === "passed") {
      repaired.push(corrected as CanonicalStageId);
    }
    const stage = stages[name];
    const findings = asFindings(record.findings);

    if (agentMatch) {
      /**
       * The runner and the orchestrator each append a record for the same pass.
       * Adjacency cannot tell those apart from a return trip: at the parallel
       * fork the sibling's records land in between, so `simulation`'s two
       * records are separated by all of `pcb_layout`'s. The gate can tell them
       * apart — routing only ever happens *after* a gate, so an agent record
       * opens a new pass only when the last one has already been gated.
       */
      const open = stage.attempts[stage.attempts.length - 1];
      if (open && open.gate === null) {
        open.agent = open.agent ? { ...open.agent, ...record } : record;
        const entry = openEntry.get(name);
        if (entry) {
          entry.record = open.agent;
          entry.findings = findings.length > 0 ? findings : entry.findings;
        }
        continue;
      }
      stage.attempts.push({ agent: record, gate: null, gateFindings: [] });
      const entry: TraceEntry = {
        seq: trace.length,
        stage: name,
        kind: "agent",
        attempt: stage.attempts.length,
        record,
        findings,
      };
      trace.push(entry);
      openEntry.set(name, entry);
      continue;
    }
    // A gate with no preceding agent record cannot happen through the API, but
    // an attempt is created rather than dropping the gate on the floor.
    if (stage.attempts.length === 0) stage.attempts.push({ agent: null, gate: null, gateFindings: [] });
    const attempt = stage.attempts[stage.attempts.length - 1];
    attempt.gate = record;
    attempt.gateFindings = findings;
    trace.push({ seq: trace.length, stage: name, kind: "gate", attempt: stage.attempts.length, record, findings });
  }

  for (const id of TEAM_STAGE_IDS) {
    const stage = stages[id];
    const last = stage.attempts[stage.attempts.length - 1];
    if (last === undefined) continue;
    if (last.gate === null) {
      stage.phase = "gating";
      continue;
    }
    stage.phase = last.gate.result === "passed" ? "passed" : "failed";
  }

  /**
   * A repaired stage did clear its own gate - the orchestrator runs that same
   * check over the corrected document before letting the run continue - so the
   * lane shows it as passed. Its rejected pass stays in the attempts and in the
   * trace, which is where "it took a repair" is legible.
   */
  for (const id of repaired) stages[id].phase = "passed";

  /**
   * The project manager's gate is the one gate that carries no evidence record
   * of its own, and the orchestrator returns the moment it fails — so a record
   * from any later stage is proof that it passed.
   */
  const pm = stages.project_manager;
  const firstPass = pm.attempts[0];
  if (firstPass && firstPass.gate === null) {
    if (trace.some((entry) => entry.stage !== "project_manager")) {
      pm.phase = "passed";
      pm.unresolvedDetail =
        "The canonical stage order is enforced here rather than checked by a tool, so this gate appends no " +
        "evidence record. Every later stage that ran is the proof it passed.";
    } else if (runStatus !== "running") {
      pm.phase = "failed";
      pm.unresolvedDetail = "The project manager's plan was rejected, so no stage after it ran.";
    }
  }

  /**
   * A gate the orchestrator built itself — an agent that returned nothing, an
   * output no tool can ground, a read-only agent that tried to mutate — carries
   * no evidence record, so the stage would sit at "gating" forever. The report
   * lists that finding, so a finished run can say what actually happened.
   */
  const orchestratorFindings = (report?.open_critical_findings ?? []).filter(
    (finding) => finding.rule === "stage grounded and accepted",
  );
  if (runStatus !== "running") {
    for (const id of TEAM_STAGE_IDS) {
      const stage = stages[id];
      if (stage.phase !== "gating") continue;
      const finding = orchestratorFindings.find((item) => item.kicad_object === id);
      stage.phase = finding ? "failed" : "unresolved";
      stage.unresolvedDetail = finding
        ? String(finding.actual)
        : "The run ended before this stage's gate was recorded.";
      if (finding) {
        const last = stage.attempts[stage.attempts.length - 1];
        if (last) last.gateFindings = [finding];
      }
    }
  }

  const lastEntry = trace[trace.length - 1];
  let inFlight: TeamStageId[] = [];
  let routing = false;
  if (runStatus === "running") {
    const gating = TEAM_STAGE_IDS.filter((id) => stages[id].phase === "gating");
    if (gating.length > 0) {
      inFlight = gating;
    } else if (lastEntry === undefined) {
      inFlight = ["project_manager"];
    } else if (lastEntry.kind === "gate" && lastEntry.record.result === "passed") {
      inFlight = successors(lastEntry.stage, parallel);
    } else {
      // A failed gate is routed by the finding text, which only the backend
      // resolves — so no stage is claimed until its own record lands.
      routing = true;
    }
    for (const id of inFlight) {
      if (stages[id].phase === "waiting") stages[id].phase = "running";
    }
  }

  const returnTrips: Partial<Record<TeamStageId, number>> = {};
  for (const id of CANONICAL_STAGE_IDS) {
    const extra = stages[id].attempts.length - 1;
    if (extra > 0) returnTrips[id] = extra;
  }

  return { stages, trace, inFlight, routing, returnTrips, repaired };
}

export const RELEASE_TONE: Record<string, "wire" | "brick" | "copper"> = {
  "ready for engineering review": "wire",
  needs_human_review: "copper",
  failed: "brick",
};

export function releaseLabel(status: string): string {
  if (status === "ready for engineering review") return "Ready for engineering review";
  if (status === "needs_human_review") return "Needs human review";
  if (status === "failed") return "Run failed";
  return status;
}
