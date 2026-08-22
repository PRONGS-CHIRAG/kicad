/** The four states of the flow, derived in page.tsx and only displayed elsewhere. */
export type Stage = "project" | "select" | "preview" | "report";

export const STAGES: { id: Stage; label: string }[] = [
  { id: "project", label: "Project" },
  { id: "select", label: "Describe" },
  { id: "preview", label: "Review" },
  { id: "report", label: "Verdict" },
];
