import { useEffect, useState } from "react";

import {
  DEFAULT_PREFERENCES,
  LANDING_WORKSPACES,
  PAGE_SIZE_OPTIONS,
  resetPreferences,
  savePreferences,
  useUiPreferences,
  type LandingWorkspace,
  type PageSize,
} from "../preferences";

type Request = <T>(path: string, init?: RequestInit) => Promise<T>;
type SystemStatus = {
  version: string;
  environment: "development" | "production";
  database: "healthy" | "unhealthy";
  ai_provider: "configured" | "not_configured";
  browser_automation: "available" | "unavailable";
};

export function SettingsSection({ request }: { request: Request }) {
  const preferences = useUiPreferences();
  const [landingWorkspace, setLandingWorkspace] = useState<LandingWorkspace>(preferences.landingWorkspace);
  const [pageSize, setPageSize] = useState<PageSize>(preferences.pageSize);
  const [feedback, setFeedback] = useState<{ kind: "success" | "error"; message: string } | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusRefresh, setStatusRefresh] = useState(0);

  useEffect(() => {
    setLandingWorkspace(preferences.landingWorkspace);
    setPageSize(preferences.pageSize);
  }, [preferences.landingWorkspace, preferences.pageSize]);

  useEffect(() => {
    let current = true;
    setStatusLoading(true);
    setStatusError(null);
    request<SystemStatus>("/system/status")
      .then((value) => {
        if (current) setStatus(value);
      })
      .catch((reason) => {
        if (current) {
          setStatus(null);
          setStatusError(reason instanceof Error ? reason.message : "System status is unavailable.");
        }
      })
      .finally(() => {
        if (current) setStatusLoading(false);
      });
    return () => { current = false; };
  }, [request, statusRefresh]);

  const save = () => {
    setFeedback(null);
    try {
      savePreferences({ landingWorkspace, pageSize });
      setFeedback({ kind: "success", message: "Preferences saved in this browser." });
    } catch (reason) {
      setFeedback({
        kind: "error",
        message: reason instanceof Error ? reason.message : "Preferences could not be saved.",
      });
    }
  };

  const reset = () => {
    setFeedback(null);
    try {
      resetPreferences();
      setLandingWorkspace(DEFAULT_PREFERENCES.landingWorkspace);
      setPageSize(DEFAULT_PREFERENCES.pageSize);
      setFeedback({ kind: "success", message: "Preferences reset to their defaults." });
    } catch (reason) {
      setFeedback({
        kind: "error",
        message: reason instanceof Error ? reason.message : "Preferences could not be reset.",
      });
    }
  };

  return (
    <div className="page settings-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">APPLICATION</p>
          <h1>Settings</h1>
          <p>Manage this browser’s workspace preferences and review application status.</p>
        </div>
      </div>

      <section className="settings-card" aria-labelledby="settings-preferences-heading">
        <div className="settings-section-heading">
          <div><h2 id="settings-preferences-heading">Workspace preferences</h2><p>These preferences are saved locally in this browser profile.</p></div>
        </div>
        <div className="settings-fields">
          <label>
            Default landing workspace
            <select value={landingWorkspace} onChange={(event) => setLandingWorkspace(event.target.value as LandingWorkspace)}>
              {LANDING_WORKSPACES.map((workspace) => <option key={workspace} value={workspace}>{workspace}</option>)}
            </select>
            <small>Used the next time the application opens.</small>
          </label>
          <label>
            Rows per page
            <select value={pageSize} onChange={(event) => setPageSize(Number(event.target.value) as PageSize)}>
              {PAGE_SIZE_OPTIONS.map((size) => <option key={size} value={size}>{size} rows</option>)}
            </select>
            <small>Applies to project, workflow, test case, run history, and report lists.</small>
          </label>
        </div>
        {feedback && <p className={`settings-feedback ${feedback.kind}`} role={feedback.kind === "error" ? "alert" : "status"}>{feedback.message}</p>}
        <div className="settings-actions">
          <button className="secondary" type="button" onClick={reset}>Reset defaults</button>
          <button className="primary" type="button" onClick={save}>Save preferences</button>
        </div>
      </section>

      <section className="settings-card" aria-labelledby="settings-system-heading">
        <div className="settings-section-heading">
          <div><h2 id="settings-system-heading">Application and services</h2><p>Read-only status from the running backend. No credentials are returned.</p></div>
          <button className="secondary" type="button" onClick={() => setStatusRefresh((value) => value + 1)} disabled={statusLoading}>
            {statusLoading ? "Checking…" : "Refresh status"}
          </button>
        </div>
        {statusLoading && !status && <p className="workspace-message" role="status">Checking application status…</p>}
        {statusError && <p className="form-error" role="alert">{statusError}</p>}
        {status && (
          <>
            <dl className="settings-runtime">
              <div><dt>Application version</dt><dd>{status.version}</dd></div>
              <div><dt>Environment</dt><dd>{status.environment}</dd></div>
              <div><dt>Backend API</dt><dd><ServiceStatus status="available" label="Available" /></dd></div>
              <div><dt>PostgreSQL</dt><dd><ServiceStatus status={status.database === "healthy" ? "available" : "unavailable"} label={status.database === "healthy" ? "Healthy" : "Unhealthy"} /></dd></div>
              <div><dt>AI provider</dt><dd><ServiceStatus status={status.ai_provider === "configured" ? "available" : "not_configured"} label={status.ai_provider === "configured" ? "Configured" : "Not configured"} /></dd></div>
              <div><dt>Browser automation</dt><dd><ServiceStatus status={status.browser_automation} label={status.browser_automation === "available" ? "Playwright and Chromium available" : "Playwright or Chromium unavailable"} /></dd></div>
            </dl>
            <p className="settings-note">AI status reports whether a provider URL and model are configured; it does not test provider connectivity. Browser status checks for the local Chromium executable without launching a browser.</p>
          </>
        )}
        {!statusLoading && !status && !statusError && <p className="settings-note">System status is unavailable.</p>}
      </section>

    </div>
  );
}

function ServiceStatus({ status, label }: { status: "available" | "unavailable" | "not_configured"; label: string }) {
  return <span className={`settings-status ${status}`}><i />{label}</span>;
}
