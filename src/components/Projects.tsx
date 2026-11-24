import { useEffect, useState, type FormEvent } from "react";

import {
  availableExplorationsPath,
  projectArchivePath,
  projectDetailPath,
  projectExplorationAssociationPath,
  projectExplorationsPath,
  projectKnowledgeGraphsPath,
  projectRunHistoryPath,
  projectTestCasesPath,
  projectsPath,
  type ProjectListState,
} from "./projectsApi";
import { WorkflowIntelligenceSection } from "./WorkflowIntelligence";
import { useUiPreferences } from "../preferences";

type Request = <T>(path: string, init?: RequestInit) => Promise<T>;
type Page<T> = { items: T[]; total: number; limit: number; offset: number };

type ProjectStats = {
  exploration_count: number;
  active_exploration_count: number;
  completed_exploration_count: number;
  failed_exploration_count: number;
  test_case_count: number;
  test_run_count: number;
  pending_run_count: number;
  running_run_count: number;
  passed_run_count: number;
  failed_run_count: number;
  knowledge_graph_exploration_count: number;
};

type ProjectRecord = {
  id: string;
  name: string;
  target_url: string;
  description: string;
  environment: string | null;
  archived: boolean;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  stats: ProjectStats;
};

type ExplorationRow = {
  id: string;
  start_url: string;
  status: string;
  pages_discovered: number;
  elements_discovered: number;
  event_count: number;
  created_at: string;
  completed_at: string | null;
  test_case_count: number;
  test_run_count: number;
  knowledge_graph_available: boolean;
};

type TestCaseRow = {
  id: string;
  exploration_id: string;
  exploration_url: string;
  source: string;
  status: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
};

type RunRow = {
  id: string;
  exploration_id: string;
  exploration_url: string;
  test_case_id: string | null;
  status: string;
  error_message: string | null;
  step_count: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

type GraphRow = {
  exploration_id: string;
  exploration_url: string;
  exploration_status: string;
  node_count: number;
  edge_count: number;
  available: boolean;
};

type Resource<T> = { scope: string; data: T | null; loading: boolean; error: string | null };
type DetailTab = "Explorations" | "Test Cases" | "Knowledge Graph" | "Run history" | "Workflow Intelligence";

const DETAIL_TABS: DetailTab[] = ["Explorations", "Test Cases", "Knowledge Graph", "Run history", "Workflow Intelligence"];

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "The project request failed.";
}

