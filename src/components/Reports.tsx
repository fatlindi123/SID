import { useEffect, useMemo, useRef, useState, type ChangeEvent, type Dispatch, type SetStateAction } from "react";

import {
  reportDetailPath,
  reportGenerationPath,
  reportHistoryPath,
  type ReportType,
} from "./reportsApi";
import { useUiPreferences } from "../preferences";

type Request = <T>(path: string, init?: RequestInit) => Promise<T>;
type ReportStatus = "running" | "completed" | "failed";
type ReportRecord = {
  id: string;
  report_type: ReportType;
  project_id: string | null;
  exploration_id: string | null;
  scope_label: string;
  status: ReportStatus;
  generated_at: string;
  completed_at: string | null;
  filters: Record<string, unknown>;
  snapshot?: Record<string, unknown> | null;
  error_code: string | null;
};
type ProjectOption = { id: string; name: string; archived: boolean };
type ExplorationOption = { id: string; start_url: string; status: string };
type Page<T> = { items: T[]; total: number; limit: number; offset: number };
type Resource<T> = { data: T | null; loading: boolean; error: string | null };

const errorText = (error: unknown) => error instanceof Error ? error.message : "The request failed.";
const label = (value: string) => value.replace(/_/g, " ").replace(/\b\w/g, (char: string) => char.toUpperCase());

function formatDate(value: string | null | undefined): string {
  if (!value) return "Unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Unavailable"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function dateFilter(value: string, end = false): string | undefined {
  if (!value) return undefined;
  return new Date(`${value}T${end ? "23:59:59.999" : "00:00:00.000"}`).toISOString();
}

function SnapshotValue({
  value,
  explorationId,
  openSource,
  depth = 0,
}: {
  value: unknown;
  explorationId: string | null;
  openSource: (id: string, tab: string, pageId?: string) => void;
  depth?: number;
}) {
  if (value === null || value === undefined) return <span className="report-unavailable">Unavailable</span>;
  if (typeof value === "boolean") return <span>{value ? "Yes" : "No"}</span>;
  if (typeof value === "string" || typeof value === "number") return <span>{String(value)}</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="report-unavailable">None recorded</span>;
    return <div className="report-value-list">{value.map((item, index) => (
      <div className="report-value-item" key={index}>
        <SnapshotValue value={item} explorationId={explorationId} openSource={openSource} depth={depth + 1} />
      </div>
    ))}</div>;
  }
  if (typeof value !== "object") return <span className="report-unavailable">Unavailable</span>;

  const record = value as Record<string, unknown>;
  const currentExploration = typeof record.exploration_id === "string" ? record.exploration_id : explorationId;
  const pageId = typeof record.page_id === "string"
    ? record.page_id
    : typeof record.id === "string" && typeof record.url === "string" && "http_status" in record
      ? record.id
      : undefined;
  const isCase = typeof record.latest_execution_status === "string";
  const isWorkflow = typeof record.source_type === "string" && Array.isArray(record.steps);
  const isRun = typeof record.step_counts === "object" && "status" in record;
  const sourceTab = pageId ? "Exploration" : isCase || isRun ? "Test Cases" : isWorkflow ? "Workflow Intelligence" : null;
  const sourceId = currentExploration;
  const entries = Object.entries(record);
  return (
    <div className={depth === 0 ? "report-snapshot-object" : "report-snapshot-nested"}>
      {sourceTab && sourceId && <button className="report-source-link" onClick={() => openSource(sourceId, sourceTab, pageId)}>
        {pageId ? "Open source page" : `Open ${sourceTab}`}
      </button>}
      {entries.map(([key, item]) => (
        <section className="report-snapshot-field" key={key}>
          <h4>{label(key)}</h4>
          <SnapshotValue
            value={item}
            explorationId={currentExploration}
            openSource={openSource}
            depth={depth + 1}
          />
        </section>
      ))}
    </div>
  );
}

