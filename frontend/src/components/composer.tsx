"use client";

import { AnswerKey, PlanAnswers, SessionResponse } from "@/lib/api";
import { Chip, Panel, Segmented } from "./ui";

export type Mode = "nl" | "form";

/** The form drives the same `answers` path the clarification chips use. */
const FORM_FIELDS: { key: AnswerKey; label: string; options?: string[]; fromPins?: boolean }[] = [
  { key: "protocol", label: "Interface", options: ["I2C", "SPI", "UART"] },
  { key: "logic_voltage", label: "Logic voltage", options: ["3.3V", "5V"] },
  { key: "controller_sda", label: "Controller SDA", fromPins: true },
  { key: "controller_scl", label: "Controller SCL", fromPins: true },
  { key: "peripheral_sda", label: "Peripheral SDA", fromPins: true },
  { key: "peripheral_scl", label: "Peripheral SCL", fromPins: true },
  { key: "pullup_value", label: "Pull-up value", options: ["4.7k", "2.2k", "10k"] },
];

export function Composer({
  session,
  selected,
  mode,
  instruction,
  answers,
  busy,
  onToggle,
  onClearSelection,
  onMode,
  onInstruction,
  onAnswers,
  onPlan,
}: {
  session: SessionResponse;
  selected: string[];
  mode: Mode;
  instruction: string;
  answers: PlanAnswers;
  busy: boolean;
  onToggle: (reference: string) => void;
  onClearSelection: () => void;
  onMode: (mode: Mode) => void;
  onInstruction: (instruction: string) => void;
  onAnswers: (answers: PlanAnswers) => void;
  onPlan: () => void;
}) {
  const ready = selected.length >= 2;

  return (
    <Panel
      eyebrow="Step 02"
      title="Pick the parts, say what to change"
      aside={
        <Segmented
          label="How to describe the change"
          value={mode}
          onChange={onMode}
          options={[
            { value: "nl", label: "Words" },
            { value: "form", label: "Fields" },
          ]}
        />
      }
    >
      <div className="grid gap-5 lg:grid-cols-[minmax(0,15rem)_minmax(0,1fr)]">
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <h3 className="eyebrow">Components</h3>
            <span className="flex shrink-0 items-center gap-2">
              <span className="font-mono text-[0.6875rem] text-muted">{selected.length} selected</span>
              {selected.length > 0 && (
                <button
                  type="button"
                  className="font-mono text-[0.6875rem] text-wire underline decoration-wire/40 underline-offset-2 hover:decoration-wire"
                  onClick={onClearSelection}
                >
                  clear
                </button>
              )}
            </span>
          </div>

          <ul className="mt-2 max-h-[19rem] overflow-y-auto rounded border border-rule">
            {session.components.map((component) => {
              const on = selected.includes(component.reference);
              return (
                <li key={component.reference} className="min-w-0 border-b border-rule/60 last:border-b-0">
                  <label
                    className={`flex min-w-0 cursor-pointer items-start gap-2.5 border-l-2 px-2.5 py-2 transition-colors ${
                      on ? "border-l-wire bg-wire-soft/70" : "border-l-transparent hover:bg-paper"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="mt-0.5 shrink-0"
                      checked={on}
                      onChange={() => onToggle(component.reference)}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                        <span className="wrap-any font-mono text-[0.8125rem] font-semibold leading-snug text-ink">
                          {component.reference}
                        </span>
                        <span className="wrap-any font-mono text-[0.6875rem] leading-snug text-muted">
                          {component.value}
                        </span>
                      </span>
                      <span
                        className="mt-0.5 block min-w-0 wrap-any font-mono text-[0.625rem] leading-snug text-muted"
                        title={component.lib_id}
                      >
                        {component.lib_id} · {component.pins.length} pins
                      </span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="min-w-0">
          {mode === "nl" ? (
            <label className="block min-w-0">
              <span className="eyebrow">The change, in your words</span>
              <textarea
                className="field mt-2 h-[11.5rem] resize-y font-mono leading-relaxed"
                value={instruction}
                onChange={(event) => onInstruction(event.target.value)}
              />
            </label>
          ) : (
            <PlanForm session={session} selected={selected} answers={answers} onChange={onAnswers} />
          )}

          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
            <button type="button" className="btn" disabled={busy || !ready} onClick={onPlan}>
              {busy ? "Planning…" : "Plan the change"}
            </button>
            {!ready && (
              <p className="min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
                Select at least two components to connect.
              </p>
            )}
            {ready && (
              <span className="flex min-w-0 flex-wrap gap-1">
                {selected.map((reference) => (
                  <Chip key={reference} tone="wire">
                    {reference}
                  </Chip>
                ))}
              </span>
            )}
          </div>

          {mode === "form" && (
            <p className="mt-3 min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
              The fields set exactly what the planner reads from a sentence, so they produce the same actions —
              and never call the LLM.
            </p>
          )}
        </div>
      </div>
    </Panel>
  );
}

function PlanForm({
  session,
  selected,
  answers,
  onChange,
}: {
  session: SessionResponse;
  selected: string[];
  answers: PlanAnswers;
  onChange: (answers: PlanAnswers) => void;
}) {
  const pins = Array.from(
    new Set(
      session.components
        .filter((component) => selected.includes(component.reference))
        .flatMap((component) => component.pins.map((pin) => pin.name)),
    ),
  ).sort();

  if (selected.length < 2) {
    return (
      <p className="min-w-0 wrap-any rounded border border-dashed border-rule px-4 py-8 text-center text-[0.8125rem] leading-snug text-muted">
        Select two components and their pins will fill these fields.
      </p>
    );
  }

  return (
    <div className="grid gap-2.5 sm:grid-cols-2">
      {FORM_FIELDS.map((field) => {
        const options = field.fromPins ? pins : (field.options ?? []);
        return (
          <label key={field.key} className="min-w-0">
            <span className="eyebrow">{field.label}</span>
            <select
              className="field mt-1.5 font-mono"
              value={answers[field.key] ?? ""}
              onChange={(event) => onChange({ ...answers, [field.key]: event.target.value || undefined })}
            >
              <option value="">auto</option>
              {options.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        );
      })}
    </div>
  );
}