function localDate(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

function statusClass(status: string) {
  if (["completed", "passed", "active"].includes(status)) return "project-status completed";
  if (["failed", "deleted"].includes(status)) return "project-status failed";
  if (["running", "pending", "queued"].includes(status)) return "project-status active";
  return "project-status";
}

function ProjectForm({
  project,
  busy,
  error,
  onCancel,
  onSubmit,
}: {
  project: ProjectRecord | null;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onSubmit: (values: { name: string; target_url: string; description: string; environment: string | null }) => Promise<void>;
}) {
  const [name, setName] = useState(project?.name || "");
  const [targetUrl, setTargetUrl] = useState(project?.target_url || "");
  const [description, setDescription] = useState(project?.description || "");
  const [environment, setEnvironment] = useState(project?.environment || "");
  const [localError, setLocalError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLocalError(null);
    const trimmedName = name.trim();
    const trimmedTarget = targetUrl.trim();
    try {
      const target = new URL(trimmedTarget);
      if (!(target.protocol === "http:" || target.protocol === "https:") || target.username || target.password) {
        throw new Error("Use an HTTP or HTTPS website URL without embedded credentials.");
      }
    } catch (reason) {
      setLocalError(reason instanceof Error && reason.message.startsWith("Use ") ? reason.message : "Enter a valid HTTP or HTTPS website URL.");
      return;
    }
    if (!trimmedName) {
      setLocalError("Project name is required.");
      return;
    }
    try {
      await onSubmit({
        name: trimmedName,
        target_url: trimmedTarget,
        description: description.trim(),
        environment: environment.trim() || null,
      });
    } catch {
      // The request error is rendered by the parent so it remains visible after submission.
    }
  };

  return (
    <section className="project-form-card" aria-labelledby="project-form-title">
      <div className="project-section-heading">
        <div><h2 id="project-form-title">{project ? "Edit project" : "Create project"}</h2><p>Project metadata organizes saved exploration results.</p></div>
      </div>
      <form className="project-form" onSubmit={submit}>
        <label>Project name<input required maxLength={200} value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>Target website<input required type="url" maxLength={2048} placeholder="https://app.example.com" value={targetUrl} onChange={(event) => setTargetUrl(event.target.value)} /></label>
        <label>Environment label<input maxLength={100} placeholder="Production, staging, local…" value={environment} onChange={(event) => setEnvironment(event.target.value)} /></label>
        <label className="project-description-field">Description<textarea rows={3} maxLength={4000} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        {(localError || error) && <p className="form-error" role="alert">{localError || error}</p>}
        <div className="project-actions"><button className="secondary" type="button" disabled={busy} onClick={onCancel}>Cancel</button><button className="primary" type="submit" disabled={busy}>{busy ? "Saving…" : project ? "Save changes" : "Create project"}</button></div>
      </form>
    </section>
  );
}

export function ProjectsSection({
  request,
  openExploration,
  initialProjectId = null,
}: {
  request: Request;
  openExploration: (explorationId: string, tab: string, pageId?: string) => void;
  initialProjectId?: string | null;
}) {
  const { pageSize } = useUiPreferences();
  const [search, setSearch] = useState("");
  const [listState, setListState] = useState<ProjectListState>("active");
  const [listPage, setListPage] = useState(0);
  const [listRefresh, setListRefresh] = useState(0);
  const listScope = `${search}|${listState}|${listPage}|${pageSize}|${listRefresh}`;
  const [listResource, setListResource] = useState<Resource<Page<ProjectRecord>>>({ scope: "", data: null, loading: false, error: null });
  const [projectId, setProjectId] = useState<string | null>(initialProjectId);
  const [projectRefresh, setProjectRefresh] = useState(0);
  const [projectResource, setProjectResource] = useState<Resource<ProjectRecord>>({ scope: "", data: null, loading: false, error: null });
  const [detailTab, setDetailTab] = useState<DetailTab>("Explorations");
  const [detailSearch, setDetailSearch] = useState("");
  const [detailPage, setDetailPage] = useState(0);
  const [sectionRefresh, setSectionRefresh] = useState(0);
  const [sectionResource, setSectionResource] = useState<Resource<Page<unknown>>>({ scope: "", data: null, loading: false, error: null });
  const [formProject, setFormProject] = useState<ProjectRecord | null | undefined>(undefined);
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [addExplorationOpen, setAddExplorationOpen] = useState(false);
  const [availableSearch, setAvailableSearch] = useState("");
  const [availableResource, setAvailableResource] = useState<Resource<Page<ExplorationRow>>>({ scope: "", data: null, loading: false, error: null });
  const [assigningId, setAssigningId] = useState<string | null>(null);
  const [removeConfirmationId, setRemoveConfirmationId] = useState<string | null>(null);
  const [archiveConfirmation, setArchiveConfirmation] = useState(false);
  const [archiveBusy, setArchiveBusy] = useState(false);

  useEffect(() => {
    setListPage(0);
    setDetailPage(0);
  }, [pageSize]);

  useEffect(() => {
    if (initialProjectId) setProjectId(initialProjectId);
  }, [initialProjectId]);

  useEffect(() => {
    let current = true;
    setListResource((previous) => ({ scope: listScope, data: previous.scope === listScope ? previous.data : null, loading: true, error: null }));
    void request<Page<ProjectRecord>>(projectsPath({ search, state: listState, limit: pageSize, offset: listPage * pageSize }))
      .then((data) => { if (current) setListResource({ scope: listScope, data, loading: false, error: null }); })
      .catch((error: unknown) => { if (current) setListResource({ scope: listScope, data: null, loading: false, error: errorMessage(error) }); });
    return () => { current = false; };
  }, [request, search, listState, listPage, listRefresh, pageSize]);

  useEffect(() => {
    if (!projectId) {
      setProjectResource({ scope: "", data: null, loading: false, error: null });
      return;
    }
    let current = true;
    const scope = `${projectId}|${projectRefresh}`;
    setProjectResource((previous) => ({ scope, data: previous.scope === scope ? previous.data : null, loading: true, error: null }));
    void request<ProjectRecord>(projectDetailPath(projectId))
      .then((data) => { if (current) setProjectResource({ scope, data, loading: false, error: null }); })
      .catch((error: unknown) => { if (current) setProjectResource({ scope, data: null, loading: false, error: errorMessage(error) }); });
    return () => { current = false; };
  }, [request, projectId, projectRefresh]);

  const sectionScope = projectId ? `${projectId}|${detailTab}|${detailSearch}|${detailPage}|${pageSize}|${sectionRefresh}` : "";
  useEffect(() => {
    if (!projectId) {
      setSectionResource({ scope: "", data: null, loading: false, error: null });
      return;
    }
    let current = true;
    setSectionResource((previous) => ({ scope: sectionScope, data: previous.scope === sectionScope ? previous.data : null, loading: true, error: null }));
    if (detailTab === "Workflow Intelligence") {
      setSectionResource({ scope: sectionScope, data: null, loading: false, error: null });
      return;
    }
    let path = "";
    if (detailTab === "Explorations") path = projectExplorationsPath(projectId, { search: detailSearch, limit: pageSize, offset: detailPage * pageSize });
    if (detailTab === "Test Cases") path = projectTestCasesPath(projectId, { search: detailSearch, limit: pageSize, offset: detailPage * pageSize });
    if (detailTab === "Knowledge Graph") path = projectKnowledgeGraphsPath(projectId, { limit: pageSize, offset: detailPage * pageSize });
    if (detailTab === "Run history") path = projectRunHistoryPath(projectId, { limit: pageSize, offset: detailPage * pageSize });
    void request<Page<unknown>>(path)
      .then((data) => { if (current) setSectionResource({ scope: sectionScope, data, loading: false, error: null }); })
      .catch((error: unknown) => { if (current) setSectionResource({ scope: sectionScope, data: null, loading: false, error: errorMessage(error) }); });
    return () => { current = false; };
  }, [request, projectId, detailTab, detailSearch, detailPage, sectionRefresh, pageSize]);

  const project = projectResource.scope === `${projectId}|${projectRefresh}` ? projectResource.data : null;
  const list = listResource.scope === listScope ? listResource.data : null;
  const section = sectionResource.scope === sectionScope ? sectionResource.data : null;
  const listLoading = listResource.scope !== listScope || listResource.loading;
  const projectLoading = Boolean(projectId) && (projectResource.scope !== `${projectId}|${projectRefresh}` || projectResource.loading);
  const sectionLoading = Boolean(projectId) && (sectionResource.scope !== sectionScope || sectionResource.loading);

  useEffect(() => {
    setDetailTab("Explorations");
    setDetailSearch("");
    setDetailPage(0);
    setActionError(null);
    setAddExplorationOpen(false);
    setRemoveConfirmationId(null);
  }, [projectId]);

  const loadAvailable = (filter: string) => {
    if (!projectId) return;
    const scope = `${projectId}|${filter}`;
    setAvailableResource({ scope, data: null, loading: true, error: null });
    void request<Page<ExplorationRow>>(availableExplorationsPath({ search: filter, limit: 100 }))
      .then((data) => setAvailableResource((old) => old.scope === scope ? { scope, data, loading: false, error: null } : old))
      .catch((error: unknown) => setAvailableResource((old) => old.scope === scope ? { scope, data: null, loading: false, error: errorMessage(error) } : old));
  };

  useEffect(() => {
    if (!addExplorationOpen || !projectId) return;
    loadAvailable(availableSearch);
  }, [addExplorationOpen, projectId, availableSearch]);

  const submitProject = async (values: { name: string; target_url: string; description: string; environment: string | null }) => {
    setFormBusy(true);
    setFormError(null);
    try {
      if (formProject) {
        await request<ProjectRecord>(projectDetailPath(formProject.id), { method: "PATCH", body: JSON.stringify(values) });
        setFormProject(undefined);
        setProjectRefresh((value) => value + 1);
      } else {
        const created = await request<ProjectRecord>("/projects", { method: "POST", body: JSON.stringify(values) });
        setFormProject(undefined);
        setProjectId(created.id);
        setProjectRefresh((value) => value + 1);
      }
      setListRefresh((value) => value + 1);
    } catch (error) {
      const message = errorMessage(error);
      setFormError(message);
      throw error;
    } finally {
      setFormBusy(false);
    }
  };

  const assignExploration = async (explorationId: string) => {
    if (!projectId) return;
    setAssigningId(explorationId);
    setActionError(null);
    try {
      await request(projectExplorationAssociationPath(projectId, explorationId), { method: "PUT" });
      setAddExplorationOpen(false);
      setSectionRefresh((value) => value + 1);
      setProjectRefresh((value) => value + 1);
      setListRefresh((value) => value + 1);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setAssigningId(null);
    }
  };

  const unassignExploration = async (explorationId: string) => {
    if (!projectId) return;
    setActionError(null);
    try {
      await request(projectExplorationAssociationPath(projectId, explorationId), { method: "DELETE" });
      setRemoveConfirmationId(null);
      setSectionRefresh((value) => value + 1);
      setProjectRefresh((value) => value + 1);
      setListRefresh((value) => value + 1);
    } catch (error) {
      setActionError(errorMessage(error));
    }
  };

  const archiveOrRestore = async (restore: boolean) => {
    if (!project) return;
    setArchiveBusy(true);
    setActionError(null);
    try {
      await request<ProjectRecord>(projectArchivePath(project.id, restore), { method: "POST" });
      setArchiveConfirmation(false);
      setProjectRefresh((value) => value + 1);
      setListRefresh((value) => value + 1);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setArchiveBusy(false);
    }
  };

  const pagination = (total: number, page: number, setPage: (value: number) => void) => (
    <div className="project-pagination"><span>{total ? `${page * pageSize + 1}–${Math.min(total, (page + 1) * pageSize)} of ${total}` : "0 results"}</span><div><button className="secondary" type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><button className="secondary" type="button" disabled={(page + 1) * pageSize >= total} onClick={() => setPage(page + 1)}>Next</button></div></div>
  );

  if (formProject !== undefined) {
    return <div className="page projects-page"><div className="page-heading"><div><p className="eyebrow">WORKSPACE</p><h1>Projects</h1></div></div><ProjectForm project={formProject} busy={formBusy} error={formError} onCancel={() => { setFormProject(undefined); setFormError(null); }} onSubmit={submitProject} /></div>;
  }

  if (!projectId) {
    const items = list?.items || [];
    const total = list?.total || 0;
    return (
      <div className="page projects-page">
        <div className="page-heading"><div><p className="eyebrow">WORKSPACE</p><h1>Projects</h1><p>Organize saved explorations, test cases, and knowledge graphs.</p></div><button className="primary" onClick={() => { setFormError(null); setFormProject(null); }}>Create project</button></div>
        <div className="projects-toolbar">
          <label className="projects-search">Search projects<input value={search} onChange={(event) => { setSearch(event.target.value); setListPage(0); }} placeholder="Name, target website, or environment" /></label>
          <label>Show<select value={listState} onChange={(event) => { setListState(event.target.value as ProjectListState); setListPage(0); }}><option value="active">Active</option><option value="archived">Archived</option><option value="all">All projects</option></select></label>
        </div>
        {listResource.error && <p className="form-error" role="alert">Projects could not be loaded: {listResource.error}</p>}
        {listLoading && <p className="workspace-message" role="status">Loading projects…</p>}
        {!listLoading && !listResource.error && items.length === 0 && <div className="project-empty" role="status"><h2>{search ? "No matching projects" : "No projects yet"}</h2><p>Create a project, then associate existing explorations to group their saved results.</p><button className="secondary" onClick={() => { setFormError(null); setFormProject(null); }}>Create project</button></div>}
        {!listResource.error && items.length > 0 && <div className="project-list">{items.map((item) => <button className="project-list-card" key={item.id} onClick={() => setProjectId(item.id)}>
          <span className="project-list-main"><strong>{item.name}</strong><span>{item.target_url}</span><small>{item.description || "No description"}</small></span>
          <span className="project-list-meta">{item.environment && <span className="project-environment">{item.environment}</span>}<span className={item.archived ? "project-status" : "project-status completed"}>{item.archived ? "Archived" : "Active"}</span><span>{item.stats.exploration_count} explorations</span><span>{item.stats.test_case_count} test cases</span><span>{item.stats.test_run_count} runs</span></span>
        </button>)}</div>}
        {!listResource.error && total > pageSize && pagination(total, listPage, setListPage)}
      </div>
    );
  }

  const page = section?.items || [];
  const currentProject = project;
  const total = section?.total || 0;

  return (
    <div className="page projects-page">
      <div className="project-detail-top"><button className="text-button" onClick={() => { setProjectId(null); setDetailPage(0); }}>← All projects</button></div>
      {projectResource.error && <p className="form-error" role="alert">Project could not be loaded: {projectResource.error}</p>}
      {projectLoading && <p className="workspace-message" role="status">Loading project…</p>}
      {currentProject && (
        <>
          <div className="page-heading project-detail-heading"><div><p className="eyebrow">PROJECT</p><h1>{currentProject.name}</h1><p>{currentProject.target_url}{currentProject.environment ? ` · ${currentProject.environment}` : ""}</p></div><div className="project-actions"><button className="secondary" onClick={() => { setFormError(null); setFormProject(currentProject); }}>Edit project</button>{currentProject.archived ? <button className="secondary" disabled={archiveBusy} onClick={() => void archiveOrRestore(true)}>{archiveBusy ? "Updating…" : "Restore project"}</button> : <button className="secondary" disabled={archiveBusy} onClick={() => setArchiveConfirmation(true)}>Archive project</button>}</div></div>
          {currentProject.description && <p className="project-detail-description">{currentProject.description}</p>}
          {actionError && <p className="form-error" role="alert">{actionError}</p>}
          {archiveConfirmation && <div className="project-confirmation" role="alert"><span>Archive this project? Its explorations, cases, runs, graph data, and observations will remain saved. New exploration assignments and test runs will be blocked.</span><div><button className="secondary" disabled={archiveBusy} onClick={() => setArchiveConfirmation(false)}>Cancel</button><button className="primary" disabled={archiveBusy} onClick={() => void archiveOrRestore(false)}>{archiveBusy ? "Archiving…" : "Confirm archive"}</button></div></div>}
          <div className="project-stats-grid">
            <div><strong>{currentProject.stats.exploration_count}</strong><span>Explorations</span><small>{currentProject.stats.active_exploration_count} active · {currentProject.stats.completed_exploration_count} completed</small></div>
            <div><strong>{currentProject.stats.test_case_count}</strong><span>Active test cases</span><small>From associated explorations</small></div>
            <div><strong>{currentProject.stats.test_run_count}</strong><span>Test runs</span><small>{currentProject.stats.passed_run_count} passed · {currentProject.stats.failed_run_count} failed</small></div>
            <div><strong>{currentProject.stats.knowledge_graph_exploration_count}</strong><span>Knowledge graphs</span><small>Explorations with saved graph nodes</small></div>
          </div>
          <nav className="project-tabs" aria-label="Project data">
            {DETAIL_TABS.map((tab) => <button key={tab} className={detailTab === tab ? "active" : ""} onClick={() => { setDetailTab(tab); setDetailPage(0); setDetailSearch(""); }}>{tab}</button>)}
          </nav>
          <div className="project-section-heading"><div><h2>{detailTab}</h2><p>Saved data associated with this project.</p></div>{detailTab === "Explorations" && !currentProject.archived && <button className="secondary" onClick={() => { setAddExplorationOpen((value) => !value); setActionError(null); }}>Add existing exploration</button>}</div>
          {!(["Knowledge Graph", "Workflow Intelligence"] as DetailTab[]).includes(detailTab) && <label className="project-detail-search">Search this section<input value={detailSearch} onChange={(event) => { setDetailSearch(event.target.value); setDetailPage(0); }} placeholder={detailTab === "Test Cases" ? "Search case names" : "Search URLs"} /></label>}
          {detailTab === "Workflow Intelligence" && projectId && <WorkflowIntelligenceSection
            explorationId={null}
            projectId={projectId}
            request={request}
            onOpenEvidence={(explorationId, pageId) => openExploration(explorationId, "Exploration", pageId)}
          />}
          {addExplorationOpen && !currentProject.archived && <div className="available-explorations">
            <label>Find an unassigned exploration<input value={availableSearch} onChange={(event) => setAvailableSearch(event.target.value)} placeholder="Search by URL" /></label>
            {availableResource.loading && <p role="status">Loading unassigned explorations…</p>}
            {availableResource.error && <p className="form-error" role="alert">Could not load explorations: {availableResource.error}</p>}
            {!availableResource.loading && !availableResource.error && (availableResource.data?.items.length || 0) === 0 && <p className="analysis-empty-note">No unassigned explorations match this search.</p>}
            {availableResource.data?.items.map((item) => <div className="available-exploration-row" key={item.id}><span><strong>{item.start_url}</strong><small>{item.status} · {item.pages_discovered} pages · created {localDate(item.created_at)}</small></span><button className="secondary" disabled={assigningId === item.id} onClick={() => void assignExploration(item.id)}>{assigningId === item.id ? "Adding…" : "Add to project"}</button></div>)}
          </div>}
          {detailTab !== "Workflow Intelligence" && sectionResource.error && <p className="form-error" role="alert">{detailTab} could not be loaded: {sectionResource.error}</p>}
          {detailTab !== "Workflow Intelligence" && sectionLoading && <p className="workspace-message" role="status">Loading {detailTab.toLowerCase()}…</p>}
          {detailTab !== "Workflow Intelligence" && !sectionLoading && !sectionResource.error && page.length === 0 && <div className="project-empty compact" role="status"><h3>No {detailTab.toLowerCase()} saved</h3><p>{detailTab === "Explorations" ? "Add an existing unassigned exploration to this project." : "This section will show records when associated explorations have saved data."}</p></div>}

          {!sectionResource.error && detailTab === "Explorations" && page.length > 0 && <div className="project-data-list">{(page as ExplorationRow[]).map((item) => <article className="project-data-row" key={item.id}>
            <div className="project-row-main"><strong>{item.start_url}</strong><span className={statusClass(item.status)}>{item.status}</span><small>{item.pages_discovered} pages · {item.elements_discovered} elements · {item.event_count} events · {item.test_case_count} cases · {item.test_run_count} runs</small></div>
            <div className="project-row-actions"><button className="secondary" onClick={() => openExploration(item.id, "Exploration")}>Open</button><button className="secondary" onClick={() => openExploration(item.id, "Test Cases")}>Test Cases</button><button className="secondary" onClick={() => openExploration(item.id, "Knowledge Graph")}>{item.knowledge_graph_available ? "Open graph" : "Graph status"}</button><button className="secondary" onClick={() => openExploration(item.id, "Workflow Intelligence")}>Workflows</button>{removeConfirmationId === item.id ? <span className="project-inline-confirm"><span>Remove association?</span><button className="text-button" onClick={() => void unassignExploration(item.id)}>Confirm</button><button className="text-button" onClick={() => setRemoveConfirmationId(null)}>Cancel</button></span> : <button className="text-button" onClick={() => setRemoveConfirmationId(item.id)}>Remove</button>}</div>
          </article>)}</div>}

          {!sectionResource.error && detailTab === "Test Cases" && page.length > 0 && <div className="project-data-list">{(page as TestCaseRow[]).map((item) => <article className="project-data-row" key={item.id}><div className="project-row-main"><strong>{item.name}</strong><span className={statusClass(item.status)}>{item.status} · {item.source.replace("_", " ")}</span><small>{item.exploration_url} · {item.description || "No description"}</small></div><button className="secondary" onClick={() => openExploration(item.exploration_id, "Test Cases")}>Open test cases</button></article>)}</div>}

          {!sectionResource.error && detailTab === "Run history" && page.length > 0 && <div className="project-data-list">{(page as RunRow[]).map((item) => <article className="project-data-row" key={item.id}><div className="project-row-main"><strong>{item.exploration_url}</strong><span className={statusClass(item.status)}>{item.status}</span><small>{item.step_count} steps · {localDate(item.created_at)}{item.error_message ? ` · ${item.error_message}` : ""}</small></div><button className="secondary" onClick={() => openExploration(item.exploration_id, "Test Cases")}>Open run history</button></article>)}</div>}

          {!sectionResource.error && detailTab === "Knowledge Graph" && page.length > 0 && <div className="project-data-list">{(page as GraphRow[]).map((item) => <article className="project-data-row" key={item.exploration_id}><div className="project-row-main"><strong>{item.exploration_url}</strong><span className={item.available ? "project-status completed" : "project-status"}>{item.available ? "Saved graph" : "Not built"}</span><small>{item.exploration_status} · {item.node_count} nodes · {item.edge_count} relationships</small></div><button className="secondary" onClick={() => openExploration(item.exploration_id, "Knowledge Graph")}>Open graph</button></article>)}</div>}

          {!sectionResource.error && total > pageSize && pagination(total, detailPage, setDetailPage)}
        </>
      )}
    </div>
  );
}
