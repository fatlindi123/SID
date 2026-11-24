import type { EligibleTestObservation } from "./testExecutionApi";

export type ObservationPageOption = { id: string; title: string; url: string };

function stateLabel(state: { selected: boolean | null; expanded: boolean | null }) {
  if (typeof state.selected === "boolean") return `selected ${state.selected}`;
  if (typeof state.expanded === "boolean") return `expanded ${state.expanded}`;
  return "State unavailable";
}

export function ObservationStepPicker({
  pages,
  observations,
  loading,
  error,
  pageFilter,
  selectedId,
  onPageFilterChange,
  onSelectionChange,
  onAdd,
  alreadyAdded,
  disabled,
}: {
  pages: ObservationPageOption[];
  observations: EligibleTestObservation[];
  loading: boolean;
  error: string | null;
  pageFilter: string;
  selectedId: string;
  onPageFilterChange: (pageId: string) => void;
  onSelectionChange: (observationId: string) => void;
  onAdd: () => void;
  alreadyAdded: boolean;
  disabled: boolean;
}) {
  const filtered = observations.filter((item) => pageFilter === "all" || item.page_id === pageFilter);
  const selected = observations.find((item) => item.id === selectedId);
  const groups = pages.map((page) => ({
    page,
    items: filtered.filter((item) => item.page_id === page.id),
  })).filter((group) => group.items.length > 0);

  return (
    <fieldset className="observation-picker">
      <legend>Add a saved interaction observation</legend>
      {loading && observations.length === 0 && <p className="workspace-message" role="status">Loading eligible observations…</p>}
      {error && <p className="form-error" role="alert">Eligible observations could not be loaded: {error}</p>}
      {!loading && !error && observations.length === 0 && <p className="analysis-empty-note" role="status">No eligible observed tabs or disclosure controls are saved for this exploration.</p>}
      {observations.length > 0 && (
        <>
          <label>Filter by page
            <select value={pageFilter} onChange={(event) => onPageFilterChange(event.target.value)}>
              <option value="all">All eligible pages</option>
              {pages.filter((page) => observations.some((item) => item.page_id === page.id)).map((page) => (
                <option key={page.id} value={page.id}>{page.title || page.url}</option>
              ))}
            </select>
          </label>
          <label>Observed control
            <select value={selectedId} onChange={(event) => onSelectionChange(event.target.value)}>
              <option value="">Choose an observed transition</option>
              {groups.map(({ page, items }) => (
                <optgroup key={page.id} label={page.title || page.url}>
                  {items.map((item) => <option key={item.id} value={item.id}>
                    {item.interaction_type}: {item.control.name} ({stateLabel(item.before_state)} → {stateLabel(item.observed_after_state)})
                  </option>)}
                </optgroup>
              ))}
            </select>
          </label>
          {selected && <p className="analysis-empty-note">
            {selected.control.role} “{selected.control.name}” on {selected.page_title}: {stateLabel(selected.before_state)} → {stateLabel(selected.observed_after_state)}. Choose and confirm an expected state on the step before saving.
          </p>}
          <button className="secondary" type="button" onClick={onAdd} disabled={disabled || !selected || alreadyAdded}>
            Add observation step
          </button>
        </>
      )}
    </fieldset>
  );
}
