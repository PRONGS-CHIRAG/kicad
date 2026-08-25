/** The four states of the single-change flow, derived in page.tsx and only displayed elsewhere. */
export type Stage = "project" | "select" | "preview" | "report";

export type Step = { id: string; label: string };

export const STAGES: { id: Stage; label: string }[] = [
  { id: "project", label: "Project" },
  { id: "select", label: "Describe" },
  { id: "preview", label: "Review" },
  { id: "report", label: "Verdict" },
];

/** The same shape for the ten-agent lane: one brief, one run, one release. */
export type TeamStep = "project" | "brief" | "run" | "release";

export const TEAM_STEPS: { id: TeamStep; label: string }[] = [
  { id: "project", label: "Project" },
  { id: "brief", label: "Brief" },
  { id: "run", label: "Team run" },
  { id: "release", label: "Release" },
];

/** Which lane of the app is in use. Both work on the same project. */
export type Lane = "single" | "team";
