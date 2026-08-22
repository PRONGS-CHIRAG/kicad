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
};

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

export type Clarification = { question: string; reason: string; options: string[] };

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
  health: () => request<{ status: string; kicad_cli: string | null; executor: string; llm_enabled: boolean }>("/api/health"),
  projects: () => request<{ projects: { name: string; schematic: string; components: number }[] }>("/api/projects"),
  createSession: (project: string) =>
    request<SessionResponse>("/api/sessions", { method: "POST", body: JSON.stringify({ project }) }),
  plan: (sessionId: string, selected: string[], instruction: string) =>
    request<PlanResponse>(`/api/sessions/${sessionId}/plan`, {
      method: "POST",
      body: JSON.stringify({ selected_components: selected, instruction }),
    }),
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
