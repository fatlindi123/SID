export type ProjectListState = "active" | "archived" | "all";

export function projectsPath(filters: {
  search?: string;
  state?: ProjectListState;
  limit?: number;
  offset?: number;
} = {}) {
  const query = new URLSearchParams();
  if (filters.search?.trim()) query.set("search", filters.search.trim());
  if (filters.state && filters.state !== "active") query.set("state", filters.state);
  if (filters.limit) query.set("limit", String(filters.limit));
  if (filters.offset) query.set("offset", String(filters.offset));
  const suffix = query.toString();
  return `/projects${suffix ? `?${suffix}` : ""}`;
}

export function projectDetailPath(projectId: string) {
  return `/projects/${projectId}`;
}

export function projectExplorationsPath(projectId: string, filters: { search?: string; status?: string; limit?: number; offset?: number } = {}) {
  const query = new URLSearchParams();
  if (filters.search?.trim()) query.set("search", filters.search.trim());
  if (filters.status) query.set("exploration_status", filters.status);
  if (filters.limit) query.set("limit", String(filters.limit));
  if (filters.offset) query.set("offset", String(filters.offset));
  const suffix = query.toString();
  return `/projects/${projectId}/explorations${suffix ? `?${suffix}` : ""}`;
}

export function availableExplorationsPath(filters: { search?: string; limit?: number; offset?: number } = {}) {
  const query = new URLSearchParams();
  if (filters.search?.trim()) query.set("search", filters.search.trim());
  if (filters.limit) query.set("limit", String(filters.limit));
  if (filters.offset) query.set("offset", String(filters.offset));
  const suffix = query.toString();
  return `/projects/available-explorations${suffix ? `?${suffix}` : ""}`;
}

export function projectExplorationAssociationPath(projectId: string, explorationId: string) {
  return `/projects/${projectId}/explorations/${explorationId}`;
}

export function projectTestCasesPath(projectId: string, filters: { search?: string; source?: string; status?: string; limit?: number; offset?: number } = {}) {
  const query = new URLSearchParams();
  if (filters.search?.trim()) query.set("search", filters.search.trim());
  if (filters.source) query.set("source", filters.source);
  if (filters.status) query.set("case_status", filters.status);
  if (filters.limit) query.set("limit", String(filters.limit));
  if (filters.offset) query.set("offset", String(filters.offset));
  const suffix = query.toString();
  return `/projects/${projectId}/test-cases${suffix ? `?${suffix}` : ""}`;
}

export function projectRunHistoryPath(projectId: string, filters: { status?: string; limit?: number; offset?: number } = {}) {
  const query = new URLSearchParams();
  if (filters.status) query.set("run_status", filters.status);
  if (filters.limit) query.set("limit", String(filters.limit));
  if (filters.offset) query.set("offset", String(filters.offset));
  const suffix = query.toString();
  return `/projects/${projectId}/run-history${suffix ? `?${suffix}` : ""}`;
}

export function projectKnowledgeGraphsPath(projectId: string, filters: { limit?: number; offset?: number } = {}) {
  const query = new URLSearchParams();
  if (filters.limit) query.set("limit", String(filters.limit));
  if (filters.offset) query.set("offset", String(filters.offset));
  const suffix = query.toString();
  return `/projects/${projectId}/knowledge-graphs${suffix ? `?${suffix}` : ""}`;
}

export function projectArchivePath(projectId: string, restore = false) {
  return `/projects/${projectId}/${restore ? "restore" : "archive"}`;
}
