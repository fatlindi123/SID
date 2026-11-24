import { useEffect, useMemo, useState, type FormEvent } from "react";

import type { AnalysisStatusResponse } from "./AIAnalysis";
import { ObservationStepPicker } from "./ObservationStepPicker";
import { useUiPreferences } from "../preferences";
import { loadScopedResource, scopedResourceFor, type ScopedResource } from "./testExecutionState";
import {
  caseAnalysisMessage,
  createTestCase,
  duplicateTestCase,
  eligibleTestObservationsPath,
  observationStepIsConfirmed,
  rerunTestCase,
  softDeleteTestCase,
  testCaseDetailPath,
  testCaseListPath,
  testRunHistoryPath,
  updateTestCase,
  type EligibleTestObservation,
} from "./testExecutionApi";

type SavedElement = {
  id: string;
  kind: string;
  tag_name: string;
  role: string | null;
  accessible_name: string | null;
  label: string | null;
  selector: string | null;
  attributes: Record<string, string | boolean>;
};

type SavedPage = {
  id: string;
  url: string;
  title: string;
  elements: SavedElement[];
};

type WorkflowScenario = {
  title: string;
  objective: string;
  expected_outcome: string;
  relevant_page_ids: string[];
  steps: {
    instruction: string;
    evidence: { page_id: string; element_ids: string[] }[];
    interaction_observation?: { observation_id: string; expected_state: boolean; expected_state_confirmed: boolean } | null;
    interaction_evidence_snapshot?: {
      interaction_type: "tab" | "disclosure";
      control: { name: string; role: string; tag_name: string };
      before_state: { selected: boolean | null; expanded: boolean | null };
      observed_after_state: { selected: boolean | null; expanded: boolean | null };
      expected_state: { attribute: string; value: boolean; user_confirmed: boolean };
    } | null;
  }[];
};

export type TestCase = {
  id: string;
  exploration_id: string;
  source: "ai_generated" | "user_created";
  status: "active" | "superseded" | "deleted";
  name: string;
  description: string;
  identity_hash: string;
  source_analysis_id: string | null;
  analysis_version_hash: string | null;
  scenario_version_hash: string;
  definition: {
    schema_version: number;
    execution_kind: string;
    scenario: WorkflowScenario & Record<string, unknown>;
    steps: { ordinal: number; instruction: string; action: Record<string, unknown>; expected_evidence: Record<string, unknown> }[];
  };
  is_current: boolean;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
};

export type TestRunStep = {
  id: string;
  ordinal: number;
  instruction: string;
  action: Record<string, unknown>;
  expected_evidence: Record<string, unknown>;
  status: "pending" | "running" | "passed" | "failed" | "cancelled";
  duration_ms: number | null;
  error_message: string | null;
  actual_evidence: Record<string, unknown> | null;
  started_at: string | null;
  completed_at: string | null;
};

type ScenarioSnapshot = {
  title?: string;
  objective?: string;
  expected_outcome?: string;
  source?: "manual" | "ai_generated" | "user_created";
  scenario?: ScenarioSnapshot;
};

export type TestRun = {
  id: string;
  exploration_id: string;
  analysis_id: string | null;
  test_case_id: string | null;
  scenario_index: number;
  scenario: ScenarioSnapshot;
  status: "pending" | "running" | "passed" | "failed" | "cancelled";
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  steps: TestRunStep[];
};

type TestCaseCollection = {
  exploration_id: string;
  analysis_status: AnalysisStatusResponse["status"];
  analysis_attempt_count: number;
  analysis_error: string | null;
  analysis_version_hash: string | null;
  active_ai_case_count: number;
  cases: TestCase[];
  total: number;
  limit: number;
  offset: number;
};

type PageOfRuns = { items: TestRun[]; total: number; limit: number; offset: number; active_run_count: number };
type ApiRequest = <T>(path: string, init?: RequestInit) => Promise<T>;
type StepKind = "navigate" | "assert_visible" | "click_link";
type DraftStep = {
  kind: StepKind | "interaction";
  page_id: string;
  element_id: string;
  observation_id?: string;
  expected_state?: boolean | null;
  expected_state_confirmed?: boolean;
  saved_interaction?: WorkflowScenario["steps"][number]["interaction_evidence_snapshot"];
};
type CaseDraft = {
  name: string;
  description: string;
  title: string;
  objective: string;
  expected_outcome: string;
  steps: DraftStep[];
};

type TestExecutionSectionProps = {
  explorationId: string | null;
  explorationStatus: string;
  analysisStatus: AnalysisStatusResponse | null;
  analysisStatusLoading: boolean;
  analysisStatusError: string | null;
  pages: SavedPage[];
  request: ApiRequest;
  onCancel: (runId: string) => Promise<void>;
  initialCaseId?: string | null;
};

const activeStatuses = new Set(["pending", "running"]);
const blankDraft = (pageId = ""): CaseDraft => ({
  name: "",
  description: "",
  title: "",
  objective: "",
  expected_outcome: "",
  steps: [{ kind: "navigate", page_id: pageId, element_id: "" }],
});

