"use client";

import { useEffect, useState } from "react";
import { SessionResponse, api } from "@/lib/api";
import { Panel, Segmented } from "./ui";

/**
 * The sheet: the session's working copy rendered by kicad-cli. It only ever
 * shows what is on disk, so a rejected run rolls back and the next render snaps
 * to the original. `revision` busts the cache after each applied change.
 */
export function SheetPane({
  session,
  view,
  revision,
  pendingActions,
  boardOutOfSync,
  onView,
  onClose,
}: {
  session: SessionResponse;
  view: "schematic" | "pcb";
  revision: number;
  pendingActions: number;
  /** A kept change edited the schematic; this project has a board that no longer matches it. */
  boardOutOfSync: boolean;
  onView: (view: "schematic" | "pcb") => void;
  onClose: () => void;
}) {
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const source = api.renderUrl(session.session_id, view, revision);

  useEffect(() => setState("loading"), [source]);

  const erc = session.baseline_erc;
  const drc = session.baseline_drc;

  return (
    <Panel
      eyebrow="Sheet"
      title={view === "pcb" ? "Board, as it stands on disk" : "Schematic, as it stands on disk"}
      aside={
        <Segmented
          label="Sheet view"
          value={view}
          onChange={onView}
          options={[
            { value: "schematic", label: "Schematic" },
            { value: "pcb", label: "PCB", disabled: !session.has_pcb },
          ]}
        />
      }
    >
      <div className="relative overflow-hidden rounded border border-rule bg-sheet drafting-grid">
        {state === "failed" ? (
          <p className="min-w-0 wrap-any px-6 py-16 text-center text-[0.8125rem] leading-snug text-muted">
            Couldn&apos;t render the {view === "pcb" ? "board" : "schematic"}. kicad-cli has to be installed and
            able to open this project.
          </p>
        ) : (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={source}
              alt={`${session.project} ${view}`}
              className={`h-[15rem] w-full object-contain transition-opacity duration-200 sm:h-[19rem] lg:h-[24rem] ${
                state === "ready" ? "opacity-100" : "opacity-0"
              }`}
              onLoad={() => setState("ready")}
              onError={() => setState("failed")}
            />
            {state === "loading" && (
              <p className="eyebrow absolute inset-0 flex items-center justify-center">
                Rendering with kicad-cli…
              </p>
            )}
          </>
        )}
      </div>

      <p
        className={`mt-2.5 min-w-0 wrap-any text-[0.75rem] leading-snug ${
          pendingActions > 0 ? "text-copper" : "text-muted"
        }`}
      >
        {pendingActions > 0
          ? `${pendingActions} proposed ${
              pendingActions === 1 ? "action is" : "actions are"
            } not on the sheet yet. Approve the plan and the sheet redraws.`
          : "Nothing pending. This is the project exactly as it is on disk."}
      </p>

      {/* A disabled toggle with no reason beside it is a dead end. Say why. */}
      {!session.has_pcb && (
        <p className="mt-1 min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
          The PCB view is off because this project has no board. Add a{" "}
          <span className="font-mono">.kicad_pcb</span> beside the schematic to turn it on.
        </p>
      )}

      {boardOutOfSync && (
        <p className="mt-1 min-w-0 wrap-any text-[0.75rem] leading-snug text-copper">
          Footprints and nets are on the board, but the new connections aren&apos;t routed yet. Open the
          project in KiCAD to lay the copper.
        </p>
      )}

      {/* The fixed facts about the artifact on screen — a schematic's title block,
          flattened into a strip. */}
      <dl className="mt-4 grid grid-cols-2 gap-px border border-rule bg-rule sm:grid-cols-4">
        <StripCell label="Project" value={session.project} />
        <StripCell label="Revision" value={String(session.revision)} />
        <StripCell
          label="Baseline ERC"
          value={`${erc.errors} err · ${erc.warnings} warn`}
          tone={erc.errors > 0 ? "brick" : erc.warnings > 0 ? "copper" : "wire"}
        />
        <StripCell
          label="Baseline DRC"
          value={drc?.ran ? `${drc.errors} err · ${drc.warnings} warn` : "not run"}
          tone={drc?.ran ? (drc.errors > 0 ? "brick" : "wire") : "neutral"}
        />
      </dl>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p className="min-w-0 wrap-any font-mono text-[0.6875rem] leading-snug text-muted" title={session.project_dir}>
          {session.project_dir}
        </p>
        <button type="button" className="btn-ghost shrink-0 !px-2.5 !py-1 !text-[0.75rem]" onClick={onClose}>
          Change project
        </button>
      </div>
    </Panel>
  );
}

function StripCell({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "wire" | "brick" | "copper";
}) {
  const tones = {
    neutral: "text-ink",
    wire: "text-wire",
    brick: "text-brick",
    copper: "text-copper",
  };
  return (
    <div className="min-w-0 bg-sheet px-3 py-2.5">
      <dt className="eyebrow">{label}</dt>
      <dd className={`mt-1 min-w-0 wrap-any font-mono text-[0.75rem] leading-snug ${tones[tone]}`} title={value}>
        {value}
      </dd>
    </div>
  );
}
