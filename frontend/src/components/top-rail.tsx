import { HealthResponse } from "@/lib/api";
import { StatusDot } from "./ui";

/** A KiCAD T-junction: two wire stubs meeting at a solder dot. */
function JunctionMark() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden className="shrink-0">
      <path d="M1 8h18M10 8v11" stroke="var(--wire)" strokeWidth="1.6" />
      <circle cx="10" cy="8" r="3" fill="var(--wire)" />
    </svg>
  );
}

/**
 * The instrument strip: what the backend can actually do right now. Verification
 * is the whole promise of this tool, so its state belongs in the chrome rather
 * than in a banner that scrolls away.
 */
export function TopRail({ health }: { health: HealthResponse | null }) {
  const readouts = health
    ? [
        { label: "kicad-cli", value: health.kicad_cli ?? "not found", ok: Boolean(health.kicad_cli) },
        { label: "erc", value: health.erc_supported ? "ready" : "unavailable", ok: health.erc_supported },
        { label: "drc", value: health.drc_supported ? "ready" : "unavailable", ok: health.drc_supported },
        { label: "executor", value: health.executor, ok: true },
        { label: "planner", value: health.llm_enabled ? "llm + rules" : "rules only", ok: true },
      ]
    : [];

  const summary = !health
    ? { text: "Backend offline", tone: "brick" as const }
    : health.erc_supported
      ? { text: "Changes are ERC-verified", tone: "wire" as const }
      : { text: "Changes can't be verified", tone: "copper" as const };

  return (
    <div className="sticky top-0 z-20 border-b border-rule bg-paper/90 backdrop-blur">
      <div className="mx-auto flex max-w-bench flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-2.5 sm:px-6">
        <div className="flex min-w-0 items-center gap-2.5">
          <JunctionMark />
          <div className="min-w-0">
            <p className="text-[0.9375rem] font-extrabold leading-none tracking-[-0.03em] text-ink">
              MITOS
            </p>
            <p className="eyebrow mt-1">for KiCAD</p>
          </div>
        </div>

        {/* Full readout where there's room; one honest summary where there isn't. */}
        <div className="hidden shrink-0 items-center gap-x-5 gap-y-1 lg:flex">
          {readouts.map((item) => (
            <span key={item.label} className="flex items-center gap-1.5">
              <StatusDot tone={item.ok ? "wire" : "copper"} />
              <span className="eyebrow">{item.label}</span>
              <span className="font-mono text-[0.6875rem] leading-none text-ink">{item.value}</span>
            </span>
          ))}
        </div>
        <span className="flex shrink-0 items-center gap-1.5 lg:hidden">
          <StatusDot tone={summary.tone} />
          <span className="font-mono text-[0.6875rem] leading-none text-ink">{summary.text}</span>
        </span>
      </div>
    </div>
  );
}
