export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type ComponentSummary = {
  reference: string;
  value: string;
  lib_id: string;
  pins: { number: string; name: string; type: string; net: string | null }[];
};

export type Violation = {
  severity: "error" | "warning" | "info";
  type: string;
  description: string;
  items: string[];
};

export type ErcReport = {
  ran: boolean;
  kicad_version: string | null;
  errors: number;
  warnings: number;
  violations: Violation[];
};

export type SessionResponse = {
  session_id: string;
  project: string;
  project_dir: string;
  components: ComponentSummary[];
  nets: Record<string, string[]>;
  baseline_erc: ErcReport;
  baseline_drc: ErcReport | null;
  revision: number;
  has_pcb: boolean;
};

export type HealthResponse = {
  status: string;
  kicad_cli: string | null;
  erc_supported: boolean;
  drc_supported: boolean;
  executor: string;
  llm_enabled: boolean;
};

export type ProjectSummary = { name: string; path: string; origin: "fixture" | "uploaded" };

export type Action =
  | { id: string; type: "connect_pins"; from: string; to: string; net_name: string; purpose: string }
  | { id: string; type: "connect_pin_to_net"; pin: string; net: string; purpose: string }
  | { id: string; type: "ensure_pullup"; net: string; to_net: string; value: string; purpose: string };

export type ActionPlan = {
  schema_version: string;
  goal: string;
  selected_components: string[];
  protocol: string;
  logic_voltage: string;
  assumptions: string[];
  warnings: string[];
  protected_objects: string[];
  actions: Action[];
};

export type AnswerKey =
  | "protocol"
  | "logic_voltage"
  | "peripheral_sda"
  | "peripheral_scl"
  | "controller_sda"
  | "controller_scl"
  | "pullup_value";

/**
 * Clarifications for protocols beyond I2C name their signal in the key, e.g.
 * "controller_pin:SCK". I2C keeps the flat legacy keys above.
 */
export type SignalAnswerKey = `controller_pin:${string}` | `peripheral_pin:${string}`;

export type PlanAnswers = Partial<Record<AnswerKey, string>> & {
  controller_pins?: Record<string, string>;
  peripheral_pins?: Record<string, string>;
};

export type Clarification = {
  question: string;
  reason: string;
  options: string[];
  answer_key: AnswerKey | SignalAnswerKey | "selection" | null;
};

export type PlanResponse = {
  plan: ActionPlan | null;
  clarification: Clarification | null;
  problems: string[];
  source: string;
  executable: boolean;
};

export type RunReport = {
  session_id: string;
  goal: string;
  decision: "accepted" | "rejected_and_restored" | "needs_user_review";
  reason: string;
  changes: string[];
  restoration_verified: boolean | null;
  plan: ActionPlan;
  execution: {
    completed: boolean;
    error: string | null;
    steps: { action_id: string; tool: string; status: string; detail: string }[];
  };
  validation: {
    checks: { name: string; passed: boolean; detail: string }[];
    erc_before: ErcReport | null;
    erc_after: ErcReport | null;
    violation_diff: { new: Violation[]; resolved: Violation[]; unchanged: Violation[] } | null;
    drc_before: ErcReport | null;
    drc_after: ErcReport | null;
    drc_diff: { new: Violation[]; resolved: Violation[]; unchanged: Violation[] } | null;
    files_changed: string[];
    unexpected_files: string[];
    requested_connections_created: number;
    requested_connections_total: number;
    unexpected_changes: number;
    protected_modified: boolean;
    project_readable: boolean;
  };
  duration_seconds: number;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof body.detail === "string" ? body.detail : response.statusText);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),
  projects: () => request<{ projects: ProjectSummary[] }>("/api/projects"),
  /** Upload a zipped KiCAD project. Uploads land in the workspace, not the fixtures. */
  uploadProject: async (name: string, file: File) => {
    const body = new FormData();
    body.append("name", name);
    body.append("file", file);
    const response = await fetch(`${API_BASE}/api/projects`, { method: "POST", body });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(typeof detail.detail === "string" ? detail.detail : response.statusText);
    }
    return (await response.json()) as ProjectSummary;
  },
  createSession: (project: string) =>
    request<SessionResponse>("/api/sessions", { method: "POST", body: JSON.stringify({ project }) }),
  plan: (sessionId: string, selected: string[], instruction: string, answers: PlanAnswers = {}) =>
    request<PlanResponse>(`/api/sessions/${sessionId}/plan`, {
      method: "POST",
      body: JSON.stringify({ selected_components: selected, instruction, answers }),
    }),
  /** SVG of the working copy on disk; `revision` busts the cache after each applied change. */
  renderUrl: (sessionId: string, view: "schematic" | "pcb", revision: number) =>
    `${API_BASE}/api/sessions/${sessionId}/render?view=${view}&revision=${revision}`,
  execute: (sessionId: string) =>
    request<RunReport>(`/api/sessions/${sessionId}/execute`, {
      method: "POST",
      body: JSON.stringify({ approved: true }),
    }),
};

export function describeAction(action: Action): string {
  switch (action.type) {
    case "connect_pins":
      return `Connect ${action.from} ↔ ${action.to} as ${action.net_name}`;
    case "connect_pin_to_net":
      return `Connect ${action.pin} to ${action.net}`;
    case "ensure_pullup":
      return `Add ${action.value} pull-up from ${action.net} to ${action.to_net}`;
  }
}
