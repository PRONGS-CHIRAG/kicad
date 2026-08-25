"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActionPlan,
  Clarification,
  HealthResponse,
  PlanAnswers,
  PlanResponse,
  ProjectSummary,
  RunReport,
  SessionResponse,
  api,
} from "@/lib/api";
import { STAGES, TEAM_STEPS, type Lane, type Stage, type TeamStep } from "@/lib/stages";
import {
  TeamEvidenceRecord,
  TeamRunStatus,
  TeamStageId,
  deriveFlow,
  teamApi,
} from "@/lib/team";
import { Composer, Mode } from "@/components/composer";
import { PlanReview } from "@/components/plan-review";
import { ProjectPicker } from "@/components/project-picker";
import { SheetPane } from "@/components/sheet-pane";
import { StageRail } from "@/components/stage-rail";
import { TeamBrief } from "@/components/team-brief";
import { TeamFlowRail } from "@/components/team-flow";
import { TeamRelease } from "@/components/team-release";
import { TeamStageDetail } from "@/components/team-stage";
import { TeamTrace } from "@/components/team-trace";
import { TopRail } from "@/components/top-rail";
import { Notice, Panel, Segmented } from "@/components/ui";
import { Verdict } from "@/components/verdict";

const DEFAULT_INSTRUCTION = `Connect these components using I2C with 3.3 V logic.
Add the required pull-up resistors.
Do not modify the USB circuit.`;

const DEFAULT_REQUEST = `Monitor temperature over I2C on a USB-C powered ESP32 board.
Keep the logic at 3.3 V and stay within a two-layer board.`;