export function ReportsSection({
  request,
  download,
  print,
  openSource,
  initialReportId = null,
}: {
  request: Request;
  download: (reportId: string, format: "pdf" | "json") => Promise<void>;
  print: (reportId: string) => void;
  openSource: (explorationId: string, tab: string, pageId?: string) => void;
  initialReportId?: string | null;
}) {
  const { pageSize } = useUiPreferences();
  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [explorations, setExplorations] = useState<ExplorationOption[]>([]);
  const [scopeLoading, setScopeLoading] = useState(true);
  const [scopeError, setScopeError] = useState<string | null>(null);
  const [reportType, setReportType] = useState<ReportType>("exploration");
  const [explorationId, setExplorationId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [runFrom, setRunFrom] = useState("");
  const [runTo, setRunTo] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submitRef = useRef(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [page, setPage] = useState(0);
  const [historyType, setHistoryType] = useState<"" | ReportType>("");
  const [historyProject, setHistoryProject] = useState("");
  const [historyExploration, setHistoryExploration] = useState("");
  const [historyFrom, setHistoryFrom] = useState("");
  const [historyTo, setHistoryTo] = useState("");
  const [history, setHistory] = useState<Resource<Page<ReportRecord>>>({ data: null, loading: true, error: null });
  const [selectedId, setSelectedId] = useState<string | null>(initialReportId);
  const [detail, setDetail] = useState<Resource<ReportRecord>>({ data: null, loading: false, error: null });

  useEffect(() => {
    if (initialReportId) setSelectedId(initialReportId);
  }, [initialReportId]);

  useEffect(() => {
    let current = true;
    setScopeLoading(true);
    setScopeError(null);
    Promise.allSettled([
      request<Page<ProjectOption>>("/projects?state=all&limit=100"),
      request<ExplorationOption[]>("/explorations?limit=100"),
    ]).then(([projectResult, explorationResult]) => {
      if (!current) return;
      const errors: string[] = [];
      if (projectResult.status === "fulfilled") setProjects(projectResult.value.items);
      else errors.push(`Projects: ${errorText(projectResult.reason)}`);
      if (explorationResult.status === "fulfilled") setExplorations(explorationResult.value);
      else errors.push(`Explorations: ${errorText(explorationResult.reason)}`);
      setScopeError(errors.length ? errors.join(" ") : null);
      setScopeLoading(false);
    });
    return () => { current = false; };
  }, [request]);

  useEffect(() => {
    if (!explorationId && explorations.length) setExplorationId(explorations[0].id);
    if (!projectId && projects.length) setProjectId(projects[0].id);
  }, [explorationId, explorations, projectId, projects]);

  const historyPath = useMemo(() => reportHistoryPath({
    reportType: historyType || undefined,
    projectId: historyProject || undefined,
    explorationId: historyExploration || undefined,
    generatedFrom: dateFilter(historyFrom),
    generatedTo: dateFilter(historyTo, true),
    limit: pageSize,
    offset: page * pageSize,
  }), [historyType, historyProject, historyExploration, historyFrom, historyTo, page, pageSize]);

  useEffect(() => setPage(0), [pageSize]);

  useEffect(() => {
    let current = true;
    setHistory({ data: null, loading: true, error: null });
    request<Page<ReportRecord>>(historyPath)
      .then((data) => { if (current) setHistory({ data, loading: false, error: null }); })
      .catch((error: unknown) => { if (current) setHistory({ data: null, loading: false, error: errorText(error) }); });
    return () => { current = false; };
  }, [request, historyPath, refresh]);

  useEffect(() => {
    if (!selectedId) {
      setDetail({ data: null, loading: false, error: null });
      return;
    }
    let current = true;
    setDetail({ data: null, loading: true, error: null });
    request<ReportRecord>(reportDetailPath(selectedId))
      .then((data) => { if (current) setDetail({ data, loading: false, error: null }); })
      .catch((error: unknown) => { if (current) setDetail({ data: null, loading: false, error: errorText(error) }); });
    return () => { current = false; };
  }, [request, selectedId]);

  const options = reportType === "project" ? projects : explorations;
  const selectedScopeId = reportType === "project" ? projectId : explorationId;
  const setSelectedScopeId = reportType === "project" ? setProjectId : setExplorationId;
  const activeHistory = history.data?.items || [];
  const currentReport = detail.data?.id === selectedId ? detail.data : null;
  const snapshot = currentReport?.snapshot;
  const scopeErrorView = scopeError && <p className="form-error" role="alert">{scopeError}</p>;

  const generate = async () => {
    if (submitRef.current || !selectedScopeId) return;
    if (runFrom && runTo && new Date(runFrom).getTime() > new Date(runTo).getTime()) {
      setActionError("The run date range is invalid.");
      return;
    }
    submitRef.current = true;
    setSubmitting(true);
    setActionError(null);
    try {
      const result = await request<ReportRecord>(reportGenerationPath(reportType, selectedScopeId), {
        method: "POST",
        body: JSON.stringify({
          ...(runFrom ? { run_created_from: new Date(runFrom).toISOString() } : {}),
          ...(runTo ? { run_created_to: new Date(runTo).toISOString() } : {}),
        }),
      });
      setSelectedId(result.id);
      setRefresh((value) => value + 1);
    } catch (error) {
      setActionError(errorText(error));
      setRefresh((value) => value + 1);
    } finally {
      submitRef.current = false;
      setSubmitting(false);
    }
  };

  const changeHistoryFilter = <T extends string,>(setter: Dispatch<SetStateAction<T>>) => (event: ChangeEvent<HTMLSelectElement | HTMLInputElement>) => {
    setter(event.target.value as T);
    setPage(0);
  };

  return (
    <div className="page reports-page">
      <div className="page-heading">
        <div><p className="eyebrow">WORKSPACE</p><h1>Reports</h1><p>Saved, evidence-backed snapshots of exploration and project data.</p></div>
      </div>

      <section className="report-card" aria-labelledby="generate-report-title">
        <div className="report-section-heading"><div><h2 id="generate-report-title">Generate report</h2><p>Each generation creates a separate immutable report.</p></div></div>
        {scopeErrorView}
        <div className="report-generation-grid">
          <label>Report type<select value={reportType} onChange={(event) => setReportType(event.target.value as ReportType)}>
            <option value="exploration">Exploration</option><option value="project">Project</option>
          </select></label>
          <label>{reportType === "project" ? "Project" : "Exploration"}
            <select value={selectedScopeId} disabled={scopeLoading || options.length === 0} onChange={(event) => setSelectedScopeId(event.target.value)}>
              {options.length === 0 && <option value="">No saved {reportType === "project" ? "projects" : "explorations"}</option>}
              {reportType === "project"
                ? (projects as ProjectOption[]).map((item) => <option key={item.id} value={item.id}>{item.name}{item.archived ? " · Archived" : ""}</option>)
                : (explorations as ExplorationOption[]).map((item) => <option key={item.id} value={item.id}>{item.start_url} · {item.status}</option>)}
            </select>
          </label>
          <label>Run history from<input type="datetime-local" value={runFrom} onChange={(event) => setRunFrom(event.target.value)} /></label>
          <label>Run history to<input type="datetime-local" value={runTo} onChange={(event) => setRunTo(event.target.value)} /></label>
        </div>
        <p className="report-help">Date filters apply only to test runs and “never executed” case classification. Other saved source sections include the full selected scope.</p>
        {actionError && <p className="form-error" role="alert">Report was not generated: {actionError}</p>}
        <button className="primary" disabled={submitting || scopeLoading || !selectedScopeId} onClick={() => void generate()}>
          {submitting ? "Generating…" : "Generate report"}
        </button>
      </section>

      <section className="report-card" aria-labelledby="report-history-title">
        <div className="report-section-heading"><div><h2 id="report-history-title">Report history</h2><p>Opening a saved report reads its stored snapshot.</p></div></div>
        <div className="report-history-filters">
          <label>Type<select value={historyType} onChange={changeHistoryFilter(setHistoryType)}><option value="">All types</option><option value="exploration">Exploration</option><option value="project">Project</option></select></label>
          <label>Project<select value={historyProject} onChange={changeHistoryFilter(setHistoryProject)}><option value="">All projects</option>{projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label>Exploration<select value={historyExploration} onChange={changeHistoryFilter(setHistoryExploration)}><option value="">All explorations</option>{explorations.map((item) => <option key={item.id} value={item.id}>{item.start_url}</option>)}</select></label>
          <label>Generated from<input type="date" value={historyFrom} onChange={changeHistoryFilter(setHistoryFrom)} /></label>
          <label>Generated to<input type="date" value={historyTo} onChange={changeHistoryFilter(setHistoryTo)} /></label>
        </div>
        {history.loading && <p role="status">Loading saved reports…</p>}
        {history.error && <p className="form-error" role="alert">Report history could not be loaded: {history.error}</p>}
        {!history.loading && !history.error && activeHistory.length === 0 && <p className="report-empty" role="status">No saved reports match these filters.</p>}
        {activeHistory.length > 0 && <div className="report-history-list">{activeHistory.map((item) => (
          <button className={`report-history-row${item.id === selectedId ? " is-selected" : ""}`} key={item.id} onClick={() => setSelectedId(item.id)}>
            <span><strong>{item.report_type === "project" ? "Project" : "Exploration"} report</strong><small>{item.scope_label}</small></span>
            <span className={`report-status status-${item.status}`}>{label(item.status)}</span>
            <time dateTime={item.generated_at}>{formatDate(item.generated_at)}</time>
          </button>
        ))}</div>}
        {!history.loading && !history.error && history.data && history.data.total > pageSize && <div className="report-pagination">
          <span>{history.data.offset + 1}–{Math.min(history.data.offset + history.data.items.length, history.data.total)} of {history.data.total}</span>
          <div><button className="secondary" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>Previous</button><button className="secondary" disabled={history.data.offset + history.data.items.length >= history.data.total} onClick={() => setPage((value) => value + 1)}>Next</button></div>
        </div>}
      </section>

      {selectedId && <section className="report-card report-detail" aria-labelledby="saved-report-title">
        <div className="report-section-heading"><div><h2 id="saved-report-title">Saved report</h2><p>{currentReport ? `${currentReport.report_type} · ${currentReport.scope_label} · ${formatDate(currentReport.generated_at)}` : "Loading saved snapshot"}</p></div>
          {currentReport?.status === "completed" && <div className="report-export-actions"><button className="secondary" onClick={() => print(currentReport.id)}>Print view</button><button className="secondary" onClick={() => void download(currentReport.id, "json").catch((error: unknown) => setActionError(errorText(error)))}>Download JSON</button><button className="primary" onClick={() => void download(currentReport.id, "pdf").catch((error: unknown) => setActionError(errorText(error)))}>Download PDF</button></div>}
        </div>
        {detail.loading && <p role="status">Loading saved report snapshot…</p>}
        {detail.error && <p className="form-error" role="alert">Saved report could not be loaded: {detail.error}</p>}
        {currentReport?.status === "failed" && <p className="form-error" role="alert">This report generation failed ({currentReport.error_code || "unknown error"}); no snapshot is available.</p>}
        {currentReport?.status === "running" && <p role="status">Report generation has not finished.</p>}
        {snapshot && <>
          <div className="report-snapshot-meta"><span>Report ID <code>{currentReport?.id}</code></span><span>Generated {formatDate(currentReport?.generated_at)}</span><span>Snapshot schema {String(snapshot.schema_version ?? "Unavailable")}</span></div>
          <SnapshotValue value={snapshot} explorationId={currentReport?.exploration_id || null} openSource={openSource} />
        </>}
      </section>}
    </div>
  );
}
