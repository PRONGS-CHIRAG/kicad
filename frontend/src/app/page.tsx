"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ActionPlan,
  Clarification,
  PlanResponse,
  RunReport,
  SessionResponse,
  api,
  describeAction,
} from "@/lib/api";

const DEFAULT_INSTRUCTION = `Connect these components using I2C with 3.3 V logic.
Add the required pull-up resistors.
Do not modify the USB circuit.`;

type Stage = "project" | "select" | "preview" | "report";

export default function Home() {
  const [projects, setProjects] = useState<{ name: string; components: number }[]>([]);
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [instruction, setInstruction] = useState(DEFAULT_INSTRUCTION);
  const [plan, setPlan] = useState<ActionPlan | null>(null);
  const [planMeta, setPlanMeta] = useState<PlanResponse | null>(null);
  const [clarification, setClarification] = useState<Clarification | null>(null);
  const [report, setReport] = useState<RunReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const stage: Stage = report ? "report" : plan ? "preview" : session ? "select" : "project";

  useEffect(() => {
    api.projects().then((r) => setProjects(r.projects)).catch((e) => setError(e.message));
  }, []);

  const run = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
      setReport(null);
    });

  const generatePlan = () =>
    run(async () => {
      if (!session) return;
      const response = await api.plan(session.session_id, selected, instruction);
      setPlanMeta(response);
      setPlan(response.plan);
      setClarification(response.clarification);
    });

  const approve = () =>
    run(async () => {
      if (!session) return;
      setReport(await api.execute(session.session_id));
    });

  return (
    <main className="mx-auto max-w-5xl p-8 space-y-8">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">KiCAD Mitos</h1>
        <p className="text-sm text-neutral-500">
          Natural-language batch edits, reviewed before execution and verified with KiCAD&apos;s own ERC.
        </p>
        <Steps stage={stage} />
      </header>

      {error && <p className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      {!session && (
        <Card title="1. Open a project">
          <ul className="divide-y">
            {projects.map((project) => (
              <li key={project.name} className="flex items-center justify-between py-2">
                <span className="font-mono text-sm">{project.name}</span>
                <button className="btn" disabled={busy} onClick={() => openProject(project.name)}>
                  Open
                </button>
              </li>
            ))}
            {projects.length === 0 && <li className="py-2 text-sm text-neutral-500">No projects found.</li>}
          </ul>
        </Card>
      )}

      {session && !report && (
        <Card title="2. Select components and describe the change">
          <p className="mb-3 text-xs text-neutral-500">
            {session.project} · baseline ERC: {session.baseline_erc.errors} errors, {session.baseline_erc.warnings}{" "}
            warnings (pre-existing violations are never blamed on your change)
          </p>
          <div className="grid gap-4 md:grid-cols-2">
            <ul className="space-y-1">
              {session.components.map((component) => (
                <li key={component.reference}>
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={selected.includes(component.reference)}
                      onChange={(event) =>
                        setSelected((current) =>
                          event.target.checked
                            ? [...current, component.reference]
                            : current.filter((r) => r !== component.reference),
                        )
                      }
                    />
                    <span className="font-mono">{component.reference}</span>
                    <span className="text-neutral-500">{component.value}</span>
                  </label>
                </li>
              ))}
            </ul>
            <div className="space-y-2">
              <textarea
                className="h-40 w-full rounded border p-2 font-mono text-sm"
                value={instruction}
                onChange={(event) => setInstruction(event.target.value)}
              />
              <button className="btn" disabled={busy || selected.length < 2} onClick={generatePlan}>
                {busy ? "Working…" : "Generate plan"}
              </button>
            </div>
          </div>
        </Card>
      )}

      {clarification && !plan && (
        <Card title="Clarification needed">
          <p className="text-sm">{clarification.question}</p>
          <p className="mt-1 text-xs text-neutral-500">reason: {clarification.reason}</p>
          {clarification.options.length > 0 && (
            <p className="mt-2 text-xs text-neutral-500">options: {clarification.options.join(", ")}</p>
          )}
        </Card>
      )}

      {plan && !report && (
        <Card title="3. Review the proposed actions">
          <dl className="mb-3 grid grid-cols-2 gap-1 text-xs text-neutral-600 md:grid-cols-4">
            <Meta label="protocol" value={plan.protocol} />
            <Meta label="logic" value={plan.logic_voltage} />
            <Meta label="source" value={planMeta?.source ?? "rules"} />
            <Meta label="protected" value={plan.protected_objects.join(", ") || "none"} />
          </dl>
          <ol className="list-decimal space-y-1 pl-5 text-sm">
            {plan.actions.map((action) => (
              <li key={action.id}>
                {describeAction(action)}
                {action.purpose && <span className="text-neutral-500"> — {action.purpose}</span>}
              </li>
            ))}
          </ol>
          <Bullets title="Assumptions" items={plan.assumptions} />
          <Bullets title="Warnings" items={plan.warnings} />
          <Bullets title="Blocking problems" items={planMeta?.problems ?? []} />
          <div className="mt-4 flex gap-2">
            <button className="btn" disabled={busy || !planMeta?.executable} onClick={approve}>
              {busy ? "Executing…" : "Approve and execute"}
            </button>
            <button className="btn-secondary" disabled={busy} onClick={() => setPlan(null)}>
              Back
            </button>
          </div>
        </Card>
      )}

      {report && (
        <Card title="4. Validation report">
          <p
            className={`rounded p-3 text-sm ${
              report.decision === "accepted" ? "bg-green-50 text-green-800" : "bg-amber-50 text-amber-900"
            }`}
          >
            <strong>{report.decision.replace(/_/g, " ")}</strong> — {report.reason}
          </p>
          <ul className="mt-3 space-y-1 text-sm">
            {report.validation.checks.map((check) => (
              <li key={check.name}>
                <span className={check.passed ? "text-green-700" : "text-red-700"}>{check.passed ? "pass" : "fail"}</span>{" "}
                <span className="font-mono text-xs">{check.name}</span>
                {check.detail && <span className="text-neutral-500"> — {check.detail}</span>}
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-neutral-500">
            ERC before: {report.validation.erc_before?.errors ?? 0} errors / {report.validation.erc_before?.warnings ?? 0}{" "}
            warnings · after: {report.validation.erc_after?.errors ?? 0} errors /{" "}
            {report.validation.erc_after?.warnings ?? 0} warnings · new critical:{" "}
            {report.validation.violation_diff?.new.filter((v) => v.severity === "error").length ?? 0}
          </p>
          <Bullets title="Changes applied" items={report.changes} />
          {report.restoration_verified !== null && (
            <p className="mt-2 text-xs text-neutral-500">
              checkpoint restored: {report.restoration_verified ? "verified" : "failed"}
            </p>
          )}
          <button className="btn mt-4" onClick={() => setReport(null)}>
            Back to plan
          </button>
        </Card>
      )}
    </main>
  );
}

function Steps({ stage }: { stage: Stage }) {
  const stages: Stage[] = ["project", "select", "preview", "report"];
  return (
    <ol className="flex gap-2 pt-2 text-xs">
      {stages.map((item, index) => (
        <li key={item} className={item === stage ? "font-semibold text-blue-700" : "text-neutral-400"}>
          {index + 1}. {item}
        </li>
      ))}
    </ol>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border p-4">
      <h2 className="mb-3 text-lg font-medium">{title}</h2>
      {children}
    </section>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="uppercase tracking-wide text-neutral-400">{label}</dt>
      <dd className="font-mono">{value}</dd>
    </div>
  );
}

function Bullets({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">{title}</h3>
      <ul className="list-disc pl-5 text-sm">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