function statusName(status: string) {
  return status[0].toUpperCase() + status.slice(1);
}

function runScenario(run: TestRun): ScenarioSnapshot {
  return run.scenario.scenario || run.scenario;
}

function caseScenario(testCase: TestCase) {
  return testCase.definition.scenario;
}

function elementName(element: SavedElement) {
  return element.accessible_name || element.label || element.tag_name;
}

function stateLabel(state: { selected: boolean | null; expanded: boolean | null } | null | undefined) {
  if (!state) return "State unavailable";
  if (typeof state.selected === "boolean") return `selected ${state.selected}`;
  if (typeof state.expanded === "boolean") return `expanded ${state.expanded}`;
  return "State unavailable";
}

function scenarioForDraft(draft: CaseDraft, pages: SavedPage[]): WorkflowScenario {
  const relevantPageIds = new Set<string>();
  const steps = draft.steps.map((step) => {
    relevantPageIds.add(step.page_id);
    const elementIds = step.element_id ? [step.element_id] : [];
    if (step.kind === "interaction") {
      return {
        instruction: "",
        evidence: [{ page_id: step.page_id, element_ids: elementIds }],
        interaction_observation: {
          observation_id: step.observation_id || "",
          expected_state: step.expected_state === true,
          expected_state_confirmed: step.expected_state_confirmed === true,
        },
      };
    }
    if (step.kind === "click_link") {
      const page = pages.find((item) => item.id === step.page_id);
      const element = page?.elements.find((item) => item.id === step.element_id);
      const href = element?.attributes.href;
      if (page && typeof href === "string") {
        try {
          const targetUrl = new URL(href, page.url).href;
          const target = pages.find((item) => new URL(item.url).href === targetUrl);
          if (target) relevantPageIds.add(target.id);
        } catch {
          // The API's safe planner reports invalid or unsaved destinations.
        }
      }
    }
    const verb = step.kind === "navigate" ? "Navigate to" : step.kind === "click_link" ? "Click the" : "Assert the";
    const object = step.kind === "navigate" ? "saved page." : step.kind === "click_link" ? "saved internal link." : "saved element is visible.";
    return {
      instruction: `${verb} ${object}`,
      evidence: [{ page_id: step.page_id, element_ids: elementIds }],
    };
  });
  return {
    title: draft.title.trim(),
    objective: draft.objective.trim(),
    expected_outcome: draft.expected_outcome.trim(),
    relevant_page_ids: [...relevantPageIds],
    steps,
  };
}

function serializeError(reason: unknown) {
  return reason instanceof Error ? reason.message : "The request failed.";
}

