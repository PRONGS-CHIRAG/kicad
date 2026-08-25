"use client";

import { useRef, useState } from "react";
import { ProjectSummary } from "@/lib/api";
import { Chip, Notice, Panel } from "./ui";

export function ProjectPicker({
  projects,
  busy,
  reachable,
  onOpen,
  onUpload,
}: {
  projects: ProjectSummary[];
  busy: boolean;
  reachable: boolean;
  onOpen: (name: string) => void;
  onUpload: (name: string, file: File) => Promise<boolean>;
}) {
  return (
    <Panel eyebrow="Step 01" title="Open a project">
      {!reachable && (
        <div className="mb-4">
          <Notice tone="brick" title="Backend offline">
            Can&apos;t reach the API at the configured address. Start the backend, then reload — until it
            answers, nothing can be planned or verified.
          </Notice>
        </div>
      )}

      {projects.length === 0 ? (
        <p className="min-w-0 wrap-any text-[0.8125rem] leading-snug text-muted">
          No projects yet. Upload a .zip with your .kicad_pro and .kicad_sch at its root to get started.
        </p>
      ) : (
        <ul className="divide-y divide-rule/70 border-y border-rule/70">
          {projects.map((project) => (
            <li key={project.name} className="min-w-0">
              <button
                type="button"
                disabled={busy}
                onClick={() => onOpen(project.name)}
                className="group flex w-full items-center gap-3 px-1 py-2.5 text-left transition-colors hover:bg-wire-soft disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span className="min-w-0 flex-1">
                  <span className="block min-w-0 wrap-any font-mono text-[0.8125rem] font-medium leading-snug text-ink">
                    {project.name}
                  </span>
                  <span className="mt-0.5 block min-w-0 wrap-any font-mono text-[0.6875rem] leading-snug text-muted">
                    {project.path}
                  </span>
                </span>
                {project.origin === "uploaded" && (
                  <span className="shrink-0">
                    <Chip tone="wire">uploaded</Chip>
                  </span>
                )}
                <svg
                  aria-hidden
                  width="16"
                  height="16"
                  viewBox="0 0 16 16"
                  fill="none"
                  className="shrink-0 text-rule transition-colors group-hover:text-wire"
                >
                  <path d="M2 8h10M8.5 4.5 12 8l-3.5 3.5" stroke="currentColor" strokeWidth="1.5" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      )}

      <UploadProject busy={busy} onUpload={onUpload} />
    </Panel>
  );
}

/**
 * The chosen file lives in state, not in the input ref — reading the ref during
 * render made the filename go stale and needed a no-op setState to repaint.
 */
function UploadProject({
  busy,
  onUpload,
}: {
  busy: boolean;
  onUpload: (name: string, file: File) => Promise<boolean>;
}) {
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const take = (chosen: File | null) => {
    if (!chosen) return;
    setFile(chosen);
    // Name the project after the archive unless one was typed already.
    setName((current) => current || chosen.name.replace(/\.zip$/i, ""));
  };

  const submit = async () => {
    if (!file || !name.trim()) return;
    const ok = await onUpload(name.trim(), file);
    if (ok) {
      setName("");
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <div className="mt-5 border-t border-rule pt-4">
      <h3 className="eyebrow">Or bring your own</h3>

      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          take(event.dataTransfer.files?.[0] ?? null);
        }}
        className={`mt-2.5 rounded border border-dashed px-4 py-5 text-center transition-colors ${
          dragging ? "border-wire bg-wire-soft" : "border-rule bg-paper/60"
        }`}
      >
        <input
          ref={inputRef}
          id="project-zip"
          type="file"
          accept=".zip"
          className="sr-only"
          onChange={(event) => take(event.target.files?.[0] ?? null)}
        />
        <p className="min-w-0 wrap-any text-[0.8125rem] leading-snug text-ink">
          Drop a .zip here, or{" "}
          <label
            htmlFor="project-zip"
            className="cursor-pointer font-medium text-wire underline decoration-wire/40 underline-offset-2 hover:decoration-wire"
          >
            choose a file
          </label>
          .
        </p>
        <p className="mt-1 min-w-0 wrap-any text-[0.75rem] leading-snug text-muted">
          The .kicad_pro and .kicad_sch go at the root of the archive. Uploads land in the server workspace,
          never in the bundled fixtures.
        </p>
        {file && (
          <p className="mt-2.5 flex justify-center">
            <Chip tone="wire" title={file.name}>
              {file.name} · {(file.size / 1024).toFixed(0)} KB
            </Chip>
          </p>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-1 basis-48">
          <span className="eyebrow">Project name</span>
          <input
            className="field mt-1.5 font-mono"
            placeholder="my-board"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <button type="button" className="btn" disabled={busy || !name.trim() || !file} onClick={submit}>
          {busy ? "Uploading…" : "Upload project"}
        </button>
      </div>
    </div>
  );
}
