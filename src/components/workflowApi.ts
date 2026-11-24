export type WorkflowProvenance = "observed" | "suggested" | "user_authored";
export type WorkflowState = "active" | "stale" | "reviewed" | "unreviewed";

export type WorkflowStep = {
  id: string;
  ordinal: number;
  step_type: string;
  provenance: WorkflowProvenance;
  instruction: string;
  page_id: string;
  target_page_id: string | null;
  page_url: string;
  page_title: string;
  control_name: string | null;
  evidence: Record<string, string>[];
  observed_state: Record<string, unknown> | null;
  expected_state: Record<string, unknown> | null;
};

export type Workflow = {
  id: string;
  exploration_id: string;
  project_id: string | null;
  identity_hash: string;
  source_type: "page_link" | "interaction_observation" | "test_case" | "manual";
  source_id: string;
  source_hash: string;
  provenance: WorkflowProvenance;
  status: "active" | "stale";
  name: string;
  description: string;
  reviewed: boolean;
  reviewed_at: string | null;
  evidence: Record<string, string>[];
  created_at: string;
  updated_at: string;
  steps: WorkflowStep[];
};

export type WorkflowPage = { items: Workflow[]; total: number; limit: number; offset: number };
export type WorkflowRebuild = {
  created: number;
  updated: number;
  unchanged: number;
  marked_stale: number;
  preserved_manual: number;
  skipped_invalid_sources: number;
  [key: string]: number | string;
};

export function workflowsPath(scope: { explorationId?: string | null; projectId?: string | null }, filters: {
  search?: string;
  provenance?: WorkflowProvenance | "all";
  status?: WorkflowState | "all";
  limit?: number;
  offset?: number;
} = {}) {
  const base = scope.projectId
    ? `/projects/${scope.projectId}/workflows`
    : scope.explorationId
      ? `/explorations/${scope.explorationId}/workflows`
      : null;
  if (!base) return null;
  const query = new URLSearchParams();
  if (filters.search?.trim()) query.set("search", filters.search.trim());
  if (filters.provenance && filters.provenance !== "all") query.set("provenance", filters.provenance);
  if (filters.status && filters.status !== "all") query.set("status", filters.status);
  if (filters.limit) query.set("limit", String(filters.limit));
  if (filters.offset) query.set("offset", String(filters.offset));
  const suffix = query.toString();
  return `${base}${suffix ? `?${suffix}` : ""}`;
}

export function workflowDetailPath(scope: { explorationId?: string | null; projectId?: string | null }, workflowId: string) {
  if (scope.projectId) return `/projects/${scope.projectId}/workflows/${workflowId}`;
  return scope.explorationId ? `/explorations/${scope.explorationId}/workflows/${workflowId}` : null;
}

export function rebuildWorkflowsPath(scope: { explorationId?: string | null; projectId?: string | null }) {
  if (scope.projectId) return `/projects/${scope.projectId}/workflows/rebuild`;
  return scope.explorationId ? `/explorations/${scope.explorationId}/workflows/rebuild` : null;
}

export function workflowReviewPath(scope: { explorationId?: string | null; projectId?: string | null }, workflowId: string) {
  const base = workflowDetailPath(scope, workflowId);
  return base ? `${base}/review` : null;
}
