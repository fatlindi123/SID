export type InteractionObservation = {
  id: string;
  exploration_id: string;
  page_id: string;
  element_ordinal: number;
  action_type: "tab" | "disclosure" | "unknown";
  outcome: "observed" | "skipped" | "blocked" | "failed";
  reason: string | null;
  before_evidence: Record<string, any>;
  after_evidence: Record<string, any> | null;
  duration_ms: number;
  created_at: string;
};

const outcomeLabel: Record<InteractionObservation["outcome"], string> = {
  observed: "Observed",
  skipped: "Skipped",
  blocked: "Blocked",
  failed: "Failed",
};

function evidenceState(evidence: Record<string, any> | null) {
  if (!evidence) return "No after-state was captured.";
  const target = evidence.target || {};
  const state = [
    typeof target.expanded === "boolean" ? `expanded ${target.expanded}` : null,
    typeof target.selected === "boolean" ? `selected ${target.selected}` : null,
  ].filter(Boolean);
  const changes = evidence.visible_changes || {};
  const counts = [
    (changes.added || []).length ? `${changes.added.length} visible added` : null,
    (changes.removed || []).length ? `${changes.removed.length} visible removed` : null,
    (changes.state_changes || []).length ? `${changes.state_changes.length} state changes` : null,
  ].filter(Boolean);
  return [...state, ...counts].join(" · ") || "No visible state change was recorded.";
}

export function InteractionObservations({
  observations,
  loading,
  error,
  pageTitle,
}: {
  observations: InteractionObservation[];
  loading: boolean;
  error: string | null;
  pageTitle?: (pageId: string) => string | null;
}) {
  return (
    <section className="interaction-section">
      <div className="panel-head">
        <div>
          <h2>Interaction observations</h2>
          <p>Saved evidence from isolated, allowlisted tab and disclosure probes.</p>
        </div>
        <span className="count">{observations.length} saved</span>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      {loading && !observations.length ? (
        <div className="empty-state compact"><span>Loading saved interaction observations…</span></div>
      ) : observations.length ? (
        <div className="interaction-list">
          {observations.map((item) => {
            const target = item.before_evidence.target || {};
            const label = target.name || `${target.role || "Control"} · ${target.tag_name || "element"}`;
            const title = pageTitle?.(item.page_id) || item.before_evidence.title || "Saved page";
            const url = item.before_evidence.url || "URL unavailable";
            return (
              <article className="interaction-card" key={item.id}>
                <div className="interaction-card-head">
                  <div>
                    <strong>{label}</strong>
                    <span>{title} · {item.action_type} · element #{item.element_ordinal + 1}</span>
                  </div>
                  <span className={`interaction-outcome ${item.outcome}`}>{outcomeLabel[item.outcome]}</span>
                </div>
                <code className="interaction-url">{url}</code>
                <p>{evidenceState(item.after_evidence)}</p>
                {item.reason && <small className="interaction-reason">{item.reason.replace(/_/g, " ")}</small>}
                {item.after_evidence && (
                  <details>
                    <summary>View bounded before and after evidence</summary>
                    <div className="interaction-evidence-grid">
                      <div><strong>Before</strong><pre>{JSON.stringify(item.before_evidence, null, 2)}</pre></div>
                      <div><strong>After</strong><pre>{JSON.stringify(item.after_evidence, null, 2)}</pre></div>
                    </div>
                  </details>
                )}
                <small className="interaction-duration">{item.duration_ms} ms · {new Date(item.created_at).toLocaleString()}</small>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="empty-state compact">
          <strong>{error ? "Observations could not be loaded." : "No interaction observations saved"}</strong>
          <span>{error ? "Retry after the backend connection is restored." : "Only explicitly marked non-form tabs and disclosure controls are considered."}</span>
        </div>
      )}
    </section>
  );
}
