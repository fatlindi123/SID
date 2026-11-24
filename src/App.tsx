import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { ExplorationPagesAndElements, ExplorationSummary } from "./components/ExplorationResults";
import { AIAnalysisSection, type AIAnalysisResult, type AnalysisStatusResponse } from "./components/AIAnalysis";
import { TestExecutionSection } from "./components/TestExecution";
import { InteractionObservations, type InteractionObservation } from "./components/InteractionObservations";
import { KnowledgeGraphSection } from "./components/KnowledgeGraph";
import { ProjectsSection } from "./components/Projects";
import { WorkflowIntelligenceSection } from "./components/WorkflowIntelligence";
import { ReportsSection } from "./components/Reports";
import { SettingsSection } from "./components/Settings";
import { GlobalSearch, searchDestination, type SearchResult } from "./components/GlobalSearch";
import { AccountMenu, avatarInitials } from "./components/AccountMenu";
import { WelcomeExperience } from "./components/WelcomeExperience";
import { reportExportPath } from "./components/reportsApi";
import { readPreferences } from "./preferences";
import { sidebarWorkspace, workspaceFromSearch, workspaceLocation } from "./navigation";
import { apiRequest, API_BASE, ensureCsrf, setCsrfToken, ApiRequestError, type AuthenticatedUser } from "./apiClient";
import { AuthExperience } from "./components/AuthExperience";

type ExplorationStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
type ScopedAnalysisError = { exploration_id: string; message: string };

type Exploration = {
  id: string;
  start_url: string;
  status: ExplorationStatus;
  current_url: string | null;
  max_pages: number;
  max_depth: number;
  pages_discovered: number;
  elements_discovered: number;
  event_count: number;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
};

type ObservedElement = {
  id: string;
  ordinal: number;
  kind: string;
  tag_name: string;
  role: string | null;
  accessible_name: string | null;
  label: string | null;
  text: string | null;
  selector: string | null;
  attributes: Record<string, string | boolean>;
};

type ExplorationPage = {
  id: string;
  url: string;
  title: string;
  description: string | null;
  depth: number;
  http_status: number | null;
  visible_text: string;
  dom_outline: { tag_name: string; role: string | null; heading_level: number | null; name: string | null; label: string | null }[];
  element_count: number;
  screenshot_url: string | null;
  discovered_at: string;
  elements: ObservedElement[];
};

type ExplorationEvent = {
  id: string;
  sequence: number;
  event_type: string;
  message: string;
  url: string | null;
  details: Record<string, unknown>;
  created_at: string;
};

function statusTone(status: ExplorationStatus): "green" | "blue" | "gray" | "amber" {
  if (status === "completed") return "green";
  if (status === "running" || status === "queued") return "blue";
  if (status === "failed") return "amber";
  return "gray";
}

function statusLabel(status: ExplorationStatus): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function formatTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(value),
  );
}

function hostLabel(value: string): string {
  try {
    return new URL(value).host;
  } catch {
    return value;
  }
}

type IconName =
  | "overview"
  | "folder"
  | "compass"
  | "graph"
  | "check"
  | "report"
  | "settings"
  | "plus"
  | "chevron"
  | "arrow"
  | "play"
  | "pause"
  | "external"
  | "globe"
  | "cursor"
  | "workflow"
  | "clock"
  | "code"
  | "menu"
  | "close"
  | "spark"
  | "shield";

const paths: Record<IconName, ReactNode> = {
  overview: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </>
  ),
  folder: (
    <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H10l2 2h6.5A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5z" />
  ),
  compass: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m15.2 8.8-2 4.4-4.4 2 2-4.4z" />
    </>
  ),
  graph: (
    <>
      <circle cx="6" cy="7" r="2.5" />
      <circle cx="18" cy="6" r="2.5" />
      <circle cx="12" cy="18" r="2.5" />
      <path d="m8.3 7.6 7.4-.9M7.5 9l3.3 6.8m6-7.5-3.5 7.5" />
    </>
  ),
  check: (
    <>
      <path d="M5 3h14v18H5z" />
      <path d="m8 12 2.3 2.3L16 8.7" />
    </>
  ),
  report: (
    <>
      <path d="M5 3h10l4 4v14H5z" />
      <path d="M14 3v5h5M8 13h8M8 17h6" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  chevron: <path d="m9 18 6-6-6-6" />,
  arrow: <path d="m5 12 14 0m-5-5 5 5-5 5" />,
  play: <path d="m8 5 11 7-11 7z" />,
  pause: <path d="M9 5v14M15 5v14" />,
  external: (
    <>
      <path d="M14 4h6v6M20 4l-9 9" />
      <path d="M18 13v6H5V6h6" />
    </>
  ),
  globe: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" />
    </>
  ),
  cursor: <path d="m5 3 14 9-7 2-3 7z" />,
  workflow: (
    <>
      <rect x="3" y="4" width="7" height="5" rx="1" />
      <rect x="14" y="15" width="7" height="5" rx="1" />
      <path d="M6.5 9v4h11v2" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  code: <path d="m8 8-4 4 4 4m8-8 4 4-4 4m-2-11-4 14" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  spark: <path d="m12 3 1.4 5.1L18 10l-4.6 1.9L12 17l-1.4-5.1L6 10l4.6-1.9z" />,
  shield: (
    <>
      <path d="M12 3 4.5 6v5.5c0 4.7 3.2 8 7.5 9.5 4.3-1.5 7.5-4.8 7.5-9.5V6z" />
      <path d="m8.5 12 2.2 2.2 4.8-5" />
    </>
  ),
};

