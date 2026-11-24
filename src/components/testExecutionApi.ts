export type TestExecutionRequest = <T>(path: string, init?: RequestInit) => Promise<T>;

export type EligibleTestObservation = {
  id: string;
  exploration_id: string;
  page_id: string;
  page_url: string;
  page_title: string;
  interaction_type: "tab" | "disclosure";
  control: {
    element_id: string;
    element_ordinal: number;
    tag_name: string;
    role: string;
    name: string;
  };
  before_state: { selected: boolean | null; expanded: boolean | null };
  observed_after_state: { selected: boolean | null; expanded: boolean | null };
  outcome: "observed";
};

export function eligibleTestObservationsPath(explorationId: string, pageId?: string | null): string {
  const query = new URLSearchParams({ limit: "500" });
  if (pageId) query.set("page_id", pageId);
  return `/explorations/${explorationId}/test-observations?${query.toString()}`;
}

export function observationStepIsConfirmed(
  observationId: string | undefined,
  expectedState: boolean | null | undefined,
  confirmed: boolean | undefined,
): boolean {
  return Boolean(observationId) && typeof expectedState === "boolean" && confirmed === true;
}

export type CaseListFilters = {
  source: string;
  status: string;
  search: string;
  limit: number;
  offset: number;
};

export function caseAnalysisMessage(
  status: string | undefined,
  activeAiCaseCount: number | undefined,
  errorMessage: string | null | undefined,
): string | null {
  if (status === "not_started") return "AI analysis has not been started. User-created cases can still be managed.";
  if (status === "queued") return "AI analysis is queued. Its cases will appear when analysis completes.";
  if (status === "running") return "AI analysis is running. Its cases will appear when analysis completes.";
  if (status === "failed") return `AI analysis failed.${errorMessage ? ` ${errorMessage}` : ""}`;
  if (status === "completed" && activeAiCaseCount !== undefined) {
    return activeAiCaseCount > 0
      ? `AI analysis completed with ${activeAiCaseCount} executable case${activeAiCaseCount === 1 ? "" : "s"}.`
      : "AI analysis completed without executable test cases.";
  }
  return null;
}

export function testCaseListPath(explorationId: string, filters: CaseListFilters): string {
  const query = new URLSearchParams({
    limit: String(filters.limit),
    offset: String(filters.offset),
    status: filters.status,
  });
  if (filters.source !== "all") query.set("source", filters.source);
  if (filters.search.trim()) query.set("search", filters.search.trim());
  return `/explorations/${explorationId}/test-cases?${query.toString()}`;
}

export function testCaseDetailPath(explorationId: string, caseId: string): string {
  return `/explorations/${explorationId}/test-cases/${caseId}`;
}

export function testRunHistoryPath(
  explorationId: string,
  options: { caseId?: string | null; limit: number; offset: number },
): string {
  const query = new URLSearchParams({ limit: String(options.limit), offset: String(options.offset) });
  if (options.caseId) query.set("test_case_id", options.caseId);
  return `/explorations/${explorationId}/test-run-history?${query.toString()}`;
}

export function createTestCase<T>(request: TestExecutionRequest, explorationId: string, body: unknown): Promise<T> {
  return request(`/explorations/${explorationId}/test-cases`, { method: "POST", body: JSON.stringify(body) });
}

export function updateTestCase<T>(request: TestExecutionRequest, explorationId: string, caseId: string, body: unknown): Promise<T> {
  return request(testCaseDetailPath(explorationId, caseId), { method: "PATCH", body: JSON.stringify(body) });
}

export function duplicateTestCase<T>(request: TestExecutionRequest, explorationId: string, caseId: string): Promise<T> {
  return request(`${testCaseDetailPath(explorationId, caseId)}/duplicate`, { method: "POST" });
}

export function softDeleteTestCase<T>(request: TestExecutionRequest, explorationId: string, caseId: string): Promise<T> {
  return request(testCaseDetailPath(explorationId, caseId), { method: "DELETE" });
}

export function rerunTestCase<T>(request: TestExecutionRequest, explorationId: string, caseId: string): Promise<T> {
  return request(`${testCaseDetailPath(explorationId, caseId)}/runs`, { method: "POST" });
}