export default function Home() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [lane, setLane] = useState<Lane>("single");
  const [mode, setMode] = useState<Mode>("nl");
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [instruction, setInstruction] = useState(DEFAULT_INSTRUCTION);
  const [plan, setPlan] = useState<ActionPlan | null>(null);
  const [planMeta, setPlanMeta] = useState<PlanResponse | null>(null);
  const [clarification, setClarification] = useState<Clarification | null>(null);
  const [answers, setAnswers] = useState<PlanAnswers>({});
  const [view, setView] = useState<"schematic" | "pcb">("schematic");
  const [revision, setRevision] = useState(0);
  const [report, setReport] = useState<RunReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* The ten-agent lane. It runs on its own working copy, so it carries its own
     session alongside the one the single-change lane is editing. */
  const [teamRequest, setTeamRequest] = useState(DEFAULT_REQUEST);
  /** Defaults to the free runner on purpose: the paid one is one labelled click away. */
  const [runner, setRunner] = useState<"devin" | "stub">("stub");
  const [teamRunId, setTeamRunId] = useState<string | null>(null);
  const [teamRun, setTeamRun] = useState<TeamRunStatus | null>(null);
  const [teamRecords, setTeamRecords] = useState<TeamEvidenceRecord[]>([]);
  const [teamSession, setTeamSession] = useState<SessionResponse | null>(null);
  const [openStage, setOpenStage] = useState<TeamStageId | null>(null);

  /** Derived, never stored: one source of truth for where the flow is. */
  const stage: Stage = report ? "report" : plan ? "preview" : session ? "select" : "project";
  /** Derived from the run's own status, never from whether a report exists —
      answering a question puts the run back to `running` with the old report
      still attached, and showing that verdict again would be a lie. */
  const teamStep: TeamStep = !teamRunId ? "brief" : teamRun?.status === "running" ? "run" : "release";
  const teamRunning = teamRun?.status === "running";
  /** Terminal means terminal: anything else, including "not answered yet", keeps polling. */
  const teamSettled = teamRun?.status === "completed" || teamRun?.status === "failed";

  const flow = useMemo(
    () =>
      deriveFlow(
        teamRecords,
        teamRun?.status ?? "running",
        teamRun?.report ?? null,
        health?.team_parallel ?? true,
      ),
    [teamRecords, teamRun, health],
  );

  const refreshProjects = useCallback(
    () => api.projects().then((r) => setProjects(r.projects)).catch((e: Error) => setError(e.message)),
    [],
  );

  useEffect(() => {
    refreshProjects();
    api.health().then(setHealth).catch(() => setHealth(null));
  }, [refreshProjects]);

  /** Resolves to whether the call succeeded, so callers can clear their inputs. */
  const run = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  /**
   * Poll while the run is live, then once more when it settles. Evidence is the
   * live channel — the orchestrator appends a record per agent and per gate as
   * it goes — while the report and the routing events only exist at the end.
   */
  useEffect(() => {
    if (!teamRunId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const [status, evidence] = await Promise.all([
          teamApi.status(teamRunId),
          teamApi.evidence(teamRunId),
        ]);
        if (cancelled) return;
        setTeamRun(status);
        setTeamRecords(evidence.records);
      } catch {
        /* A dropped poll is not worth tearing the view down for. The interval is
           armed until the run reports a terminal status, so the next one retries —
           including when it is the very first request that fails. */
      }
    };
    tick();
    if (teamSettled) return () => { cancelled = true; };
    const timer = setInterval(tick, 1000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [teamRunId, teamSettled]);

  /** The team edits its own copy of the project; the sheet should show that one. */
  useEffect(() => {
    const id = teamRun?.session_id;
    if (!id || teamSession?.session_id === id) return;
    api.session(id).then(setTeamSession).catch(() => setTeamSession(null));
  }, [teamRun?.session_id, teamSession?.session_id]);

  const resetTeam = () => {
    setTeamRunId(null);
    setTeamRun(null);
    setTeamRecords([]);
    setTeamSession(null);
    setOpenStage(null);
  };

  const openProject = (name: string) =>
    run(async () => {
      const created = await api.createSession(name);
      setSession(created);
      setSelected([]);
      setPlan(null);
      setClarification(null);
      setAnswers({});
      setReport(null);
      setView("schematic");
      setMode("nl");
      setLane("single");
      setRevision(created.revision);
      resetTeam();
    });

  const closeProject = () => {
    setSession(null);
    setSelected([]);
    setPlan(null);
    setPlanMeta(null);
    setClarification(null);
    setAnswers({});
    setReport(null);
    setError(null);
    resetTeam();
    refreshProjects();
  };

  const generatePlan = (
    nextSelected = selected,
    nextAnswers = answers,
    nextInstruction = mode === "form" ? "" : instruction,
  ) =>
    run(async () => {
      if (!session) return;
      const response = await api.plan(session.session_id, nextSelected, nextInstruction, nextAnswers);
      setPlanMeta(response);
      setPlan(response.plan);
      setClarification(response.clarification);
    });

  /**
   * Any hand edit to the selection or the instruction invalidates answers
   * gathered for the previous one — otherwise stale answers leak into the next
   * plan and it stops matching what's on screen.
   */
  const toggleComponent = (reference: string) => {
    setSelected((current) =>
      current.includes(reference) ? current.filter((r) => r !== reference) : [...current, reference],
    );
    setAnswers({});
  };

  const clearSelection = () => {
    setSelected([]);
    setAnswers({});
  };

  const changeInstruction = (next: string) => {
    setInstruction(next);
    setAnswers({});
  };

  const changeMode = (next: Mode) => {
    setMode(next);
    setAnswers({});
  };

  /** Apply a clicked clarification option and immediately re-plan with it. */
  const answerClarification = (option: string) => {
    if (!clarification) return;
    if (option === "Cancel") {
      setClarification(null);
      setAnswers({});
      return;
    }
    if (clarification.answer_key === "selection") {
      const known = new Set(session?.components.map((c) => c.reference) ?? []);
      const kept = selected.filter((ref) => known.has(ref));
      const next = kept.includes(option) ? kept.filter((ref) => ref !== option) : [...kept, option];
      setSelected(next);
      setClarification(null);
      if (next.length >= 2) generatePlan(next);
      return;
    }
    if (!clarification.answer_key) return;
    /**
     * A per-signal key ("controller_pin:SCK") belongs inside one of the two
     * dicts, not at the top level — PlanAnswers ignores unknown fields, so
     * writing it flat would drop the answer and re-ask the same question
     * forever.
     */
    const key = clarification.answer_key;
    const separator = key.indexOf(":");
    let next: PlanAnswers;
    if (separator === -1) {
      next = { ...answers, [key]: option };
    } else {
      const field = key.slice(0, separator) === "controller_pin" ? "controller_pins" : "peripheral_pins";
      const signal = key.slice(separator + 1);
      next = { ...answers, [field]: { ...(answers[field] ?? {}), [signal]: option } };
    }
    setAnswers(next);
    generatePlan(selected, next);
  };

  const approve = () =>
    run(async () => {
      if (!session) return;
      setReport(await api.execute(session.session_id));
      setRevision((current) => current + 1);
    });

  const startTeamRun = () =>
    run(async () => {
      if (!session) return;
      resetTeam();
      const started = await teamApi.start({
        project: session.project,
        request: teamRequest,
        runner,
      });
      setTeamRunId(started.run_id);
    });

  const answerTeamRun = (answer: string) =>
    run(async () => {
      if (!teamRunId) return;
      await teamApi.answer(teamRunId, answer);
      // Put the view back into the running state at once; the poll confirms it.
      setTeamRun((current) => (current ? { ...current, status: "running" } : current));
    });

  const errorNotice = error && (
    <Notice tone="brick" title="Request failed">
      {error}
    </Notice>
  );

  /* The sheet follows the lane: in the team lane it renders the very working
     copy the schematic and layout stages write to, keyed to the evidence count
     so it redraws as the run progresses. */
  const sheetSession = lane === "team" && teamSession ? teamSession : session;
  const sheetRevision = lane === "team" && teamSession ? teamRecords.length : revision;

  return (
    <>
      <TopRail health={health} />

      <main className="mx-auto max-w-bench px-4 pb-20 pt-6 sm:px-6">
        {!session ? (
          <div className="mx-auto max-w-2xl">
            <h1 className="max-w-[26ch] text-[1.75rem] font-extrabold leading-[1.1] tracking-[-0.035em] text-ink sm:text-[2.25rem]">
              Describe the change.
              <br />
              <span className="text-wire">KiCAD decides if it stays.</span>
            </h1>
            <p className="mt-3 max-w-[52ch] text-[0.9375rem] leading-relaxed text-muted">
              Mitos plans the edit, applies it to a checkpointed copy of your project, then runs KiCAD&apos;s own
              electrical rules check. If the board comes back worse than it started, the change is reverted.
            </p>
            <p className="mt-2 max-w-[52ch] text-[0.9375rem] leading-relaxed text-muted">
              Open a project and there are two lanes on it: one change at a time, or a ten-agent team that works
              a whole request from requirements through to a release record — gated the same way, stage by stage.
            </p>

            <div className="mt-8">
              <StageRail steps={STAGES} current={stage} />
            </div>

            {errorNotice && <div className="mt-6">{errorNotice}</div>}

            <div className="mt-6">
              <ProjectPicker
                projects={projects}
                busy={busy}
                reachable={health !== null}
                onOpen={openProject}
                onUpload={(name, file) =>
                  run(async () => {
                    await api.uploadProject(name, file);
                    await refreshProjects();
                  })
                }
              />
            </div>
          </div>
        ) : (
          <div className="grid gap-5 lg:grid-cols-[minmax(0,7fr)_minmax(0,6fr)] lg:items-start">
            <div className="min-w-0 lg:sticky lg:top-[4.5rem] lg:max-h-[calc(100vh-5.5rem)] lg:overflow-y-auto">
              {sheetSession && (
                <SheetPane
                  session={sheetSession}
                  view={view}
                  revision={sheetRevision}
                  pendingActions={lane === "single" && plan && !report ? plan.actions.length : 0}
                  boardOutOfSync={
                    lane === "single"
                      ? report?.decision === "accepted" && sheetSession.has_pcb
                      : flow.stages.pcb_layout.phase === "passed" && sheetSession.has_pcb
                  }
                  onView={setView}
                  onClose={closeProject}
                />
              )}
              {lane === "team" && teamRunning && !teamSession && (
                <p className="mt-2 min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
                  The sheet above is the project you opened. The team&apos;s own copy appears here as soon as the
                  run reports it.
                </p>
              )}
            </div>

            <div className="min-w-0 space-y-5">
              <div className="panel px-5 py-4">
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                  <Segmented
                    label="Lane"
                    value={lane}
                    onChange={setLane}
                    options={[
                      { value: "single", label: "One change" },
                      { value: "team", label: "Ten-agent team" },
                    ]}
                  />
                  <p className="min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
                    {lane === "single"
                      ? "One instruction, planned and ERC-gated."
                      : "A whole request, ten agents, gated at every stage."}
                  </p>
                </div>
                <div className="mt-4">
                  <StageRail
                    steps={lane === "single" ? STAGES : TEAM_STEPS}
                    current={lane === "single" ? stage : teamStep}
                  />
                </div>
              </div>

              {errorNotice}

              {health && !health.erc_supported && (
                <Notice tone="copper" title="No verification available">
                  kicad-cli can&apos;t run ERC here, so the pipeline still plans and applies, but no result can be
                  called safe — every run comes back as <em>needs your review</em>.
                </Notice>
              )}

              {lane === "single" ? (
                <>
                  {/* One step in focus at a time. "Back to describe" clears the plan
                      and brings this panel straight back. */}
                  {!report && !plan && (
                    <Composer
                      session={session}
                      selected={selected}
                      mode={mode}
                      instruction={instruction}
                      answers={answers}
                      busy={busy}
                      onToggle={toggleComponent}
                      onClearSelection={clearSelection}
                      onMode={changeMode}
                      onInstruction={changeInstruction}
                      onAnswers={setAnswers}
                      onPlan={() => generatePlan(selected, answers, mode === "form" ? "" : instruction)}
                    />
                  )}

                  {clarification && !plan && (
                    <Panel eyebrow="Before planning" title="One thing to pin down">
                      <p className="min-w-0 wrap-any text-[0.9375rem] font-medium leading-snug text-ink">
                        {clarification.question}
                      </p>
                      <p className="mt-1.5 min-w-0 wrap-any text-[0.8125rem] leading-snug text-muted">
                        {clarification.reason}
                      </p>
                      {clarification.options.length > 0 && (
                        <div className="mt-3.5 flex flex-wrap gap-2">
                          {clarification.options.map((option) => (
                            <button
                              key={option}
                              type="button"
                              className={
                                option === "Cancel" ? "btn-ghost !border-transparent !text-muted" : "btn-ghost"
                              }
                              disabled={busy}
                              onClick={() => answerClarification(option)}
                            >
                              {option}
                            </button>
                          ))}
                        </div>
                      )}
                      {clarification.answer_key === "selection" && (
                        <p className="mt-3 min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
                          Pick the components to connect — planning restarts as soon as two are chosen.
                        </p>
                      )}
                    </Panel>
                  )}

                  {plan && !report && (
                    <PlanReview
                      plan={plan}
                      meta={planMeta}
                      busy={busy}
                      onApprove={approve}
                      onBack={() => setPlan(null)}
                    />
                  )}

                  {report && (
                    <Verdict
                      report={report}
                      onBackToPlan={() => setReport(null)}
                      onNewChange={() => {
                        setReport(null);
                        setPlan(null);
                      }}
                    />
                  )}
                </>
              ) : (
                <>
                  {!teamRunId ? (
                    <TeamBrief
                      session={session}
                      health={health}
                      request={teamRequest}
                      runner={runner}
                      busy={busy}
                      onRequest={setTeamRequest}
                      onRunner={setRunner}
                      onStart={startTeamRun}
                    />
                  ) : (
                    <>
                      <TeamFlowRail
                        flow={flow}
                        status={teamRun?.status ?? "running"}
                        parallel={health?.team_parallel ?? true}
                        resumed={teamRun?.answers.length ?? 0}
                        selected={openStage}
                        onSelect={(next) => setOpenStage((current) => (current === next ? null : next))}
                      />

                      {openStage && (
                        <TeamStageDetail
                          stage={openStage}
                          flow={flow}
                          report={teamRun?.report ?? null}
                          onClose={() => setOpenStage(null)}
                        />
                      )}

                      {teamRun && teamRun.status !== "running" && (
                        <TeamRelease
                          run={teamRun}
                          busy={busy}
                          onAnswer={answerTeamRun}
                          onNewRun={resetTeam}
                        />
                      )}

                      <TeamTrace
                        flow={flow}
                        events={teamRun?.events ?? []}
                        onSelect={(next) => setOpenStage(next)}
                      />
                    </>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </main>
    </>
  );
}