function Icon({
  name,
  size = 18,
  className = "",
}: {
  name: IconName;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}

const nav = [
  ["Overview", "overview"],
  ["Projects", "folder"],
  ["Exploration", "compass"],
  ["Knowledge Graph", "graph"],
  ["Workflow Intelligence", "workflow"],
  ["Test Cases", "check"],
  ["Reports", "report"],
  ["Settings", "settings"],
] as [string, IconName][];

function Logo({ onClick }: { onClick: () => void }) {
  return (
    <button className="brand-button" type="button" onClick={onClick} aria-label="SID — Overview">
      <div className="logo-mark">
        <span />
        <span />
        <span />
      </div>
      <span className="text-[14px] font-semibold tracking-[-.01em] text-zinc-900">SID</span>
    </button>
  );
}

function Status({
  children,
  tone = "green",
}: {
  children: ReactNode;
  tone?: "green" | "blue" | "gray" | "amber";
}) {
  return (
    <span className={`status status-${tone}`}>
      <i />
      {children}
    </span>
  );
}

function Header({
  active,
  onMenu,
  onNew,
  onNavigate,
  onOverview,
  onOpenWorkspaceMenu,
  workspaceMenuOpen,
  user,
  request,
}: {
  active: string;
  onMenu: () => void;
  onNew: () => void;
  onNavigate: (result: SearchResult) => void;
  onOverview: () => void;
  onOpenWorkspaceMenu: (anchor: HTMLElement) => void;
  workspaceMenuOpen: boolean;
  user: AuthenticatedUser;
  request: <T>(path: string, init?: RequestInit) => Promise<T>;
}) {
  return (
    <header className="topbar">
      <button className="mobile-menu" onClick={onMenu} aria-label="Open navigation">
        <Icon name="menu" />
      </button>
      <div className="crumbs">
        <button type="button" onClick={onOverview}>Workspace</button>
        <Icon name="chevron" size={13} />
        <button type="button" onClick={onOverview}>SID</button>
        {active !== "Overview" && <><Icon name="chevron" size={13} /><span aria-current="page">{active}</span></>}
      </div>
      <div className="top-actions">
        <GlobalSearch request={request} onSelect={onNavigate} />
        <button className="primary small" onClick={onNew}>
          <Icon name="plus" size={15} /> New exploration
        </button>
        <button className="avatar avatar-button" type="button" aria-label="Open account menu" aria-haspopup="menu" aria-expanded={workspaceMenuOpen} title="Account menu" onClick={(event) => onOpenWorkspaceMenu(event.currentTarget)}>
          {avatarInitials(user.full_name)}
        </button>
      </div>
    </header>
  );
}

export function Sidebar({
  active,
  onSelect,
  open,
  close,
  activeExplorations,
  onOpenWorkspaceMenu,
  workspaceMenuOpen,
  user,
}: {
  active: string;
  onSelect: (x: string) => void;
  open: boolean;
  close: () => void;
  activeExplorations: number;
  onOpenWorkspaceMenu: (anchor: HTMLElement) => void;
  workspaceMenuOpen: boolean;
  user: AuthenticatedUser;
}) {
  return (
    <>
      {open && <button className="side-scrim" onClick={close} aria-label="Close navigation" />}
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="side-head">
          <Logo onClick={() => { onSelect("Overview"); close(); }} />
          <button className="mobile-close" onClick={close}>
            <Icon name="close" />
          </button>
        </div>
        <div className="workspace-switch" aria-label="SID workspace">
          <div className="workspace-logo">S</div>
          <div>
            <strong>SID</strong>
            <span>Local workspace</span>
          </div>
        </div>
        <nav>
          <p className="nav-label">Workspace</p>
          {nav.slice(0, 7).map(([label, icon]) => (
            <button
              key={label}
              className={active === label ? "active" : ""}
              onClick={() => {
                onSelect(label);
                close();
              }}
            >
              <Icon name={icon} />
              <span>{label}</span>
              {label === "Exploration" && activeExplorations > 0 && <em>{activeExplorations}</em>}
            </button>
          ))}
          <p className="nav-label lower">Application</p>
          <button
            className={active === "Settings" ? "active" : ""}
            onClick={() => {
              onSelect("Settings");
              close();
            }}
          >
            <Icon name="settings" />
            <span>Settings</span>
          </button>
        </nav>
        <div className="side-footer">
          <button className="profile" type="button" aria-label="Open account menu" aria-haspopup="menu" aria-expanded={workspaceMenuOpen} onClick={(event) => onOpenWorkspaceMenu(event.currentTarget)}>
            <div className="avatar">{avatarInitials(user.full_name)}</div>
            <div>
              <strong>{user.full_name}</strong>
              <span>{user.email}</span>
            </div>
            <Icon name="chevron" size={14} />
          </button>
        </div>
      </aside>
    </>
  );
}

function Overview({
  explorations,
  onNew,
  openProject,
  loadError,
}: {
  explorations: Exploration[];
  onNew: () => void;
  openProject: (id: string) => void;
  loadError: string | null;
}) {
  const active = explorations.filter((item) => item.status === "queued" || item.status === "running");
  const latest = active[0];
  const recent = explorations.slice(0, 4);
  const totals = [
    ["globe", String(explorations.length), "Recent explorations", "Latest 100 saved records"],
    ["cursor", String(explorations.reduce((sum, item) => sum + item.pages_discovered, 0)), "Pages discovered", "Across loaded explorations"],
    ["workflow", String(explorations.reduce((sum, item) => sum + item.elements_discovered, 0)), "Interface elements", "Across loaded explorations"],
    ["clock", String(active.length), "Queued or running", "Among loaded explorations"],
  ] as const;

  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">INTELLIGENCE WORKSPACE</p>
          <h1>SID</h1>
          <p>Understand software without reading its documentation.</p>
        </div>
        <button className="primary" onClick={onNew}>
          <Icon name="plus" size={16} /> New exploration
        </button>
      </div>
      {loadError && <p className="form-error" role="alert">{loadError}</p>}
      <section>
        <div className="section-title">
          <div>
            <h2>Active explorations</h2>
            <p>Live status from the browser worker.</p>
          </div>
          <button className="text-button" onClick={() => latest && openProject(latest.id)}>
            View active <Icon name="arrow" size={14} />
          </button>
        </div>
        <div className="active-card">
          {latest ? (
            <>
              <div className="active-info">
                <div className="app-icon atlas">{hostLabel(latest.start_url).slice(0, 1).toUpperCase()}</div>
                <div>
                  <div className="title-row">
                    <h3>{hostLabel(latest.start_url)}</h3>
                    <Status tone={statusTone(latest.status)}>{statusLabel(latest.status)}</Status>
                  </div>
                  <a href={latest.start_url} target="_blank" rel="noreferrer">
                    {latest.start_url} <Icon name="external" size={12} />
                  </a>
                </div>
              </div>
              <div className="progress-wrap">
                <div>
                  <span>Pages inspected</span>
                  <strong>{latest.pages_discovered} / {latest.max_pages}</strong>
                </div>
                <div className="progress">
                  <i style={{ width: Math.min(100, latest.pages_discovered / latest.max_pages * 100) + "%" }} />
                </div>
                <p>Current page: <b>{latest.current_url || "Waiting for browser worker"}</b></p>
              </div>
              <div className="active-stats">
                <div><strong>{latest.pages_discovered}</strong><span>Pages</span></div>
                <div><strong>{latest.elements_discovered}</strong><span>Elements</span></div>
                <div><strong>{latest.max_depth}</strong><span>Max depth</span></div>
              </div>
              <button className="secondary" onClick={() => openProject(latest.id)}>
                View results <Icon name="arrow" size={14} />
              </button>
            </>
          ) : (
            <div className="empty-state">
              <strong>No active explorations</strong>
              <span>Start an exploration to inspect a public website.</span>
              <button className="secondary" onClick={onNew}>New exploration</button>
            </div>
          )}
        </div>
      </section>
      <div className="overview-grid">
        <section>
          <div className="section-title">
            <div>
              <h2>Recent explorations</h2>
              <p>Targets and results saved by the backend.</p>
            </div>
          </div>
          <div className="apps-list">
            {recent.length ? recent.map((item) => (
              <button className="app-row" key={item.id} onClick={() => openProject(item.id)}>
                <div className="app-icon north">{hostLabel(item.start_url).slice(0, 1).toUpperCase()}</div>
                <div className="app-name">
                  <strong>{hostLabel(item.start_url)}</strong>
                  <span>{item.start_url}</span>
                </div>
                <div className="app-meta">
                  <span>{item.pages_discovered} pages</span>
                  <span>{item.elements_discovered} elements</span>
                </div>
                <span className="app-date">{statusLabel(item.status)}</span>
                <Icon name="chevron" size={16} />
              </button>
            )) : (
              <div className="empty-state compact">
                <span>No exploration records yet.</span>
              </div>
            )}
          </div>
        </section>
        <section>
          <div className="section-title">
            <div>
              <h2>Exploration activity</h2>
              <p>Most recent persisted state changes.</p>
            </div>
          </div>
          <div className="timeline">
            {recent.length ? recent.slice(0, 4).map((item, index) => (
              <div className="event" key={item.id}>
                <div className={"event-icon " + (item.status === "failed" ? "amber" : index === 0 ? "blue" : "green")}>
                  <Icon name={item.status === "failed" ? "shield" : item.status === "completed" ? "check" : "globe"} size={15} />
                </div>
                <div>
                  <div>
                    <strong>Exploration {statusLabel(item.status).toLowerCase()}</strong>
                    <span>{formatTime(item.updated_at)}</span>
                  </div>
                  <p>{hostLabel(item.start_url)} · {item.pages_discovered} pages · {item.elements_discovered} elements</p>
                </div>
                {index < Math.min(recent.length, 4) - 1 && <i className="line" />}
              </div>
            )) : (
              <div className="empty-state compact"><span>No activity yet.</span></div>
            )}
          </div>
        </section>
      </div>
      <section className="discoveries">
        <div className="section-title">
          <div>
            <h2>Discovery summary</h2>
            <p>Counts from exploration records in this workspace.</p>
          </div>
        </div>
        <div className="summary-grid">
          {totals.map((item, index) => (
            <div className="summary" key={item[2]}>
              <div className={"summary-icon s" + index}><Icon name={item[0]} /></div>
              <div>
                <strong>{item[1]}</strong>
                <span>{item[2]}</span>
                <p>{item[3]}</p>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function ProjectWorkspace({
  initialTab = "Exploration",
  explorationId,
  explorations,
  onRefresh,
  loadError,
  initialPageId,
  initialTestCaseId,
  initialWorkflowSearch,
  onInitialPageHandled,
  onNavigateTab,
}: {
  initialTab?: string;
  explorationId: string | null;
  explorations: Exploration[];
  onRefresh: () => Promise<void>;
  loadError: string | null;
  initialPageId?: string | null;
  initialTestCaseId?: string | null;
  initialWorkflowSearch?: string | null;
  onInitialPageHandled?: () => void;
  onNavigateTab: (tab: string) => void;
}) {
  const tab = initialTab;
  const [pages, setPages] = useState<ExplorationPage[]>([]);
  const [events, setEvents] = useState<ExplorationEvent[]>([]);
  const [interactions, setInteractions] = useState<InteractionObservation[]>([]);
  const [interactionsLoading, setInteractionsLoading] = useState(false);
  const [interactionsError, setInteractionsError] = useState<string | null>(null);
  const [selectedPageId, setSelectedPageId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pagesError, setPagesError] = useState<string | null>(null);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [analysisStatus, setAnalysisStatus] = useState<AnalysisStatusResponse | null>(null);
  const [analysisResult, setAnalysisResult] = useState<AIAnalysisResult | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(true);
  const [analysisStarting, setAnalysisStarting] = useState(false);
  const [analysisError, setAnalysisError] = useState<ScopedAnalysisError | null>(null);
  const [analysisRefresh, setAnalysisRefresh] = useState(0);
  const lastEventSequence = useRef(0);
  useEffect(() => {
    if (initialPageId && pages.some((page) => page.id === initialPageId)) {
      setSelectedPageId(initialPageId);
      onNavigateTab("Exploration");
      onInitialPageHandled?.();
    }
  }, [initialPageId, pages, onInitialPageHandled, onNavigateTab]);

  const exploration = explorations.find((item) => item.id === explorationId) || null;
  const isActive = exploration?.status === "queued" || exploration?.status === "running";

  useEffect(() => {
    setSelectedPageId(null);
    setPages([]);
    setEvents([]);
    setInteractions([]);
    setInteractionsError(null);
    setError(null);
    setPagesError(null);
    setEventsError(null);
    setEventsLoading(false);
    lastEventSequence.current = 0;
  }, [explorationId]);

  useEffect(() => {
    if (!explorationId) {
      setInteractionsLoading(false);
      setInteractions([]);
      return;
    }
    let disposed = false;
    let timer: number | undefined;
    setInteractionsLoading(true);
    setInteractionsError(null);
    const loadInteractions = async () => {
      try {
        const saved = await apiRequest<InteractionObservation[]>(
          "/explorations/" + explorationId + "/interactions?limit=200",
        );
        if (!disposed) {
          setInteractions(saved);
          setInteractionsError(null);
        }
      } catch (reason) {
        if (!disposed) {
          setInteractionsError(reason instanceof Error ? reason.message : "Could not load saved interactions.");
        }
      } finally {
        if (!disposed) {
          setInteractionsLoading(false);
          if (isActive) timer = window.setTimeout(loadInteractions, 1500);
        }
      }
    };
    void loadInteractions();
    return () => {
      disposed = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [explorationId, isActive]);

  useEffect(() => {
    if (!explorationId) return;
    let disposed = false;
    setLoading(true);
    setPagesError(null);
    apiRequest<ExplorationPage[]>("/explorations/" + explorationId + "/pages")
      .then((nextPages) => {
        if (disposed) return;
        setPages(nextPages);
        setSelectedPageId((current) =>
          current && nextPages.some((page) => page.id === current) ? current : nextPages[0]?.id || null,
        );
      })
      .catch((reason) => {
        if (!disposed) setPagesError(reason instanceof Error ? reason.message : "Could not load exploration pages.");
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [explorationId, exploration?.pages_discovered, exploration?.status]);

  useEffect(() => {
    if (!explorationId) return;
    let disposed = false;
    let timer: number | undefined;
    let after = lastEventSequence.current;
    setEventsLoading(true);
    const loadEvents = async () => {
      try {
        while (!disposed) {
          const nextEvents = await apiRequest<ExplorationEvent[]>(
            "/explorations/" + explorationId + "/events?limit=500&after=" + after,
          );
          if (disposed) break;
          if (nextEvents.length) {
            after = nextEvents[nextEvents.length - 1].sequence;
            lastEventSequence.current = after;
            setEvents((current) => [...current, ...nextEvents]);
          }
          if (nextEvents.length < 500) break;
        }
        if (!disposed) setEventsError(null);
      } catch (reason) {
        if (!disposed) setEventsError(reason instanceof Error ? reason.message : "Could not load exploration events.");
      } finally {
        if (!disposed) setEventsLoading(false);
      }
      if (!disposed && isActive) timer = window.setTimeout(loadEvents, 2200);
    };
    void loadEvents();
    return () => {
      disposed = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [explorationId, isActive]);

  useEffect(() => {
    if (!explorationId) {
      setAnalysisLoading(false);
      setAnalysisStatus(null);
      setAnalysisResult(null);
      return;
    }
    let disposed = false;
    let timer: number | undefined;
    setAnalysisLoading(true);
    setAnalysisError(null);

    const loadAnalysis = async () => {
      let nextStatus: AnalysisStatusResponse | null = null;
      try {
        nextStatus = await apiRequest<AnalysisStatusResponse>(
          "/explorations/" + explorationId + "/analysis",
        );
        if (disposed) return;
        setAnalysisStatus(nextStatus);
        if (nextStatus.status === "completed") {
          const saved = await apiRequest<{ result: AIAnalysisResult }>(
            "/explorations/" + explorationId + "/analysis/results",
          );
          if (!disposed) setAnalysisResult(saved.result);
        } else {
          setAnalysisResult(null);
        }
        if (!disposed) setAnalysisError(null);
      } catch (reason) {
        if (!disposed) {
          setAnalysisError({
            exploration_id: explorationId,
            message: reason instanceof Error ? reason.message : "Could not load AI analysis.",
          });
        }
      } finally {
        if (!disposed) {
          setAnalysisLoading(false);
          if (nextStatus?.status === "queued" || nextStatus?.status === "running") {
            timer = window.setTimeout(loadAnalysis, 1500);
          }
        }
      }
    };
    void loadAnalysis();
    return () => {
      disposed = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [explorationId, analysisRefresh]);

  const currentAnalysisStatus = analysisStatus?.exploration_id === explorationId ? analysisStatus : null;
  const currentAnalysisError = analysisError?.exploration_id === explorationId ? analysisError.message : null;
  const currentAnalysisStatusLoading = analysisLoading || (
    Boolean(explorationId) && currentAnalysisStatus === null && currentAnalysisError === null
  );
  const selectedPage = pages.find((page) => page.id === selectedPageId) || pages[0] || null;
  const cancelExploration = async () => {
    if (!explorationId) return;
    setCancelling(true);
    setError(null);
    try {
      await apiRequest<Exploration>("/explorations/" + explorationId + "/cancel", { method: "POST" });
      await onRefresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not cancel this exploration.");
    } finally {
      setCancelling(false);
    }
  };

  const startAnalysis = async () => {
    if (!explorationId) return;
    setAnalysisStarting(true);
    setAnalysisError(null);
    try {
      const nextStatus = await apiRequest<AnalysisStatusResponse>(
        "/explorations/" + explorationId + "/analysis",
        { method: "POST" },
      );
      setAnalysisStatus(nextStatus);
      setAnalysisResult(null);
      setAnalysisRefresh((current) => current + 1);
    } catch (reason) {
      setAnalysisError({
        exploration_id: explorationId,
        message: reason instanceof Error ? reason.message : "Could not start AI analysis.",
      });
    } finally {
      setAnalysisStarting(false);
    }
  };

  const cancelTestRun = async (runId: string) => {
    if (!explorationId) return;
    await apiRequest("/explorations/" + explorationId + "/test-runs/" + runId + "/cancel", { method: "POST" });
  };

  if (!explorationId || (!exploration && !explorations.length)) {
    return (
      <div className="page project-page">
        <div className="page-heading">
          <div><p className="eyebrow">BROWSER EXPLORATION</p><h1>Exploration</h1><p>Start a real browser run to collect pages and interface elements.</p></div>
          <Status tone="gray">No data</Status>
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
        {loadError && <p className="form-error" role="alert">{loadError}</p>}
        <section className="exploration-empty"><h2>No explorations yet</h2><p>Create an exploration with a public HTTP or HTTPS URL to see real browser results here.</p></section>
      </div>
    );
  }

  return (
    <div className="page project-page">
      <div className="project-heading">
        <div className="active-info">
          <div className="app-icon atlas">{exploration ? hostLabel(exploration.start_url).slice(0, 1).toUpperCase() : "?"}</div>
          <div>
            <div className="title-row">
              <h1>{exploration ? hostLabel(exploration.start_url) : "Loading exploration"}</h1>
              {exploration && <Status tone={statusTone(exploration.status)}>{statusLabel(exploration.status)}</Status>}
            </div>
            {exploration && <a href={exploration.start_url} target="_blank" rel="noreferrer">{exploration.start_url} <Icon name="external" size={12} /></a>}
          </div>
        </div>
        <div className="project-actions">
          {isActive && <button className="secondary" disabled={cancelling} onClick={cancelExploration}><Icon name="close" size={14} />{cancelling ? "Cancelling" : "Cancel"}</button>}
        </div>
      </div>
      <div className="tabs">
        {["Exploration", "Interactions", "AI Analysis", "Knowledge Graph", "Workflow Intelligence", "Test Cases"].map((item) => (
          <button className={tab === item ? "active" : ""} onClick={() => onNavigateTab(item)} key={item}>{item}</button>
        ))}
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      {exploration?.error_message && <p className="form-error" role="alert">{exploration.error_message}</p>}
      {tab === "Exploration" && (
        <>
          <ExplorationSummary
            pages={pages}
            totalEvents={exploration?.event_count ?? events.length}
          />
          <div className="workspace-grid">
            <section className="preview-section">
              <div className="panel-head">
                <div><h2>Captured page</h2><p>{selectedPage ? "Saved screenshot from the isolated browser context" : "No page screenshot available yet"}</p></div>
                {selectedPage && <select aria-label="Select discovered page" value={selectedPage.id} onChange={(event) => setSelectedPageId(event.target.value)}>
                  {pages.map((page) => <option key={page.id} value={page.id}>{page.title || page.url}</option>)}
                </select>}
              </div>
              <div className="browser">
                <div className="browser-bar">
                  <div className="browser-dots"><i /><i /><i /></div>
                  <div className="browser-url"><Icon name="shield" size={12} /><span>{selectedPage?.url || exploration?.current_url || exploration?.start_url || "Waiting for page"}</span></div>
                  {selectedPage?.http_status && <span className="browser-http-status">{selectedPage.http_status}</span>}
                </div>
                <div className="browser-page screenshot-frame">
                  {selectedPage?.screenshot_url ? (
                    <img className="exploration-screenshot" src={new URL(selectedPage.screenshot_url, API_BASE).toString()} alt={"Screenshot of " + (selectedPage.title || selectedPage.url)} />
                  ) : <p>{exploration?.status === "failed" ? "The target page could not be captured." : "A screenshot appears after a page is inspected."}</p>}
                </div>
              </div>
              {selectedPage && (
                <div className="page-metadata">
                  <strong>{selectedPage.title || "Untitled page"}</strong>
                  <span>{selectedPage.description || selectedPage.visible_text.slice(0, 260) || "No visible text was extracted."}</span>
                  <small>Depth {selectedPage.depth} · HTTP {selectedPage.http_status ?? "unknown"} · {selectedPage.dom_outline.length} DOM outline entries</small>
                </div>
              )}
            </section>
            <section className="log-section">
              <div className="panel-head"><div><h2>Activity log</h2><p>Persisted browser worker events</p></div><span className="count">{exploration?.event_count ?? events.length} events</span></div>
              <div className="log-list">
                {eventsError && <p className="form-error" role="alert">{eventsError}</p>}
                {events.length ? [...events].reverse().map((event) => {
                  const kind = event.event_type.includes("failed") || event.event_type.includes("blocked") ? "assert" : event.event_type.includes("page_discovered") ? "extract" : event.event_type.includes("navigation") ? "action" : "observe";
                  return <div className="log-entry" key={event.id}>
                    <div className="log-main">
                      <i className={"log-dot " + kind} />
                      <span className="log-time">{new Date(event.created_at).toLocaleTimeString()}</span>
                      <div><strong>{event.message}</strong><code>{event.url || event.event_type}{Object.keys(event.details).length ? " · " + JSON.stringify(event.details) : ""}</code></div>
                    </div>
                  </div>;
                }) : <div className="empty-state compact"><span>{eventsError ? "Browser events could not be loaded." : eventsLoading ? "Loading saved browser events…" : isActive ? "Events will appear when the worker starts." : "No browser events were saved for this exploration."}</span></div>}
              </div>
            </section>
          </div>
          <ExplorationPagesAndElements
            pages={pages}
            selectedPageId={selectedPage?.id || null}
            loading={loading}
            error={pagesError}
            onSelectPage={setSelectedPageId}
          />
        </>
      )}
      {tab === "AI Analysis" && (
        <AIAnalysisSection
          explorationStatus={exploration?.status || "unknown"}
          pages={pages}
          status={analysisStatus}
          result={analysisResult}
          loading={analysisLoading}
          starting={analysisStarting}
          error={currentAnalysisError}
          onStart={startAnalysis}
        />
      )}
      {tab === "Interactions" && (
        <InteractionObservations
          observations={interactions}
          loading={interactionsLoading}
          error={interactionsError}
          pageTitle={(pageId) => pages.find((page) => page.id === pageId)?.title || null}
        />
      )}
      {tab === "Knowledge Graph" && (
        <KnowledgeGraphSection
          key={explorationId || "no-exploration"}
          explorationId={explorationId}
          pages={pages.map((page) => ({ id: page.id, title: page.title, url: page.url }))}
          request={apiRequest}
        />
      )}
      {tab === "Workflow Intelligence" && (
        <WorkflowIntelligenceSection
          key={explorationId || "no-exploration"}
          explorationId={explorationId}
          pages={pages.map((page) => ({ id: page.id, title: page.title, url: page.url }))}
          request={apiRequest}
          initialSearch={initialWorkflowSearch}
          onOpenEvidence={(_sourceExplorationId, pageId) => {
            setSelectedPageId(pageId);
            onNavigateTab("Exploration");
          }}
        />
      )}
      {tab === "Test Cases" && (
        <TestExecutionSection
          explorationId={explorationId}
          explorationStatus={exploration?.status || "unknown"}
          analysisStatus={currentAnalysisStatus}
          analysisStatusLoading={currentAnalysisStatusLoading}
          analysisStatusError={currentAnalysisError}
          pages={pages}
          request={apiRequest}
          initialCaseId={initialTestCaseId}
          onCancel={cancelTestRun}
        />
      )}
    </div>
  );
}
function NewExplorationModal({
  close,
  start,
}: {
  close: () => void;
  start: (url: string) => Promise<void>;
}) {
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await start(url.trim());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start the exploration.");
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <div className="modal-backdrop">
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="new-exploration-title">
        <div className="modal-head">
          <div>
            <h2 id="new-exploration-title">New exploration</h2>
            <p>Enter a public website to inspect with an isolated browser.</p>
          </div>
          <button className="icon-button" onClick={close} aria-label="Close dialog">
            <Icon name="close" size={17} />
          </button>
        </div>
        <form className="modal-form" onSubmit={submit}>
          <label htmlFor="exploration-url">Application URL</label>
          <div className="url-input">
            <Icon name="globe" size={16} />
            <input
              id="exploration-url"
              autoFocus
              type="url"
              required
              placeholder="https://app.example.com"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
            />
          </div>
          <div className="modal-option">
            <div className="summary-icon s0"><Icon name="shield" /></div>
            <div><strong>Read-only exploration</strong><p>Only explicit tabs and disclosure controls are probed; forms are never submitted.</p></div>
          </div>
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="modal-actions">
            <button className="secondary" type="button" disabled={submitting} onClick={close}>Cancel</button>
            <button className="primary" type="submit" disabled={!url.trim() || submitting}>
              <Icon name="spark" size={15} /> {submitting ? "Queueing…" : "Start exploring"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function App() {
  const currentPath = typeof window === "undefined" ? "/" : window.location.pathname;
  const authRoute = ["/login", "/register", "/forgot-password", "/reset-password"].includes(currentPath);
  const welcomeRoute = currentPath === "/welcome";
  const [authUser, setAuthUser] = useState<AuthenticatedUser | null>(null);
  const [authReady, setAuthReady] = useState(authRoute);
  const [authStartupError, setAuthStartupError] = useState<string | null>(null);
  const [editingProfile, setEditingProfile] = useState(false);
  const [accountActionError, setAccountActionError] = useState<string | null>(null);
  const [active, setActive] = useState<string>(() =>
    workspaceFromSearch(typeof window === "undefined" ? "" : window.location.search, readPreferences().landingWorkspace),
  );
  const [mobileOpen, setMobileOpen] = useState(false);
  const [modal, setModal] = useState(false);
  const [explorations, setExplorations] = useState<Exploration[]>([]);
  const [explorationId, setExplorationId] = useState<string | null>(null);
  const [pageFocusId, setPageFocusId] = useState<string | null>(null);
  const [searchFocus, setSearchFocus] = useState<SearchResult | null>(null);
  const [workspaceMenuAnchor, setWorkspaceMenuAnchor] = useState<HTMLElement | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const recordWorkspace = useCallback((workspace: string) => {
    setActive(workspace);
    if (typeof window === "undefined") return;
    const destination = workspaceLocation(window.location.href, workspace);
    const current = window.location.pathname + window.location.search + window.location.hash;
    if (destination !== current) {
      window.history.pushState({ ...(window.history.state || {}), workspace }, "", destination);
    }
  }, []);

  useEffect(() => {
    const restoreWorkspace = () => {
      setActive(workspaceFromSearch(window.location.search, readPreferences().landingWorkspace));
      setSearchFocus(null);
      setPageFocusId(null);
      setWorkspaceMenuAnchor(null);
      setMobileOpen(false);
    };
    window.addEventListener("popstate", restoreWorkspace);
    return () => window.removeEventListener("popstate", restoreWorkspace);
  }, []);

  useEffect(() => {
    if (authRoute) return;
    let current = true;
    void (async () => {
      try {
        await ensureCsrf();
        const user = await apiRequest<AuthenticatedUser>("/auth/me");
        if (!current) return;
        if (user.onboarding_completed && welcomeRoute) {
          window.location.replace("/");
          return;
        }
        if (!user.onboarding_completed && !welcomeRoute) {
          const requested = currentPath === "/" ? "" : window.location.pathname + window.location.search + window.location.hash;
          const suffix = requested ? "?next=" + encodeURIComponent(requested) : "";
          window.location.replace("/welcome" + suffix);
          return;
        }
        setAuthUser(user);
        setAuthReady(true);
      } catch (reason) {
        if (!current) return;
        if (reason instanceof ApiRequestError && reason.status === 401) {
          const requested = window.location.pathname + window.location.search + window.location.hash;
          window.location.replace("/login?next=" + encodeURIComponent(requested));
          return;
        }
        setAuthStartupError(reason instanceof Error ? reason.message : "Your session could not be checked.");
        setAuthReady(true);
      }
    })();
    return () => { current = false; };
  }, [authRoute, currentPath, welcomeRoute]);

  useEffect(() => {
    if (authRoute || welcomeRoute || !authUser) return;
    const onExpired = () => {
      const requested = window.location.pathname + window.location.search + window.location.hash;
      window.location.replace("/login?next=" + encodeURIComponent(requested));
    };
    window.addEventListener("inscope:unauthenticated", onExpired);
    return () => window.removeEventListener("inscope:unauthenticated", onExpired);
  }, [authRoute, authUser]);

  const refreshExplorations = useCallback(async () => {
    try {
      const records = await apiRequest<Exploration[]>("/explorations?limit=100");
      setExplorations(records);
      setLoadError(null);
      setExplorationId((current) => current || records[0]?.id || null);
    } catch (reason) {
      setLoadError(reason instanceof Error ? reason.message : "Could not load explorations.");
    }
  }, []);

  useEffect(() => {
    if (authRoute || welcomeRoute || !authUser) return;
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      await refreshExplorations();
      if (!stopped) timer = window.setTimeout(poll, 2500);
    };
    void poll();
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [refreshExplorations, authRoute, welcomeRoute, authUser]);

  const startExploration = async (url: string) => {
    const created = await apiRequest<Exploration>("/explorations", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    setExplorationId(created.id);
    await refreshExplorations();
    recordWorkspace("Exploration");
    setModal(false);
  };
  const openProject = (id: string) => {
    setExplorationId(id);
    recordWorkspace("Exploration");
  };
  const openProjectExploration = (id: string, tab: string, pageId?: string) => {
    setExplorationId(id);
    setPageFocusId(pageId || null);
    setSearchFocus(null);
    recordWorkspace(tab);
  };
  const navigateWorkspace = useCallback((workspace: string) => {
    recordWorkspace(workspace);
    setSearchFocus(null);
    setPageFocusId(null);
    setWorkspaceMenuAnchor(null);
    setMobileOpen(false);
  }, [recordWorkspace]);
  const navigateSearchResult = useCallback((result: SearchResult) => {
    const destination = searchDestination(result);
    setSearchFocus(result);
    if (destination.explorationId) setExplorationId(destination.explorationId);
    setPageFocusId(destination.pageId);
    recordWorkspace(destination.pageId ? "Exploration" : destination.workspace);
    setWorkspaceMenuAnchor(null);
    setMobileOpen(false);
  }, [recordWorkspace]);
  const toggleWorkspaceMenu = useCallback((anchor: HTMLElement) => {
    setWorkspaceMenuAnchor((current) => current === anchor ? null : anchor);
  }, []);
  const closeWorkspaceMenu = useCallback(() => setWorkspaceMenuAnchor(null), []);
  const saveProfile = async (fullName: string) => {
    const user = await apiRequest<AuthenticatedUser>("/auth/me", { method: "PATCH", body: JSON.stringify({ full_name: fullName }) });
    setAuthUser(user);
    setEditingProfile(false);
  };
  const signOut = async () => {
    setAccountActionError(null);
    try {
      await apiRequest<void>("/auth/logout", { method: "POST" });
      setAuthUser(null);
      setExplorations([]);
      setExplorationId(null);
      setPageFocusId(null);
      setSearchFocus(null);
      setCsrfToken(null);
      window.location.replace("/login");
    } catch (reason) {
      setAccountActionError(reason instanceof Error ? reason.message : "Sign out could not be completed.");
    }
  };
  const completeOnboarding = async (workspace = "Overview") => {
    const user = await apiRequest<AuthenticatedUser>("/auth/onboarding/complete", { method: "POST" });
    setAuthUser(user);
    setActive(workspace);
    setWorkspaceMenuAnchor(null);
    const requested = new URLSearchParams(window.location.search).get("next");
    const destination = workspace === "Overview" ? safeWelcomeDestination(requested) : "/";
    const routedDestination = workspaceLocation(new URL(destination, window.location.origin).href, workspace);
    window.history.replaceState({ workspace }, "", routedDestination);
  };
  const downloadReport = async (reportId: string, format: "pdf" | "json") => {
    let response: Response;
    try {
      response = await fetch(API_BASE + reportExportPath(reportId, format), { credentials: "include" });
    } catch {
      throw new Error("Cannot reach the backend. Check that the API is running.");
    }
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(body?.error?.message || "The export failed (" + response.status + ").");
    }
    const file = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = file;
    link.download = `software-intelligence-report-${reportId}.${format}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(file), 1000);
  };
  const printReport = (reportId: string) => {
    window.open(API_BASE + reportExportPath(reportId, "print"), "_blank", "noopener,noreferrer");
  };
  const workspaceTabs = ["Exploration", "Interactions", "AI Analysis", "Knowledge Graph", "Workflow Intelligence", "Test Cases"];
  if (authRoute) return <AuthExperience route={currentPath} />;
  if (!authReady) return <div className="auth-loading" role="status">Checking your SID session…</div>;
  if (!authUser) return <div className="auth-loading auth-loading-error" role="alert"><p>{authStartupError || "A verified SID session is required."}</p><button className="primary" onClick={() => window.location.reload()}>Try again</button><a href="/login">Go to sign in</a></div>;
  if (welcomeRoute) return (
    <>
      <WelcomeExperience
        user={authUser}
        accountMenuOpen={workspaceMenuAnchor !== null}
        onOpenAccountMenu={toggleWorkspaceMenu}
        onOpenDashboard={() => completeOnboarding("Overview")}
        error={accountActionError}
      />
      <AccountMenu
        anchor={workspaceMenuAnchor}
        close={closeWorkspaceMenu}
        navigate={(workspace) => { void completeOnboarding(workspace).catch((reason) => setAccountActionError(reason instanceof Error ? reason.message : "Could not open that workspace.")); }}
        user={authUser}
        onEditProfile={() => setEditingProfile(true)}
        onLogout={signOut}
      />
      {editingProfile && <EditProfileDialog user={authUser} onClose={() => setEditingProfile(false)} onSave={saveProfile} />}
      {accountActionError && <div className="account-action-error" role="alert">{accountActionError}<button type="button" onClick={() => setAccountActionError(null)} aria-label="Dismiss">×</button></div>}
    </>
  );
  const body =
    active === "Overview" ? (
      <Overview
        explorations={explorations}
        onNew={() => setModal(true)}
        openProject={(id) => {
          setExplorationId(id);
          recordWorkspace("Exploration");
        }}
        loadError={loadError}
      />
    ) : active === "Projects" ? (
      <ProjectsSection
        request={apiRequest}
        openExploration={openProjectExploration}
        initialProjectId={searchFocus?.entity_type === "project" ? searchFocus.id : null}
      />
    ) : active === "Reports" ? (
      <ReportsSection
        request={apiRequest}
        download={downloadReport}
        print={printReport}
        openSource={openProjectExploration}
        initialReportId={searchFocus?.entity_type === "report" ? searchFocus.id : null}
      />
    ) : active === "Settings" ? (
      <SettingsSection request={apiRequest} />
    ) : workspaceTabs.includes(active) ? (
      <ProjectWorkspace
        initialTab={active}
        initialPageId={pageFocusId}
        onInitialPageHandled={() => setPageFocusId(null)}
        initialTestCaseId={searchFocus?.entity_type === "test_case" ? searchFocus.id : null}
        initialWorkflowSearch={searchFocus?.entity_type === "workflow" ? searchFocus.title : null}
        onNavigateTab={recordWorkspace}
        explorationId={explorationId}
        explorations={explorations}
        onRefresh={refreshExplorations}
        loadError={loadError}
      />
    ) : null;

  return (
    <div className="app-shell">
      <Sidebar
        active={sidebarWorkspace(active)}
        onSelect={navigateWorkspace}
        open={mobileOpen}
        close={() => setMobileOpen(false)}
        activeExplorations={explorations.filter((item) => item.status === "queued" || item.status === "running").length}
        onOpenWorkspaceMenu={toggleWorkspaceMenu}
        workspaceMenuOpen={workspaceMenuAnchor !== null}
        user={authUser}
      />
      <main>
        <Header
          active={active}
          onMenu={() => setMobileOpen(true)}
          onNew={() => setModal(true)}
          request={apiRequest}
          onNavigate={navigateSearchResult}
          onOverview={() => navigateWorkspace("Overview")}
          onOpenWorkspaceMenu={toggleWorkspaceMenu}
          workspaceMenuOpen={workspaceMenuAnchor !== null}
          user={authUser}
        />
        {body}
      </main>
      <AccountMenu anchor={workspaceMenuAnchor} close={closeWorkspaceMenu} navigate={navigateWorkspace} user={authUser} onEditProfile={() => setEditingProfile(true)} onLogout={signOut} />
      {editingProfile && <EditProfileDialog user={authUser} onClose={() => setEditingProfile(false)} onSave={saveProfile} />}
      {accountActionError && <div className="account-action-error" role="alert">{accountActionError}<button type="button" onClick={() => setAccountActionError(null)} aria-label="Dismiss">×</button></div>}
      {modal && <NewExplorationModal close={() => setModal(false)} start={startExploration} />}
    </div>
  );
}

function safeWelcomeDestination(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "/";
  try {
    const parsed = new URL(value, window.location.origin);
    if (parsed.origin !== window.location.origin || ["/login", "/register", "/forgot-password", "/reset-password", "/welcome"].includes(parsed.pathname)) return "/";
    return parsed.pathname + parsed.search + parsed.hash;
  } catch {
    return "/";
  }
}

function EditProfileDialog({ user, onClose, onSave }: {
  user: AuthenticatedUser;
  onClose: () => void;
  onSave: (fullName: string) => Promise<void>;
}) {
  const [fullName, setFullName] = useState(user.full_name);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onClose(); }}>
    <section className="modal account-profile-modal" role="dialog" aria-modal="true" aria-labelledby="edit-profile-title">
      <div className="modal-head"><div><p className="eyebrow">ACCOUNT</p><h2 id="edit-profile-title">Edit profile</h2><p>Update how your name appears in SID.</p></div><button className="icon-button" type="button" aria-label="Close" onClick={onClose}>×</button></div>
      <form onSubmit={(event) => { event.preventDefault(); setSaving(true); setError(null); void onSave(fullName).catch((reason) => setError(reason instanceof Error ? reason.message : "Profile update failed.")).finally(() => setSaving(false)); }}>
        <label className="form-field">Full name<input autoFocus value={fullName} onChange={(event) => setFullName(event.target.value)} required maxLength={160} /></label>
        <div className="account-email-readonly"><span>Email address</span><strong>{user.email}</strong><small>Verified</small></div>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="modal-actions"><button className="secondary" type="button" disabled={saving} onClick={onClose}>Cancel</button><button className="primary" type="submit" disabled={saving || !fullName.trim()}>{saving ? "Saving…" : "Save profile"}</button></div>
      </form>
    </section>
  </div>;
}
