export type ExplorationResultElement = {
  id: string;
  kind: string;
  tag_name: string;
  role: string | null;
  accessible_name: string | null;
  label: string | null;
  text: string | null;
  selector: string | null;
};

export type ExplorationResultPage = {
  id: string;
  url: string;
  title: string;
  http_status: number | null;
  element_count: number;
  elements: ExplorationResultElement[];
};

export function ExplorationSummary({
  pages,
  totalEvents,
}: {
  pages: ExplorationResultPage[];
  totalEvents: number;
}) {
  const totalElements = pages.reduce((total, page) => total + page.elements.length, 0);
  const metrics = [
    ["P", pages.length, "Pages explored", "Saved pages in this exploration"],
    ["E", totalElements, "Elements discovered", "Saved interface elements across pages"],
    ["B", totalEvents, "Browser events", "Total persisted exploration events"],
  ] as const;

  return (
    <section className="summary-grid exploration-summary" aria-label="Exploration totals">
      {metrics.map(([mark, value, label, description], index) => (
        <div className="summary" key={label}>
          <span className={"summary-icon s" + index} aria-hidden="true">{mark}</span>
          <div>
            <strong>{value}</strong>
            <span>{label}</span>
            <p>{description}</p>
          </div>
        </div>
      ))}
    </section>
  );
}

export function ExplorationPagesAndElements({
  pages,
  selectedPageId,
  loading,
  error,
  onSelectPage,
}: {
  pages: ExplorationResultPage[];
  selectedPageId: string | null;
  loading: boolean;
  error: string | null;
  onSelectPage: (pageId: string) => void;
}) {
  const selectedPage = pages.find((page) => page.id === selectedPageId) || pages[0] || null;

  return (
    <div className="exploration-results-panels">
      <section className="page-list-panel" aria-labelledby="discovered-pages-title">
        <div className="panel-head">
          <div>
            <h2 id="discovered-pages-title">Discovered pages</h2>
            <p>Every page saved during this exploration</p>
          </div>
          <span className="count">{pages.length} pages</span>
        </div>
        {error && <p className="form-error results-error" role="alert">{error}</p>}
        {loading && !pages.length && (
          <p className="workspace-message results-message" role="status">
            Loading saved pages and interface elements…
          </p>
        )}
        {!loading && !error && !pages.length && (
          <div className="empty-state compact" role="status">
            <strong>No pages saved yet</strong>
            <span>The browser worker has not saved a page for this exploration.</span>
          </div>
        )}
        {!!pages.length && (
          <div className="page-list">
            {pages.map((page) => (
              <button
                className={"page-list-item" + (page.id === selectedPage?.id ? " selected" : "")}
                type="button"
                key={page.id}
                data-page-id={page.id}
                aria-pressed={page.id === selectedPage?.id}
                onClick={() => onSelectPage(page.id)}
              >
                <span className="page-list-main">
                  <strong>{page.title.trim() || "Untitled page"}</strong>
                  <span>{page.url}</span>
                </span>
                <span className="page-list-meta">
                  <small>HTTP {page.http_status ?? "unknown"}</small>
                  <small>{page.elements.length} elements</small>
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="discovered-panel" aria-labelledby="discovered-elements-title">
        <div className="panel-head">
          <div>
            <h2 id="discovered-elements-title">Discovered interface elements</h2>
            <p>{selectedPage?.url || "Select a discovered page"}</p>
          </div>
          <span className="count">{selectedPage?.elements.length ?? 0} elements</span>
        </div>
        {selectedPage?.elements.length ? (
          <div className="element-grid" aria-label={"Interface elements on " + selectedPage.url}>
            {selectedPage.elements.map((element) => {
              const name = element.accessible_name || element.label || element.text || "Unnamed element";
              return (
                <article className="observed-element" key={element.id} data-element-id={element.id}>
                  <div aria-hidden="true">{element.kind.slice(0, 1).toUpperCase()}</div>
                  <span>
                    <strong>{name}</strong>
                    <small>
                      Role: {element.role || element.kind || "unknown"} · Name: {name} · Tag: {element.tag_name}
                    </small>
                    <code><span>Locator:</span> {element.selector || "Not available"}</code>
                  </span>
                </article>
              );
            })}
          </div>
        ) : (
          <div className="empty-state compact">
            <strong>{selectedPage ? "No interface elements saved on this page" : "No page selected"}</strong>
            <span>{selectedPage ? "The saved page contains no discovered interactive elements." : "Saved elements appear here after a page is discovered."}</span>
          </div>
        )}
      </section>
    </div>
  );
}
