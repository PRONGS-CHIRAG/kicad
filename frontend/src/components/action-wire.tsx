import type { ReactNode } from "react";
import { Action, describeAction } from "@/lib/api";

/**
 * The plan's actions, drawn the way KiCAD draws them: pins as boxed
 * designators, connections as orthogonal wire runs with junction dots, nets as
 * flag labels, and a pull-up as a resistor in line.
 *
 * All text lives in HTML, never inside the SVG — SVG text cannot wrap, and
 * every one of these strings comes from the backend at unbounded length. The
 * SVG only ever carries geometry.
 */

/** Backend pin references are `REF.PIN`, e.g. `U2.SDA`. */
function splitPin(reference: string): { designator: string; pin: string } {
  const dot = reference.indexOf(".");
  if (dot === -1) return { designator: reference, pin: "" };
  return { designator: reference.slice(0, dot), pin: reference.slice(dot + 1) };
}

function PinEndpoint({ reference, align }: { reference: string; align: "left" | "right" }) {
  const { designator, pin } = splitPin(reference);
  return (
    <div
      className={`w-[5.5rem] shrink-0 rounded-sm border border-brick/40 bg-brick-soft/60 px-1.5 py-1 sm:w-[7rem] ${
        align === "right" ? "text-right" : ""
      }`}
    >
      <p className="wrap-any font-mono text-[0.6875rem] font-semibold leading-tight text-brick">{designator}</p>
      {pin && <p className="wrap-any font-mono text-[0.625rem] leading-tight text-muted">{pin}</p>}
    </div>
  );
}

/** KiCAD's net label: a flag pointing back at the wire it names. */
function NetEndpoint({ name }: { name: string }) {
  return (
    <div
      className="w-[5.5rem] shrink-0 bg-wire-soft py-1 pl-3 pr-1.5 sm:w-[7rem]"
      style={{ clipPath: "polygon(7px 0, 100% 0, 100% 100%, 7px 100%, 0 50%)" }}
    >
      <p className="eyebrow text-wire">net</p>
      <p className="mt-0.5 wrap-any font-mono text-[0.6875rem] font-semibold leading-tight text-wire">{name}</p>
    </div>
  );
}

function Junction({ delay }: { delay: number }) {
  return (
    <span
      aria-hidden
      className="h-[5px] w-[5px] shrink-0 rounded-full bg-wire animate-[junction-pop_260ms_ease-out_both]"
      style={{ animationDelay: `${delay + 380}ms` }}
    />
  );
}

function Segment({ delay }: { delay: number }) {
  return (
    <span
      aria-hidden
      className="h-[1.5px] min-w-[8px] flex-1 origin-left bg-wire animate-[wire-draw_380ms_ease-out_both]"
      style={{ animationDelay: `${delay}ms` }}
    />
  );
}

/** ANSI resistor: the one piece of real symbol geometry, so it earns an SVG. */
function Resistor({ value, delay }: { value: string; delay: number }) {
  return (
    <span
      className="flex shrink-0 flex-col items-center animate-[rise-in_260ms_ease-out_both]"
      style={{ animationDelay: `${delay + 200}ms` }}
    >
      <svg width="44" height="16" viewBox="0 0 44 16" fill="none" aria-hidden className="block">
        <path
          d="M0 8h5l3-5 5 10 5-10 5 10 5-10 3 5h13"
          stroke="var(--wire)"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
      </svg>
      <span className="mt-0.5 max-w-[4rem] wrap-any text-center font-mono text-[0.625rem] leading-none text-copper">
        {value}
      </span>
    </span>
  );
}

/** The horizontal run between two endpoints, with an optional in-line part. */
function Run({ label, part, delay }: { label?: string; part?: ReactNode; delay: number }) {
  return (
    <div className="min-w-0 flex-1 self-center px-1.5">
      <div className="flex min-h-[1.125rem] items-end justify-center">
        {label && (
          <span
            title={label}
            className="max-w-full truncate rounded-sm bg-wire-soft px-1.5 py-px font-mono text-[0.625rem] font-medium leading-tight text-wire"
          >
            {label}
          </span>
        )}
      </div>
      <div className="mt-1 flex items-center">
        <Junction delay={delay} />
        <Segment delay={delay} />
        {part}
        {part && <Segment delay={delay + 90} />}
        <Junction delay={delay + 90} />
      </div>
    </div>
  );
}

export function ActionWire({ action, index }: { action: Action; index: number }) {
  const delay = index * 70;
  return (
    <li className="min-w-0 border-t border-rule/70 px-1 py-3.5 first:border-t-0 first:pt-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="eyebrow">
          act {String(index + 1).padStart(2, "0")}
        </span>
        <span className="eyebrow wrap-any text-right">{action.type.replace(/_/g, " ")}</span>
      </div>

      <div className="mt-2 flex min-w-0 items-stretch" aria-hidden>
        {action.type === "connect_pins" && (
          <>
            <PinEndpoint reference={action.from} align="left" />
            <Run label={action.net_name} delay={delay} />
            <PinEndpoint reference={action.to} align="right" />
          </>
        )}
        {action.type === "connect_pin_to_net" && (
          <>
            <PinEndpoint reference={action.pin} align="left" />
            <Run delay={delay} />
            <NetEndpoint name={action.net} />
          </>
        )}
        {action.type === "ensure_pullup" && (
          <>
            <NetEndpoint name={action.net} />
            <Run part={<Resistor value={action.value} delay={delay} />} delay={delay} />
            <NetEndpoint name={action.to_net} />
          </>
        )}
        {action.type === "place_footprint" && (
          <>
            <NetEndpoint name={action.net} />
            <Run label="near" delay={delay} />
            <PinEndpoint reference={action.near} align="right" />
          </>
        )}
      </div>

      {/* The drawing is decorative; this sentence is the actual accessible content. */}
      <p className="mt-2.5 min-w-0 wrap-any text-[0.8125rem] leading-snug text-ink">{describeAction(action)}</p>
      {action.purpose && (
        <p className="mt-0.5 min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">{action.purpose}</p>
      )}
    </li>
  );
}
