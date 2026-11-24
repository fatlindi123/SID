import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

export type SearchWorkspace = "Projects" | "Exploration" | "Test Cases" | "Workflow Intelligence" | "Reports";
export type SearchResult = {
  entity_type: "project" | "exploration" | "page" | "test_case" | "workflow" | "report";
  id: string;
  title: string;
  context: string;
  destination: {
    workspace: SearchWorkspace;
    project_id: string | null;
    exploration_id: string | null;
    page_id: string | null;
    report_id: string | null;
  };
};
type SearchResponse = { results: SearchResult[] };
type Request = <T>(path: string, init?: RequestInit) => Promise<T>;

const GROUPS: { type: SearchResult["entity_type"]; label: string }[] = [
  { type: "project", label: "Projects" },
  { type: "exploration", label: "Explorations" },
  { type: "page", label: "Saved pages" },
  { type: "test_case", label: "Test cases" },
  { type: "workflow", label: "Workflows" },
  { type: "report", label: "Reports" },
];
const SEARCH_SHORTCUT = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform)
  ? "⌘ K"
  : "Ctrl K";

export function searchDestination(result: SearchResult) {
  return {
    workspace: result.destination.workspace,
    projectId: result.destination.project_id,
    explorationId: result.destination.exploration_id,
    pageId: result.destination.page_id,
    reportId: result.destination.report_id,
    testCaseId: result.entity_type === "test_case" ? result.id : null,
    workflowName: result.entity_type === "workflow" ? result.title : null,
  };
}

export function nextSearchIndex(current: number, resultCount: number, direction: "up" | "down") {
  if (resultCount <= 0) return -1;
  if (direction === "down") return (current + 1 + resultCount) % resultCount;
  return (current - 1 + resultCount) % resultCount;
}

export function GlobalSearch({
  request,
  onSelect,
}: {
  request: Request;
  onSelect: (result: SearchResult) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [resource, setResource] = useState<{
    query: string;
    results: SearchResult[];
    loading: boolean;
    error: string | null;
  }>({ query: "", results: [], loading: false, error: null });

  const normalizedQuery = query.trim();
  const current = resource.query === normalizedQuery ? resource : null;
  const results = current?.results || [];
  const grouped = useMemo(
    () => GROUPS.map((group) => ({ ...group, items: results.filter((item) => item.entity_type === group.type) })).filter((group) => group.items.length),
    [results],
  );

  useEffect(() => {
    let currentRequest = true;
    if (normalizedQuery.length < 2) {
      setResource({ query: normalizedQuery, results: [], loading: false, error: null });
      return () => { currentRequest = false; };
    }
    setResource({ query: normalizedQuery, results: [], loading: true, error: null });
    const timer = window.setTimeout(() => {
      void request<SearchResponse>(`/search?q=${encodeURIComponent(normalizedQuery)}`)
        .then((response) => {
          if (currentRequest) setResource({ query: normalizedQuery, results: response.results, loading: false, error: null });
        })
        .catch((reason: unknown) => {
          if (currentRequest) setResource({
            query: normalizedQuery,
            results: [],
            loading: false,
            error: reason instanceof Error ? reason.message : "Search is unavailable.",
          });
        });
    }, 250);
    return () => {
      currentRequest = false;
      window.clearTimeout(timer);
    };
  }, [normalizedQuery, request]);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onShortcut = (event: globalThis.KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onShortcut);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onShortcut);
    };
  }, []);

  const choose = (result: SearchResult) => {
    setOpen(false);
    setQuery("");
    setActiveIndex(-1);
    onSelect(result);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      return;
    }
    if (event.key === "ArrowDown" && results.length) {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((index) => nextSearchIndex(index, results.length, "down"));
    } else if (event.key === "ArrowUp" && results.length) {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((index) => nextSearchIndex(index, results.length, "up"));
    } else if (event.key === "Enter" && open && results[activeIndex]) {
      event.preventDefault();
      choose(results[activeIndex]);
    }
  };

  let position = 0;
  const optionIds = new Map<string, string>();
  for (const group of grouped) {
    for (const item of group.items) optionIds.set(`${item.entity_type}:${item.id}`, `global-search-option-${position++}`);
  }
  const activeResult = results[activeIndex];

  return (
    <div className="global-search" ref={rootRef}>
      <svg aria-hidden="true" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" />
      </svg>
      <input
        ref={inputRef}
        type="search"
        role="combobox"
        aria-label="Search application"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls="global-search-results"
        aria-activedescendant={open && activeResult ? optionIds.get(`${activeResult.entity_type}:${activeResult.id}`) : undefined}
        placeholder="Search"
        value={query}
        maxLength={100}
        onFocus={() => setOpen(true)}
      onChange={(event) => { setQuery(event.target.value); setActiveIndex(-1); setOpen(true); }}
        onKeyDown={onKeyDown}
      />
      <kbd aria-hidden="true">{SEARCH_SHORTCUT}</kbd>
      {open && <div className="global-search-popover" id="global-search-results" role="listbox" aria-label="Search results">
        {normalizedQuery.length < 2 && <p className="global-search-hint">Type at least 2 characters to search saved application data.</p>}
        {normalizedQuery.length >= 2 && current?.loading && <p className="global-search-hint" role="status">Searching saved data…</p>}
        {normalizedQuery.length >= 2 && current?.error && <p className="global-search-error" role="alert">Search failed: {current.error}</p>}
        {normalizedQuery.length >= 2 && !current?.loading && !current?.error && current && results.length === 0 && <p className="global-search-hint" role="status">No saved results found.</p>}
        {grouped.map((group) => <section className="global-search-group" key={group.type} role="group" aria-label={group.label}>
          <h2>{group.label}</h2>
          {group.items.map((item) => {
            const index = results.indexOf(item);
            return <button
              id={optionIds.get(`${item.entity_type}:${item.id}`)}
              className="global-search-result"
              key={`${item.entity_type}:${item.id}`}
              role="option"
              aria-selected={index === activeIndex}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => choose(item)}
            >
              <span><strong>{item.title}</strong><small>{item.context}</small></span>
              <span className="global-search-result-type">{group.label}</span>
            </button>;
          })}
        </section>)}
      </div>}
    </div>
  );
}
