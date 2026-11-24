import { useEffect, useMemo, useState } from "react";
import {
  rebuildWorkflowsPath,
  workflowReviewPath,
  workflowsPath,
  type Workflow,
  type WorkflowPage,
  type WorkflowProvenance,
  type WorkflowRebuild,
  type WorkflowState,
} from "./workflowApi";
import { useUiPreferences } from "../preferences";

type Request = <T>(path: string, init?: RequestInit) => Promise<T>;
type PageOption = { id: string; title: string; url: string };
type Resource = { scope: string; data: WorkflowPage | null; loading: boolean; error: string | null };
type Props = {
  explorationId: string | null;
  projectId?: string | null;
  pages?: PageOption[];
  request: Request;
  onOpenEvidence?: (explorationId: string, pageId: string) => void;
  initialSearch?: string | null;
};

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Workflow request failed.";
}

function provenanceLabel(provenance: WorkflowProvenance) {
  if (provenance === "observed") return "Observed";
  if (provenance === "suggested") return "Suggested";
  return "User-authored";
}

function stateSummary(value: Record<string, unknown> | null, fallback: string) {
  if (!value) return fallback;
  const before = value.before as Record<string, unknown> | undefined;
  const after = value.after as Record<string, unknown> | undefined;
  if (before && after) {
    const key = "selected" in before ? "selected" : "expanded";
    return `${String(before[key])} → ${String(after[key])} (${String(value.attribute || key)})`;
  }
  if (value.target_url) return `Saved target: ${String(value.target_url)}`;
  if (value.visible === true) return "Expected evidence: element visible";
  return fallback;
}