export function TestExecutionSection({
  explorationId,
  explorationStatus,
  analysisStatus,
  analysisStatusLoading,
  analysisStatusError,
  pages,
  request,
  onCancel,
  initialCaseId = null,
}: TestExecutionSectionProps) {
  const { pageSize } = useUiPreferences();
  const [sourceFilter, setSourceFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("active");
  const [search, setSearch] = useState("");
  const [caseOffset, setCaseOffset] = useState(0);
  const [caseRunOffset, setCaseRunOffset] = useState(0);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [listResource, setListResource] = useState<ScopedResource<TestCaseCollection>>({
    scope: "", data: null, loading: true, error: null,
  });
  const [detailResource, setDetailResource] = useState<ScopedResource<TestCase>>({
    scope: "", data: null, loading: false, error: null,
  });
  const [caseHistory, setCaseHistory] = useState<ScopedResource<PageOfRuns>>({
    scope: "", data: null, loading: false, error: null,
  });
  const [allHistory, setAllHistory] = useState<ScopedResource<PageOfRuns>>({
    scope: "", data: null, loading: true, error: null,
  });
  const [observationResource, setObservationResource] = useState<ScopedResource<EligibleTestObservation[]>>({
    scope: "", data: null, loading: false, error: null,
  });
  const [observationPageFilter, setObservationPageFilter] = useState("all");
  const [observationToAdd, setObservationToAdd] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingCaseId, setEditingCaseId] = useState<string | null>(null);
  const [editingScenarioAllowed, setEditingScenarioAllowed] = useState(true);
  const [draft, setDraft] = useState<CaseDraft>(() => blankDraft(pages[0]?.id));
  const [actionError, setActionError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [duplicatingCaseId, setDuplicatingCaseId] = useState<string | null>(null);
  const [deletingCaseId, setDeletingCaseId] = useState<string | null>(null);
  const [startingCaseId, setStartingCaseId] = useState<string | null>(null);
  const [cancellingRunId, setCancellingRunId] = useState<string | null>(null);
  const [cancelError, setCancelError] = useState<string | null>(null);

  useEffect(() => {
    setCaseOffset(0);
    setCaseRunOffset(0);
    setHistoryOffset(0);
  }, [pageSize]);

  const listScope = explorationId ? `${explorationId}:${sourceFilter}:${statusFilter}:${search.trim()}:${caseOffset}:${pageSize}` : "";
  const currentList = explorationId
    ? scopedResourceFor(listResource, listScope, null)
    : { scope: "", data: null, loading: false, error: null };
  const detailScope = `${explorationId || ""}:${selectedCaseId || ""}`;
  const currentDetail = scopedResourceFor(detailResource, detailScope, null);
  const caseHistoryScope = `${detailScope}:${caseRunOffset}:${pageSize}`;
  const observationScope = explorationId || "";
  const currentObservations = scopedResourceFor(observationResource, observationScope, []);
  const currentCaseHistory = scopedResourceFor(caseHistory, caseHistoryScope, null);
  const allHistoryScope = explorationId ? `${explorationId}:${historyOffset}:${pageSize}` : "";
  const currentAllHistory = explorationId
    ? scopedResourceFor(allHistory, allHistoryScope, null)
    : { scope: "", data: null, loading: false, error: null };
  const selectedCase = currentDetail.data;
  const hasActiveRun = (currentAllHistory.data?.active_run_count || 0) > 0;
  const caseCollection = currentList.data;
  const resolvedAnalysisStatus = caseCollection?.analysis_status || analysisStatus?.status;
  const analysisMessage = caseAnalysisMessage(
    resolvedAnalysisStatus,
    caseCollection?.active_ai_case_count,
    caseCollection?.analysis_error || analysisStatus?.error_message,
  );

  useEffect(() => {
    setCaseOffset(0);
  }, [explorationId, sourceFilter, statusFilter, search]);

  useEffect(() => {
    setSelectedCaseId(initialCaseId);
    setEditorOpen(false);
    setEditingCaseId(null);
    setActionError(null);
  }, [explorationId, initialCaseId]);

  useEffect(() => {
    let current = true;
    if (!explorationId) {
      setListResource({ scope: "", data: null, loading: false, error: null });
      return () => { current = false; };
    }
    const scope = listScope;
    setListResource((old) => ({ scope, data: old.scope === scope ? old.data : null, loading: true, error: null }));
    void loadScopedResource(
      () => request<TestCaseCollection>(testCaseListPath(explorationId, {
        source: sourceFilter,
        status: statusFilter,
        search,
        limit: pageSize,
        offset: caseOffset,
      })),
      () => current,
    ).then((result) => {
      if (!result) return;
      setListResource({ scope, data: result.ok ? result.value : null, loading: false, error: result.ok ? null : result.error });
    });
    return () => { current = false; };
  }, [explorationId, listScope, sourceFilter, statusFilter, search, caseOffset, refresh, request, pageSize]);

  useEffect(() => {
    let current = true;
    if (!explorationId || !selectedCaseId) {
      setDetailResource({ scope: detailScope, data: null, loading: false, error: null });
      setCaseHistory({ scope: caseHistoryScope, data: null, loading: false, error: null });
      return () => { current = false; };
    }
    const scope = detailScope;
    setDetailResource((old) => ({ scope, data: old.scope === scope ? old.data : null, loading: true, error: null }));
    void loadScopedResource(
      () => request<TestCase>(testCaseDetailPath(explorationId, selectedCaseId)),
      () => current,
    ).then((result) => {
      if (!result) return;
      setDetailResource({ scope, data: result.ok ? result.value : null, loading: false, error: result.ok ? null : result.error });
    });
    return () => { current = false; };
  }, [explorationId, selectedCaseId, detailScope, request, refresh]);

  useEffect(() => {
    let current = true;
    if (!explorationId || !selectedCaseId) return () => { current = false; };
    const scope = caseHistoryScope;
    setCaseHistory((old) => ({ scope, data: old.scope === scope ? old.data : null, loading: true, error: null }));
    void loadScopedResource(
      () => request<PageOfRuns>(testRunHistoryPath(explorationId, { caseId: selectedCaseId, limit: pageSize, offset: caseRunOffset })),
      () => current,
    ).then((result) => {
      if (!result) return;
      setCaseHistory({ scope, data: result.ok ? result.value : null, loading: false, error: result.ok ? null : result.error });
    });
    return () => { current = false; };
  }, [explorationId, selectedCaseId, caseHistoryScope, caseRunOffset, request, refresh, pageSize]);

  useEffect(() => {
    let current = true;
    let timer: number | undefined;
    if (!explorationId) {
      setAllHistory({ scope: "", data: null, loading: false, error: null });
      return () => { current = false; };
    }
    const scope = allHistoryScope;
    setAllHistory((old) => ({ scope, data: old.scope === scope ? old.data : null, loading: true, error: null }));
    const load = async () => {
      const result = await loadScopedResource(
        () => request<PageOfRuns>(testRunHistoryPath(explorationId, { limit: pageSize, offset: historyOffset })),
        () => current,
      );
      if (!result) return;
      setAllHistory({ scope, data: result.ok ? result.value : null, loading: false, error: result.ok ? null : result.error });
      if (result.ok && result.value.active_run_count > 0) {
        timer = window.setTimeout(load, 1200);
      }
    };
    void load();
    return () => { current = false; if (timer) window.clearTimeout(timer); };
  }, [explorationId, allHistoryScope, historyOffset, request, refresh, pageSize]);

  useEffect(() => {
    let current = true;
    if (!explorationId || !editorOpen || !editingScenarioAllowed) {
      setObservationResource({ scope: explorationId || "", data: null, loading: false, error: null });
      return () => { current = false; };
    }
    const scope = explorationId;
    setObservationResource((old) => ({ scope, data: old.scope === scope ? old.data : null, loading: true, error: null }));
    void loadScopedResource(
      () => request<EligibleTestObservation[]>(eligibleTestObservationsPath(explorationId)),
      () => current,
    ).then((result) => {
      if (!result) return;
      setObservationResource({ scope, data: result.ok ? result.value : null, loading: false, error: result.ok ? null : result.error });
    });
    return () => { current = false; };
  }, [explorationId, editorOpen, editingScenarioAllowed, request]);

  useEffect(() => {
    if (!selectedCaseId) return;
    setCaseRunOffset(0);
  }, [selectedCaseId, explorationId]);

  const beginCreate = () => {
    setEditingCaseId(null);
    setEditingScenarioAllowed(true);
    setDraft(blankDraft(pages[0]?.id || ""));
    setObservationPageFilter("all");
    setObservationToAdd("");
    setActionError(null);
    setEditorOpen(true);
  };

  const beginEdit = (testCase: TestCase) => {
    setEditingCaseId(testCase.id);
    setEditingScenarioAllowed(testCase.source !== "ai_generated" && testCase.definition.execution_kind === "workflow");
    setDraft({
      name: testCase.name,
      description: testCase.description,
      title: testCase.definition.scenario.title,
      objective: testCase.definition.scenario.objective,
      expected_outcome: testCase.definition.scenario.expected_outcome,
      steps: testCase.definition.scenario.steps.map((step) => {
        const evidence = step.evidence[0];
        if (step.interaction_observation) {
          return {
            kind: "interaction" as const,
            page_id: evidence?.page_id || "",
            element_id: evidence?.element_ids[0] || "",
            observation_id: step.interaction_observation.observation_id,
            expected_state: step.interaction_observation.expected_state,
            expected_state_confirmed: step.interaction_observation.expected_state_confirmed,
            saved_interaction: step.interaction_evidence_snapshot || null,
          };
        }
        const instruction = step.instruction.toLowerCase();
        const kind: StepKind = instruction.startsWith("click") ? "click_link" : instruction.startsWith("navigate") || instruction.startsWith("visit") ? "navigate" : "assert_visible";
        return { kind, page_id: evidence?.page_id || "", element_id: evidence?.element_ids[0] || "" };
      }),
    });
    setObservationPageFilter("all");
    setObservationToAdd("");
    setActionError(null);
    setEditorOpen(true);
  };

  const saveCase = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!explorationId) return;
    if (draft.steps.some((step) => step.kind === "interaction" && !observationStepIsConfirmed(
      step.observation_id,
      step.expected_state,
      step.expected_state_confirmed,
    ))) {
      setActionError("Choose an expected state and confirm the observed interaction before saving this case.");
      return;
    }
    setSaving(true);
    setActionError(null);
    const body: { name: string; description: string; scenario?: WorkflowScenario } = {
      name: draft.name.trim(),
      description: draft.description.trim(),
    };
    if (!editingCaseId || editingScenarioAllowed) body.scenario = scenarioForDraft(draft, pages);
    try {
      if (editingCaseId) await updateTestCase<TestCase>(request, explorationId, editingCaseId, body);
      else await createTestCase<TestCase>(request, explorationId, body);
      setEditorOpen(false);
      setSelectedCaseId(editingCaseId || selectedCaseId);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setActionError(serializeError(reason));
    } finally {
      setSaving(false);
    }
  };

  const duplicateCase = async (testCase: TestCase) => {
    if (!explorationId) return;
    setDuplicatingCaseId(testCase.id);
    setActionError(null);
    try {
      const duplicate = await duplicateTestCase<TestCase>(request, explorationId, testCase.id);
      setSourceFilter("all");
      setStatusFilter("active");
      setSearch("");
      setCaseOffset(0);
      setSelectedCaseId(duplicate.id);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setActionError(serializeError(reason));
    } finally {
      setDuplicatingCaseId(null);
    }
  };

  const deleteCase = async (testCase: TestCase) => {
    if (!explorationId || !window.confirm(`Move "${testCase.name}" to deleted cases? Its saved run history will be preserved.`)) return;
    setDeletingCaseId(testCase.id);
    setActionError(null);
    try {
      await softDeleteTestCase<void>(request, explorationId, testCase.id);
      if (selectedCaseId === testCase.id) setSelectedCaseId(null);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setActionError(serializeError(reason));
    } finally {
      setDeletingCaseId(null);
    }
  };

  const rerunCase = async (testCase: TestCase) => {
    if (!explorationId) return;
    setStartingCaseId(testCase.id);
    setActionError(null);
    try {
      const run = await rerunTestCase<TestRun>(request, explorationId, testCase.id);
      setSelectedCaseId(testCase.id);
      setCaseRunOffset(0);
      setHistoryOffset(0);
      setRefresh((value) => value + 1);
      setTimeout(() => document.getElementById(`test-run-${run.id}`)?.scrollIntoView({ block: "nearest" }), 100);
    } catch (reason) {
      setActionError(serializeError(reason));
    } finally {
      setStartingCaseId(null);
    }
  };

  const cancelRun = async (runId: string) => {
    setCancellingRunId(runId);
    setCancelError(null);
    try {
      await onCancel(runId);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setCancelError(serializeError(reason));
    } finally {
      setCancellingRunId(null);
    }
  };

  const changeStep = (index: number, patch: Partial<DraftStep>) => {
    setDraft((current) => ({
      ...current,
      steps: current.steps.map((step, stepIndex) => stepIndex === index ? { ...step, ...patch } : step),
    }));
  };

  const selectedPageForStep = (step: DraftStep) => pages.find((page) => page.id === step.page_id);
  const eligibleObservations = currentObservations.data || [];
  const observationById = new Map(eligibleObservations.map((item) => [item.id, item]));
  const selectedObservation = observationById.get(observationToAdd);
  const addObservationStep = () => {
    if (!selectedObservation || draft.steps.length >= 20 || draft.steps.some((step) => step.observation_id === selectedObservation.id)) return;
    setDraft((current) => ({
      ...current,
      steps: [...current.steps, {
        kind: "interaction",
        page_id: selectedObservation.page_id,
        element_id: selectedObservation.control.element_id,
        observation_id: selectedObservation.id,
        expected_state: null,
        expected_state_confirmed: false,
      }],
    }));
    setObservationToAdd("");
  };
  const pageCount = Math.ceil((caseCollection?.total || 0) / pageSize);
  const globalRunPageCount = Math.ceil((currentAllHistory.data?.total || 0) / pageSize);
  const selectedHistoryPageCount = Math.ceil((currentCaseHistory.data?.total || 0) / pageSize);

  return (
    <section className="ai-analysis-panel" aria-labelledby="test-execution-title">
      <div className="panel-head ai-analysis-heading">
        <div>
          <h2 id="test-execution-title">Test cases</h2>
          <p>Manage validated scenarios grounded in pages and elements saved by this exploration.</p>
        </div>
      </div>

      {!explorationId && <p className="analysis-empty-note" role="status">Select an exploration to manage its test cases.</p>}
      {actionError && <p className="form-error" role="alert">{actionError}</p>}
      {explorationStatus !== "completed" && <p className="analysis-empty-note" role="status">Complete the browser exploration before creating or running test cases.</p>}

      <section className="analysis-group" aria-labelledby="test-cases-list-title">
        <div className="test-case-toolbar">
          <h3 id="test-cases-list-title">Saved cases</h3>
          <button className="secondary" type="button" onClick={beginCreate} disabled={!explorationId || explorationStatus !== "completed" || pages.length === 0}>
            Create test case
          </button>
        </div>

        <div className="test-case-filters">
          <label>Search cases
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Case name" maxLength={100} />
          </label>
          <label>Source
            <select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}>
              <option value="all">All sources</option>
              <option value="ai_generated">AI generated</option>
              <option value="user_created">User created</option>
            </select>
          </label>
          <label>Status
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="active">Active</option>
              <option value="superseded">Superseded</option>
              <option value="deleted">Deleted</option>
              <option value="all">All</option>
            </select>
          </label>
        </div>

        {analysisStatusLoading && <p className="workspace-message" role="status">Loading AI analysis status…</p>}
        {!analysisStatusLoading && analysisStatusError && <p className="form-error" role="alert">AI analysis status could not be loaded: {analysisStatusError}</p>}
        {!analysisStatusLoading && !analysisStatusError && analysisMessage && (
          resolvedAnalysisStatus === "failed"
            ? <p className="form-error" role="alert">{analysisMessage}</p>
            : <p className="analysis-empty-note" role="status">{analysisMessage}</p>
        )}

        {currentList.loading && !caseCollection && <p className="workspace-message" role="status">Loading saved test cases…</p>}
        {!currentList.loading && currentList.error && <p className="form-error" role="alert">Test cases could not be loaded: {currentList.error}</p>}
        {caseCollection && caseCollection.cases.length > 0 && (
          <div className="analysis-card-grid">
            {caseCollection.cases.map((testCase) => (
              <article className="analysis-card workflow-card" key={testCase.id}>
                <div className="test-run-heading">
                  <h4>{testCase.name}</h4>
                  <span className={`ai-analysis-status ${testCase.status}`} role="status">{statusName(testCase.status)}</span>
                </div>
                <span className="analysis-kind">{testCase.source === "ai_generated" ? "AI generated · evidence validated" : "User created · evidence validated"}</span>
                <p>{testCase.description || caseScenario(testCase).objective}</p>
                <button className="secondary" type="button" onClick={() => setSelectedCaseId(testCase.id)}>View details and history</button>
                <div className="test-case-actions">
                  <button className="secondary" type="button" onClick={() => void rerunCase(testCase)} disabled={testCase.status !== "active" || explorationStatus !== "completed" || hasActiveRun || startingCaseId !== null}>
                    {startingCaseId === testCase.id ? "Queueing…" : "Run case"}
                  </button>
                  <button className="secondary" type="button" onClick={() => beginEdit(testCase)} disabled={testCase.status === "deleted"}>Edit</button>
                  <button className="secondary" type="button" onClick={() => void duplicateCase(testCase)} disabled={testCase.status === "deleted" || duplicatingCaseId !== null}>
                    {duplicatingCaseId === testCase.id ? "Duplicating…" : "Duplicate"}
                  </button>
                  {testCase.status !== "deleted" && (
                    <button className="secondary" type="button" onClick={() => void deleteCase(testCase)} disabled={deletingCaseId !== null}>
                      {deletingCaseId === testCase.id ? "Deleting…" : "Delete"}
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
        {!currentList.loading && !currentList.error && caseCollection && caseCollection.total === 0 && (
          <p className="analysis-empty-note" role="status">{search || sourceFilter !== "all" || statusFilter !== "active" ? "No cases match these filters." : "No saved test cases are available for this exploration yet."}</p>
        )}
        {caseCollection && caseCollection.total > pageSize && (
          <div className="test-case-pagination" aria-label="Test case pages">
            <button className="secondary" type="button" onClick={() => setCaseOffset(Math.max(0, caseOffset - pageSize))} disabled={caseOffset === 0}>Previous cases</button>
            <span>Page {Math.floor(caseOffset / pageSize) + 1} of {pageCount}</span>
            <button className="secondary" type="button" onClick={() => setCaseOffset(caseOffset + pageSize)} disabled={caseOffset + pageSize >= caseCollection.total}>Next cases</button>
          </div>
        )}
      </section>

      {editorOpen && (
        <section className="analysis-group" aria-labelledby="case-editor-title">
          <h3 id="case-editor-title">{editingCaseId ? "Edit test case" : "Create test case"}</h3>
          <form className="test-case-editor" onSubmit={(event) => void saveCase(event)}>
            <label>Case name<input required maxLength={200} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
            <label>Description<textarea maxLength={4000} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></label>
            {!editingCaseId || editingScenarioAllowed ? <>
              <label>Scenario title<input required maxLength={200} value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
              <label>Objective<textarea required maxLength={1000} value={draft.objective} onChange={(event) => setDraft({ ...draft, objective: event.target.value })} /></label>
              <label>Expected outcome<textarea required maxLength={1000} value={draft.expected_outcome} onChange={(event) => setDraft({ ...draft, expected_outcome: event.target.value })} /></label>
            </> : <p className="analysis-empty-note">This scenario is read-only. Its case name and description can be edited; duplicate it to customize the scenario.</p>}
            {(!editingCaseId || editingScenarioAllowed) && <fieldset>
              <legend>Validated steps</legend>
              {draft.steps.map((step, index) => {
                const page = selectedPageForStep(step);
                const observation = step.observation_id ? observationById.get(step.observation_id) : undefined;
                const savedInteraction = observation?.interaction_type
                  ? {
                    interaction_type: observation.interaction_type,
                    control: observation.control,
                    before_state: observation.before_state,
                    observed_after_state: observation.observed_after_state,
                    expected_state: {
                      attribute: observation.interaction_type === "tab" ? "aria-selected" : "aria-expanded",
                      value: observation.observed_after_state.selected ?? observation.observed_after_state.expanded ?? false,
                      user_confirmed: false,
                    },
                  }
                  : step.saved_interaction;
                const elements = page?.elements.filter((element) => step.kind === "click_link"
                  ? element.kind === "link" && element.role === "link" && element.tag_name.toLowerCase() === "a" && element.attributes.inside_form === false && typeof element.attributes.href === "string"
                  : Boolean(element.selector)) || [];
                return (
                  <div className="test-case-step-editor" key={`draft-step-${index}`}>
                    <strong>Step {index + 1}</strong>
                    {step.kind !== "interaction" ? <>
                      <label>Action
                        <select value={step.kind} onChange={(event) => changeStep(index, { kind: event.target.value as StepKind, element_id: "" })}>
                          <option value="navigate">Navigate to saved page</option>
                          <option value="assert_visible">Assert saved element is visible</option>
                          <option value="click_link">Click eligible saved internal link</option>
                        </select>
                      </label>
                      <label>Saved page
                        <select required value={step.page_id} onChange={(event) => changeStep(index, { page_id: event.target.value, element_id: "" })}>
                          <option value="">Choose a saved page</option>
                          {pages.map((pageItem) => <option key={pageItem.id} value={pageItem.id}>{pageItem.title || pageItem.url}</option>)}
                        </select>
                      </label>
                    </> : <div className="interaction-step-summary">
                      <strong>Observed {savedInteraction?.interaction_type || "interaction"}</strong>
                      <span>{savedInteraction?.control?.name || "Control evidence unavailable"} · {page?.title || "Saved page"}</span>
                      <span>Observed: {stateLabel(savedInteraction?.before_state)} → {stateLabel(savedInteraction?.observed_after_state)}</span>
                      {!observation && <small className="form-error">This observation is no longer eligible. Remove it or choose a current observation before saving.</small>}
                    </div>}
                    {step.kind !== "navigate" && step.kind !== "interaction" && (
                      <label>{step.kind === "click_link" ? "Saved internal link" : "Saved element"}
                        <select required value={step.element_id} onChange={(event) => changeStep(index, { element_id: event.target.value })}>
                          <option value="">Choose persisted evidence</option>
                          {elements.map((element) => <option key={element.id} value={element.id}>{elementName(element)} ({element.tag_name})</option>)}
                        </select>
                      </label>
                    )}
                    {step.kind === "interaction" && savedInteraction && (
                      <>
                        <label>Expected state
                          <select
                            required
                            value={step.expected_state === null || step.expected_state === undefined ? "" : String(step.expected_state)}
                            disabled={!observation}
                            onChange={(event) => changeStep(index, {
                              expected_state: event.target.value === "true",
                              expected_state_confirmed: false,
                            })}
                          >
                            <option value="">Choose the expected {savedInteraction.expected_state.attribute} value</option>
                            <option value="true">true</option>
                            <option value="false">false</option>
                          </select>
                        </label>
                        <label className="interaction-confirmation">
                          <input
                            type="checkbox"
                            required
                            disabled={!observation || step.expected_state === null || step.expected_state === undefined}
                            checked={step.expected_state_confirmed === true}
                            onChange={(event) => changeStep(index, { expected_state_confirmed: event.target.checked })}
                          />
                          I confirm aria-{savedInteraction.expected_state.attribute === "aria-selected" ? "selected" : "expanded"} should be {step.expected_state === null || step.expected_state === undefined ? "the selected value" : String(step.expected_state)}.
                        </label>
                      </>
                    )}
                    {draft.steps.length > 1 && <button className="secondary" type="button" onClick={() => setDraft((current) => ({ ...current, steps: current.steps.filter((_, stepIndex) => stepIndex !== index) }))}>Remove step</button>}
                  </div>
                );
              })}
              <button className="secondary" type="button" disabled={draft.steps.length >= 20} onClick={() => setDraft((current) => ({ ...current, steps: [...current.steps, { kind: "navigate", page_id: pages[0]?.id || "", element_id: "" }] }))}>Add step</button>
            </fieldset>}
            {(!editingCaseId || editingScenarioAllowed) && (
              <ObservationStepPicker
                pages={pages}
                observations={eligibleObservations}
                loading={currentObservations.loading}
                error={currentObservations.error}
                pageFilter={observationPageFilter}
                selectedId={observationToAdd}
                onPageFilterChange={(pageId) => { setObservationPageFilter(pageId); setObservationToAdd(""); }}
                onSelectionChange={setObservationToAdd}
                onAdd={addObservationStep}
                alreadyAdded={draft.steps.some((step) => step.observation_id === observationToAdd)}
                disabled={draft.steps.length >= 20}
              />
            )}
            {actionError && <p className="form-error" role="alert">{actionError}</p>}
            <div className="test-case-actions">
              <button className="secondary" type="submit" disabled={saving || explorationStatus !== "completed"}>{saving ? "Saving…" : "Save case"}</button>
              <button className="secondary" type="button" onClick={() => { setEditorOpen(false); setActionError(null); }}>Cancel</button>
            </div>
            <p className="analysis-empty-note">Supported steps are saved-page navigation, visible-element checks, eligible internal links, and explicitly confirmed saved tab or disclosure transitions.</p>
          </form>
        </section>
      )}

      {selectedCaseId && (
        <section className="analysis-group" aria-labelledby="case-detail-title">
          <div className="test-case-toolbar">
            <h3 id="case-detail-title">Test case details</h3>
            <button className="secondary" type="button" onClick={() => setSelectedCaseId(null)}>Close details</button>
          </div>
          {currentDetail.loading && !selectedCase && <p className="workspace-message" role="status">Loading case details…</p>}
          {!currentDetail.loading && currentDetail.error && <p className="form-error" role="alert">Case details could not be loaded: {currentDetail.error}</p>}
          {selectedCase && (
            <article className="analysis-card">
              <h4>{selectedCase.name}</h4>
              <p>{selectedCase.description || "No description provided."}</p>
              <p><strong>Scenario:</strong> {caseScenario(selectedCase).title}</p>
              <p><strong>Objective:</strong> {caseScenario(selectedCase).objective}</p>
              <ol>{caseScenario(selectedCase).steps.map((step, index) => <li key={`${selectedCase.id}-detail-${index}`}>
                {step.instruction}
                {step.interaction_evidence_snapshot && <div className="interaction-step-summary">
                  <strong>Observed {step.interaction_evidence_snapshot.interaction_type}</strong>
                  <span>{step.interaction_evidence_snapshot.control.name} · {step.interaction_evidence_snapshot.control.role}</span>
                  <span>Observed: {stateLabel(step.interaction_evidence_snapshot.before_state)} → {stateLabel(step.interaction_evidence_snapshot.observed_after_state)}</span>
                  <span>User-confirmed expected state: {step.interaction_evidence_snapshot.expected_state.attribute} is {String(step.interaction_evidence_snapshot.expected_state.value)}</span>
                </div>}
              </li>)}</ol>
              <p><strong>Expected outcome:</strong> {caseScenario(selectedCase).expected_outcome}</p>
              {selectedCase.source === "ai_generated" && <p className="analysis-empty-note">AI scenario steps are read-only. Duplicate this case to create an editable user-owned version.</p>}
            </article>
          )}
          <h4>Case run history</h4>
          {currentCaseHistory.loading && !currentCaseHistory.data && <p className="workspace-message" role="status">Loading case run history…</p>}
          {!currentCaseHistory.loading && currentCaseHistory.error && <p className="form-error" role="alert">Case run history could not be loaded: {currentCaseHistory.error}</p>}
          {currentCaseHistory.data?.items.map((run) => <RunCard key={run.id} run={run} cancellingRunId={cancellingRunId} onCancel={cancelRun} />)}
          {currentCaseHistory.data && currentCaseHistory.data.total === 0 && !currentCaseHistory.loading && <p className="analysis-empty-note" role="status">This case has no saved runs.</p>}
          {currentCaseHistory.data && currentCaseHistory.data.total > pageSize && (
            <Pagination page={caseRunOffset} total={currentCaseHistory.data.total} pageSize={pageSize} label="case run history" onChange={setCaseRunOffset} />
          )}
          {selectedHistoryPageCount > 0 && <span className="sr-only">Case history spans {selectedHistoryPageCount} pages.</span>}
        </section>
      )}

      <section className="analysis-group" aria-labelledby="all-runs-title">
        <h3 id="all-runs-title">Execution history</h3>
        {cancelError && <p className="form-error" role="alert">{cancelError}</p>}
        {currentAllHistory.loading && !currentAllHistory.data && <p className="workspace-message" role="status">Loading saved test runs…</p>}
        {!currentAllHistory.loading && currentAllHistory.error && <p className="form-error" role="alert">Execution history could not be loaded: {currentAllHistory.error}</p>}
        {currentAllHistory.data?.items.map((run) => <RunCard key={run.id} run={run} cancellingRunId={cancellingRunId} onCancel={cancelRun} />)}
        {currentAllHistory.data && currentAllHistory.data.total === 0 && !currentAllHistory.loading && <p className="analysis-empty-note" role="status">No browser test runs have been saved.</p>}
        {currentAllHistory.data && currentAllHistory.data.total > pageSize && (
          <Pagination page={historyOffset} total={currentAllHistory.data.total} pageSize={pageSize} label="execution history" onChange={setHistoryOffset} />
        )}
        {globalRunPageCount > 0 && <span className="sr-only">Execution history spans {globalRunPageCount} pages.</span>}
      </section>
    </section>
  );
}

function Pagination({ page, total, pageSize, label, onChange }: { page: number; total: number; pageSize: number; label: string; onChange: (offset: number) => void }) {
  const pageCount = Math.ceil(total / pageSize);
  return (
    <div className="test-case-pagination" aria-label={label}>
      <button className="secondary" type="button" onClick={() => onChange(Math.max(0, page - pageSize))} disabled={page === 0}>Previous</button>
      <span>Page {Math.floor(page / pageSize) + 1} of {pageCount}</span>
      <button className="secondary" type="button" onClick={() => onChange(page + pageSize)} disabled={page + pageSize >= total}>Next</button>
    </div>
  );
}

function RunCard({ run, cancellingRunId, onCancel }: { run: TestRun; cancellingRunId: string | null; onCancel: (runId: string) => void }) {
  const scenario = runScenario(run);
  return (
    <article className="analysis-card" id={`test-run-${run.id}`}>
      <div className="test-run-heading">
        <h4>{scenario.title || "Saved test run"}</h4>
        <span className={`ai-analysis-status ${run.status}`} role="status">{statusName(run.status)}</span>
      </div>
      <small>Run {run.id}</small>
      {run.test_case_id && <small>Case {run.test_case_id}</small>}
      {scenario.objective && <p>{scenario.objective}</p>}
      {run.error_message && <p className="form-error" role="alert">{run.error_message}</p>}
      {activeStatuses.has(run.status) && <button className="secondary" type="button" disabled={cancellingRunId !== null} onClick={() => onCancel(run.id)}>{cancellingRunId === run.id ? "Cancelling…" : "Cancel run"}</button>}
      <ol className="test-run-steps">
        {run.steps.map((step) => (
          <li key={step.id}>
            <div className="test-step-heading"><strong>{step.instruction}</strong><span className={`ai-analysis-status ${step.status}`} role="status">{statusName(step.status)}</span></div>
            {step.action.kind === "interaction_transition" && (
              <p className="interaction-step-result">
                Expected transition: {String(step.action.attribute)} → {String(step.action.expected_value)}
                {typeof step.action.name === "string" ? ` on ${step.action.name}` : ""}
              </p>
            )}
            {step.duration_ms !== null && <small>{step.duration_ms} ms</small>}
            {step.error_message && <small className="test-step-error" role="alert">{step.error_message}</small>}
            {step.actual_evidence && <code className="test-step-evidence">{JSON.stringify(step.actual_evidence)}</code>}
          </li>
        ))}
      </ol>
    </article>
  );
}
