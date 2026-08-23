import type { Step } from "@/lib/stages";

/**
 * Display-only. The current step is derived from state in page.tsx — this rail
 * reports it and never sets it, so there is one source of truth for where you
 * are. Both lanes use it, so the steps are passed in rather than assumed.
 *
 * Numbered because the flow genuinely is a sequence: nothing can be reviewed
 * before it is planned, and nothing gets a verdict before it runs.
 */
export function StageRail({ steps, current }: { steps: Step[]; current: string }) {
  const index = steps.findIndex((item) => item.id === current);

  return (
    <ol className="flex">
      {steps.map((item, position) => {
        const done = position < index;
        const active = position === index;
        return (
          <li
            key={item.id}
            className="relative flex min-w-0 flex-1 flex-col items-center pt-0.5"
            aria-current={active ? "step" : undefined}
          >
            {position > 0 && (
              <span
                aria-hidden
                className={`absolute left-0 top-[6px] h-[1.5px] w-1/2 ${done || active ? "bg-wire" : "bg-rule"}`}
              />
            )}
            {position < steps.length - 1 && (
              <span
                aria-hidden
                className={`absolute right-0 top-[6px] h-[1.5px] w-1/2 ${done ? "bg-wire" : "bg-rule"}`}
              />
            )}
            <span
              aria-hidden
              className={`relative z-10 h-[13px] w-[13px] rounded-full border-[3px] ${
                done
                  ? "border-wire bg-wire"
                  : active
                    ? "border-wire bg-paper"
                    : "border-rule bg-paper"
              }`}
            />
            <span
              className={`mt-2 min-w-0 text-center font-mono text-[0.625rem] uppercase leading-tight tracking-[0.1em] ${
                active ? "font-semibold text-wire" : "text-muted"
              }`}
            >
              {item.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