export function WorkflowIntelligenceSection({
  explorationId,
  projectId = null,
  pages = [],
  request,
  onOpenEvidence,
  initialSearch = null,
}: Props) {
  const { pageSize } = useUiPreferences();
  const [search, setSearch] = useState("");
  const [provenance, setProvenance] = useState<WorkflowProvenance | "all">("all");
  const [status, setStatus] = useState<WorkflowState | "all">("all");
  const [page, setPage] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [resource, setResource] = useState<Resource>({ scope: "", data: null, loading: false, error: null });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [nameDraft, setNameDraft] = useState("");
  const [descriptionDraft, setDescriptionDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const path = workflowsPath({ explorationId, projectId }, { search, provenance, status, limit: pageSize, offset: page * pageSize });
  const scope = `${projectId || explorationId || ""}|${search}|${provenance}|${status}|${page}|${pageSize}`;

  useEffect(() => setPage(0), [pageSize]);

  useEffect(() => {
    setSearch(initialSearch || "");
    setProvenance("all");
    setStatus("all");
    setPage(0);
    setSelectedId(null);
    setActionError(null);
    setActionMessage(null);
  }, [explorationId, projectId, initialSearch]);

  useEffect(() => {
    let current = true;
    if (!path) {
      setResource({ scope, data: null, loading: false, error: null });
      return () => { current = false; };
    }
    setResource((old) => ({ scope, data: old.scope === scope ? old.data : null, loading: true, error: null }));
    void request<WorkflowPage>(path)
      .then((data) => { if (current) setResource({ scope, data, loading: false, error: null }); })
      .catch((error: unknown) => { if (current) setResource({ scope, data: null, loading: false, error: errorMessage(error) }); });
    return () => { current = false; };
  }, [request, path, scope, refresh]);

  const data = resource.scope === scope ? resource.data : null;
  const loading = Boolean(path) && (resource.scope !== scope || resource.loading);
  const selected = useMemo(() => data?.items.find((item) => item.id === selectedId) || data?.items[0] || null, [data, selectedId]);
  const pageTitle = (id: string) => pages.find((item) => item.id === id)?.title || null;

  useEffect(() => {
    if (!selected) {
      setNameDraft("");
      setDescriptionDraft("");
      return;
    }
    setNameDraft(selected.name);
    setDescriptionDraft(selected.description);
  }, [selected?.id, selected?.updated_at]);

  const rebuild = async () => {
    const rebuildPath = rebuildWorkflowsPath({ explorationId, projectId });
    if (!rebuildPath) return;
    setRebuilding(true);
    setActionError(null);
    setActionMessage(null);
    try {
      const result = await request<WorkflowRebuild>(rebuildPath, { method: "POST" });
      setActionMessage(`Rebuilt saved evidence: ${result.created} new, ${result.updated} updated, ${result.unchanged} unchanged; ${result.marked_stale} marked stale; ${result.skipped_invalid_sources} unsupported or stale sources skipped.`);
      setRefresh((value) => value + 1);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setRebuilding(false);
    }
  };

  const save = async () => {
    if (!selected) return;
    const updatePath = projectId
      ? `/projects/${projectId}/workflows/${selected.id}`
      : `/explorations/${explorationId}/workflows/${selected.id}`;
    setSaving(true);
    setActionError(null);
    setActionMessage(null);
    try {
      await request<Workflow>(updatePath, { method: "PATCH", body: JSON.stringify({ name: nameDraft, description: descriptionDraft }) });
      setActionMessage("Workflow details saved.");
      setRefresh((value) => value + 1);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  const review = async () => {
    if (!selected) return;
    const reviewPath = workflowReviewPath({ explorationId, projectId }, selected.id);
    if (!reviewPath) return;
    setActionError(null);
    setActionMessage(null);
    try {
      await request<Workflow>(reviewPath, { method: "POST" });
      setActionMessage("Workflow marked as reviewed.");
      setRefresh((value) => value + 1);
    } catch (error) {
      setActionError(errorMessage(error));
    }
  };

  if (!path) return <section className="workflow-panel"><div className="workflow-empty">Select an exploration or project to view saved workflows.</div></section>;

  return (
    <section className="workflow-panel" aria-label="Workflow Intelligence">
      <div className="workflow-heading">
        <div><h2>Workflow Intelligence</h2><p>Ordered paths reconstructed from saved pages, controls, interaction records, and validated test cases.</p></div>
        <button className="secondary" disabled={rebuilding} onClick={() => void rebuild()}>{rebuilding ? "Rebuilding…" : "Rebuild from saved evidence"}</button>
      </div>
      <div className="workflow-explanation"><strong>Evidence labels</strong><span><b>Observed</b> means one isolated interaction transition was recorded. <b>Suggested</b> means a safe saved link or AI-authored test case provides a candidate path; no link click or test execution is implied. <b>User-authored</b> steps come from a saved manual test case.</span></div>
      {actionError && <p className="form-error" role="alert">{actionError}</p>}
      {actionMessage && <p className="workflow-message" role="status">{actionMessage}</p>}
      <div className="workflow-filters">
        <label>Search<input value={search} onChange={(event) => { setSearch(event.target.value); setPage(0); }} placeholder="Workflow name" /></label>
        <label>Provenance<select value={provenance} onChange={(event) => { setProvenance(event.target.value as WorkflowProvenance | "all"); setPage(0); }}><option value="all">All sources</option><option value="observed">Observed</option><option value="suggested">Suggested</option><option value="user_authored">User-authored</option></select></label>
        <label>Status<select value={status} onChange={(event) => { setStatus(event.target.value as WorkflowState | "all"); setPage(0); }}><option value="all">All statuses</option><option value="active">Active</option><option value="stale">Stale evidence</option><option value="reviewed">Reviewed</option><option value="unreviewed">Unreviewed</option></select></label>
        {data && <span>{data.total} saved workflows</span>}
      </div>
      {loading && <p className="workflow-message" role="status">Loading saved workflows…</p>}
      {resource.error && resource.scope === scope && <p className="form-error" role="alert">Could not load workflows: {resource.error}</p>}
      {!loading && !resource.error && data?.total === 0 && <div className="workflow-empty"><strong>No workflows found</strong><span>Rebuild discovery after a completed exploration to inspect available evidence-backed paths.</span></div>}
      {!loading && !resource.error && Boolean(data?.items.length) && (
        <div className="workflow-layout">
          <div className="workflow-list" role="list" aria-label="Saved workflows">
            {data!.items.map((item) => <button className={`workflow-list-item ${selected?.id === item.id ? "active" : ""}`} key={item.id} onClick={() => setSelectedId(item.id)}>
              <span><strong>{item.name}</strong><small>{provenanceLabel(item.provenance)} · {item.steps.length} {item.steps.length === 1 ? "step" : "steps"}</small></span>
              <span className={`workflow-status ${item.status}`}>{item.status}{item.reviewed ? " · reviewed" : ""}</span>
            </button>)}
            {data!.total > pageSize && <div className="workflow-pagination"><button className="secondary" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>Previous</button><span>{page + 1} / {Math.ceil(data!.total / pageSize)}</span><button className="secondary" disabled={(page + 1) * pageSize >= data!.total} onClick={() => setPage((value) => value + 1)}>Next</button></div>}
          </div>
          {selected && <article className="workflow-detail">
            <div className="workflow-detail-heading"><div><span className={`workflow-status ${selected.status}`}>{selected.status} · {provenanceLabel(selected.provenance)}</span><h3>{selected.name}</h3><p>{selected.description || "No description saved."}</p></div><button className="secondary" disabled={selected.reviewed} onClick={() => void review()}>{selected.reviewed ? "Reviewed" : "Mark reviewed"}</button></div>
            <div className="workflow-edit-fields"><label>Name<input maxLength={200} value={nameDraft} onChange={(event) => setNameDraft(event.target.value)} /></label><label>Description<textarea maxLength={4000} rows={2} value={descriptionDraft} onChange={(event) => setDescriptionDraft(event.target.value)} /></label><button className="secondary" disabled={saving || !nameDraft.trim()} onClick={() => void save()}>{saving ? "Saving…" : "Save details"}</button></div>
            <ol className="workflow-steps">{selected.steps.map((step) => <li key={step.id}>
              <div className="workflow-step-head"><span>Step {step.ordinal + 1}</span><span className={`workflow-provenance ${step.provenance}`}>{provenanceLabel(step.provenance)}</span><span>{step.step_type.replace(/_/g, " ")}</span></div>
              <p>{step.instruction}</p>
              <small>{step.page_title || "Untitled page"} · {step.page_url}</small>
              {step.control_name && <small>Control: {step.control_name}</small>}
              {step.observed_state && <small>Observed transition: {stateSummary(step.observed_state, "Saved state change")}</small>}
              {step.expected_state && <small>{step.provenance === "suggested" ? "Suggested evidence" : "Saved expected evidence"}: {stateSummary(step.expected_state, "State is documented in saved evidence")}</small>}
              {step.target_page_id && <small>Target page: {pageTitle(step.target_page_id) || step.target_page_id}</small>}
              <div className="workflow-evidence-actions">
                <button className="text-button" onClick={() => onOpenEvidence?.(selected.exploration_id, step.page_id)}>Open page evidence</button>
                {step.target_page_id && <button className="text-button" onClick={() => onOpenEvidence?.(selected.exploration_id, step.target_page_id!)}>Open target evidence</button>}
                <span>{step.evidence.length} evidence references</span>
              </div>
            </li>)}</ol>
          </article>}
        </div>
      )}
    </section>
  );
}
