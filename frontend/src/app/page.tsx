"use client";

import { useCallback, useEffect, useState } from "react";
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
import type { Stage } from "@/lib/stages";
import { Composer, Mode } from "@/components/composer";
import { PlanReview } from "@/components/plan-review";
import { ProjectPicker } from "@/components/project-picker";
import { SheetPane } from "@/components/sheet-pane";
import { StageRail } from "@/components/stage-rail";
import { TopRail } from "@/components/top-rail";
import { Notice, Panel } from "@/components/ui";
import { Verdict } from "@/components/verdict";

const DEFAULT_INSTRUCTION = `Connect these components using I2C with 3.3 V logic.
Add the required pull-up resistors.
Do not modify the USB circuit.`;

export default function Home() {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [health, setHealth] = useState<HealthResponse | null>(null);
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

  /** Derived, never stored: one source of truth for where the flow is. */
  const stage: Stage = report ? "report" : plan ? "preview" : session ? "select" : "project";

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
      setRevision(created.revision);
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
    const next = { ...answers, [clarification.answer_key]: option };
    setAnswers(next);
    generatePlan(selected, next);
  };

  const approve = () =>
    run(async () => {
      if (!session) return;
      setReport(await api.execute(session.session_id));
      setRevision((current) => current + 1);
    });

  const errorNotice = error && (
    <Notice tone="brick" title="Request failed">
      {error}
    </Notice>
  );

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

            <div className="mt-8">
              <StageRail stage={stage} />
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
              <SheetPane
                session={session}
                view={view}
                revision={revision}
                pendingActions={plan && !report ? plan.actions.length : 0}
                boardOutOfSync={report?.decision === "accepted" && session.has_pcb}
                onView={setView}
                onClose={closeProject}
              />
            </div>

            <div className="min-w-0 space-y-5">
              <div className="panel px-5 py-4">
                <StageRail stage={stage} />
              </div>

              {errorNotice}

              {health && !health.erc_supported && (
                <Notice tone="copper" title="No verification available">
                  kicad-cli can&apos;t run ERC here, so the pipeline still plans and applies, but no result can be
                  called safe — every run comes back as <em>needs your review</em>.
                </Notice>
              )}

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
                          className={option === "Cancel" ? "btn-ghost !border-transparent !text-muted" : "btn-ghost"}
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
            </div>
          </div>
        )}
      </main>
    </>
  );
}
