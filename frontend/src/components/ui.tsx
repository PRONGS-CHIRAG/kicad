import type { ReactNode } from "react";

/**
 * Shared primitives. Every one that holds a backend-authored string carries
 * `min-w-0` / `wrap-any`, because ERC details, file paths and net names have no
 * length bound and a flex or grid child defaults to `min-width: auto`.
 */

export function Panel({
  eyebrow,
  title,
  aside,
  children,
  className = "",
}: {
  eyebrow?: string;
  title?: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const hasHeader = Boolean(eyebrow || title || aside);
  return (
    <section className={`panel ${className}`}>
      {hasHeader && (
        <header className="flex flex-wrap items-end justify-between gap-x-4 gap-y-3 border-b border-rule px-4 py-3 sm:px-5">
          <div className="min-w-0">
            {eyebrow && <p className="eyebrow">{eyebrow}</p>}
            {title && (
              <h2 className="mt-1.5 text-[0.9375rem] font-semibold leading-tight tracking-[-0.012em] text-ink">
                {title}
              </h2>
            )}
          </div>
          {/* Controls keep their own `shrink-0`; a text aside has to be able to
              wrap instead of pushing the header wider than the panel. */}
          {aside && <div className="min-w-0 max-w-full">{aside}</div>}
        </header>
      )}
      <div className="px-4 py-4 sm:px-5">{children}</div>
    </section>
  );
}

/** Two-state switch used for schematic/pcb and words/fields. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  disabled,
  label,
}: {
  options: { value: T; label: string; disabled?: boolean }[];
  value: T;
  onChange: (next: T) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex shrink-0 gap-0.5 rounded border border-rule bg-paper p-0.5"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className="seg"
          aria-pressed={option.value === value}
          disabled={disabled || option.disabled}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

type Tone = "neutral" | "wire" | "brick" | "copper";

const NOTICE_TONE: Record<Tone, string> = {
  neutral: "border-rule bg-paper text-muted",
  wire: "border-wire/30 bg-wire-soft text-ink",
  brick: "border-brick/35 bg-brick-soft text-ink",
  copper: "border-copper-line/45 bg-copper-soft text-ink",
};

export function Notice({
  tone = "neutral",
  title,
  children,
}: {
  tone?: Tone;
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className={`rounded border p-3 text-[0.8125rem] leading-snug ${NOTICE_TONE[tone]}`}>
      {title && <p className="eyebrow mb-1.5 text-ink">{title}</p>}
      <div className="min-w-0 wrap-any">{children}</div>
    </div>
  );
}

/** Mono data token: designators, net names, file paths, check names. */
export function Chip({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: Tone;
  title?: string;
}) {
  const tones: Record<Tone, string> = {
    neutral: "",
    wire: "border-wire/35 bg-wire-soft text-wire",
    brick: "border-brick/35 bg-brick-soft text-brick",
    copper: "border-copper-line/45 bg-copper-soft text-copper",
  };
  return (
    <span className={`chip ${tones[tone]}`} title={title}>
      {children}
    </span>
  );
}

export function StatusDot({ tone }: { tone: Tone }) {
  const tones: Record<Tone, string> = {
    neutral: "bg-rule",
    wire: "bg-wire",
    brick: "bg-brick",
    copper: "bg-copper-line",
  };
  return <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${tones[tone]}`} />;
}

/** Label over value, stacked. The value may be a long machine string. */
export function DataCell({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="eyebrow">{label}</dt>
      <dd
        className={`mt-1 min-w-0 wrap-any text-[0.8125rem] leading-snug text-ink ${mono ? "font-mono" : ""}`}
      >
        {value}
      </dd>
    </div>
  );
}

export function Bullets({ title, items, tone = "neutral" }: { title: string; items: string[]; tone?: Tone }) {
  if (items.length === 0) return null;
  const markers: Record<Tone, string> = {
    neutral: "before:bg-rule",
    wire: "before:bg-wire",
    brick: "before:bg-brick",
    copper: "before:bg-copper-line",
  };
  return (
    <div className="mt-4 min-w-0">
      <h3 className="eyebrow">{title}</h3>
      <ul className="mt-2 space-y-1.5">
        {items.map((item) => (
          <li
            key={item}
            className={`relative min-w-0 wrap-any pl-4 text-[0.8125rem] leading-snug text-muted before:absolute before:left-0 before:top-[0.5em] before:h-1 before:w-1 before:rounded-full ${markers[tone]}`}
          >
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}
