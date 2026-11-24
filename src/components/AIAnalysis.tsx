export type AnalysisStatus = "not_started" | "queued" | "running" | "completed" | "failed";

export type AnalysisStatusResponse = {
  id: string | null;
  exploration_id: string;
  status: AnalysisStatus;
  attempt_count: number;
  model: string | null;
  error_message: string | null;
  requested_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string | null;
};

export type AnalysisEvidence = { page_id: string; element_ids: string[] };

export type AIAnalysisResult = {
  website_purpose: { interpretation: string; evidence: AnalysisEvidence[] };
  page_summaries: { page_id: string; interpretation: string; evidence: AnalysisEvidence[] }[];
  main_navigation: {
    evidence: AnalysisEvidence;
    target_page_id: string | null;
    interpretation: string;
  }[];
  important_interactive_elements: { evidence: AnalysisEvidence; interpretation: string }[];
  forms_and_fields: {
    page_id: string;
    form_element_ids: string[];
    field_element_ids: string[];
    relationship_note: "page_level_only";
  }[];
  potential_workflows: {
    title: string;
    objective: string;
    relevant_page_ids: string[];
    steps: { instruction: string; evidence: AnalysisEvidence[] }[];
    expected_outcome: string;
  }[];
  warnings: {
    collection: "main_navigation" | "important_interactive_elements" | "potential_workflows";
    discarded_count: number;
  }[];
};

type AnalysisPageElement = {
  id: string;
  kind: string;
  tag_name: string;
  role: string | null;
  accessible_name: string | null;
  label: string | null;
  text: string | null;
  selector: string | null;
  attributes: Record<string, string | boolean>;
};

type AnalysisPage = {
  id: string;
  url: string;
  title: string;
  elements: AnalysisPageElement[];
};

type AIAnalysisSectionProps = {
  explorationStatus: string;
  pages: AnalysisPage[];
  status: AnalysisStatusResponse | null;
  result: AIAnalysisResult | null;
  loading: boolean;
  starting: boolean;
  error: string | null;
  onStart: () => void;
};

function pageLabel(page: AnalysisPage | undefined, pageId: string) {
  return page ? `${page.title || "Untitled page"} · ${page.url}` : `Saved page ${pageId}`;
}

function evidenceElementName(
  element: Pick<AnalysisPageElement, "accessible_name" | "label" | "text"> | undefined,
) {
  return element?.accessible_name || element?.label || element?.text || "Unnamed element";
}

const optionalCollectionNames = {
  main_navigation: "navigation insight",
  important_interactive_elements: "interactive element insight",
  potential_workflows: "workflow suggestion",
} as const;

function EvidenceList({ evidence, pages }: { evidence: AnalysisEvidence[]; pages: AnalysisPage[] }) {
  return (
    <div className="analysis-evidence">
      <strong>Observed evidence</strong>
      {evidence.map((reference, index) => {
        const page = pages.find((item) => item.id === reference.page_id);
        const elements = reference.element_ids
          .map((id) => page?.elements.find((element) => element.id === id))
          .filter((element): element is AnalysisPageElement => Boolean(element));
        return (
          <div className="analysis-evidence-item" key={`${reference.page_id}-${index}`}>
            <span>{pageLabel(page, reference.page_id)}</span>
            {elements.length ? (
              <ul>
                {elements.map((element) => (
                  <li key={element.id}>
                    <strong>{evidenceElementName(element)}</strong>
                    <small>Role: {element.role || element.kind} · Tag: {element.tag_name}</small>
                    {element.selector && <code>{element.selector}</code>}
                  </li>
                ))}
              </ul>
            ) : !reference.element_ids.length ? (
              <small>Page metadata and saved DOM outline</small>
            ) : (
              <small>Referenced elements are not present in the loaded page data.</small>
            )}
          </div>
        );
      })}
    </div>
  );
}

function InterpretationCard({
  title,
  interpretation,
  evidence,
  pages,
}: {
  title: string;
  interpretation: string;
  evidence: AnalysisEvidence[];
  pages: AnalysisPage[];
}) {
  return (
    <article className="analysis-card">
      <h3>{title}</h3>
      <span className="analysis-kind">AI-generated interpretation</span>
      <p>{interpretation}</p>
      <EvidenceList evidence={evidence} pages={pages} />
    </article>
  );
}

export function AIAnalysisSection({
  explorationStatus,
  pages,
  status,
  result,
  loading,
  starting,
  error,
  onStart,
}: AIAnalysisSectionProps) {
  const hasAttempt = (status?.attempt_count || 0) > 0;
  const isActive = status?.status === "queued" || status?.status === "running" || starting;
  const canStart = explorationStatus === "completed" && pages.length > 0 && !isActive && !loading;
  const buttonText = hasAttempt ? "Rerun analysis" : "Analyze saved exploration";

  return (
    <section className="ai-analysis-panel" aria-labelledby="ai-analysis-title">
      <div className="panel-head ai-analysis-heading">
        <div>
          <h2 id="ai-analysis-title">AI Analysis</h2>
          <p>Evidence-based interpretation of saved pages and interface elements</p>
        </div>
        <div className="ai-analysis-actions">
          {status && status.status !== "not_started" && (
            <span className={`ai-analysis-status ${status.status}`} role="status">
              {status.status === "queued" ? "Queued" : status.status[0].toUpperCase() + status.status.slice(1)}
            </span>
          )}
          <button className="secondary" type="button" onClick={onStart} disabled={!canStart}>
            {starting ? "Queueing…" : buttonText}
          </button>
        </div>
      </div>

      {loading && <p className="workspace-message" role="status">Loading saved AI analysis…</p>}
      {!loading && error && <p className="form-error" role="alert">{error}</p>}
      {!loading && !starting && !error && status?.status === "not_started" && (
        <div className="empty-state compact" role="status">
          <strong>No AI analysis saved yet</strong>
          <span>{explorationStatus === "completed" && pages.length ? "Analyze the persisted exploration to generate interpretations and workflow suggestions." : "Complete an exploration with saved pages before starting analysis."}</span>
        </div>
      )}
      {!loading && !error && isActive && (
        <div className="workspace-message" role="status">
          {starting ? "Queueing AI analysis…" : status?.status === "queued" ? "AI analysis is queued." : "AI analysis is running against the saved exploration data."}
        </div>
      )}
      {!loading && !error && status?.status === "failed" && (
        <div className="analysis-failure" role="alert">
          <strong>Analysis failed</strong>
          <span>{status.error_message || "The analysis worker did not return a result."}</span>
          <small>Retrying creates a new attempt in the same saved analysis record.</small>
        </div>
      )}
      {!loading && !error && status?.status === "completed" && !result && (
        <p className="form-error" role="alert">The analysis completed, but its saved result could not be loaded.</p>
      )}
      {!loading && !error && status?.status === "completed" && result && (
        <div className="ai-analysis-content">
          <p className="analysis-suggestion-note">Workflow scenarios are AI suggestions. Saved execution results are available in the Test Cases tab.</p>
          {result.warnings.length > 0 && (
            <p className="analysis-suggestion-note" role="status">
              Some generated items were omitted after validation: {result.warnings.map((warning) =>
                `${warning.discarded_count} ${optionalCollectionNames[warning.collection]}${warning.discarded_count === 1 ? "" : "s"}`,
              ).join(", ")}. Retained items passed required-field and saved-evidence checks.
            </p>
          )}
          <section className="analysis-group">
            <h3>Website purpose</h3>
            <InterpretationCard
              title="Purpose"
              interpretation={result.website_purpose.interpretation}
              evidence={result.website_purpose.evidence}
              pages={pages}
            />
          </section>
          <section className="analysis-group">
            <h3>Page summaries</h3>
            <div className="analysis-card-grid">
              {result.page_summaries.map((summary) => (
                <InterpretationCard
                  key={summary.page_id}
                  title={pages.find((page) => page.id === summary.page_id)?.title || "Saved page"}
                  interpretation={summary.interpretation}
                  evidence={summary.evidence}
                  pages={pages}
                />
              ))}
            </div>
          </section>
          <section className="analysis-group">
            <h3>Main navigation</h3>
            {result.main_navigation.length ? (
              <div className="analysis-card-grid">
                {result.main_navigation.map((item, index) => {
                  const page = pages.find((candidate) => candidate.id === item.evidence.page_id);
                  const element = page?.elements.find((candidate) => candidate.id === item.evidence.element_ids[0]);
                  const target = item.target_page_id ? pages.find((candidate) => candidate.id === item.target_page_id) : undefined;
                  return (
                    <article className="analysis-card" key={`${item.evidence.page_id}-${index}`}>
                      <h4>{evidenceElementName(element)}</h4>
                      <span className="analysis-kind">Observed link · {target ? `Target: ${target.title || target.url}` : "Target page not saved"}</span>
                      <p>{item.interpretation}</p>
                      <EvidenceList evidence={[item.evidence]} pages={pages} />
                    </article>
                  );
                })}
              </div>
            ) : <p className="analysis-empty-note">No main navigation links were identified in the saved elements.</p>}
          </section>
          <section className="analysis-group">
            <h3>Important interactive elements</h3>
            {result.important_interactive_elements.length ? (
              <div className="analysis-card-grid">
                {result.important_interactive_elements.map((item, index) => (
                  <InterpretationCard
                    key={`${item.evidence.page_id}-${index}`}
                    title={evidenceElementName(pages.find((page) => page.id === item.evidence.page_id)?.elements.find((element) => element.id === item.evidence.element_ids[0]))}
                    interpretation={item.interpretation}
                    evidence={[item.evidence]}
                    pages={pages}
                  />
                ))}
              </div>
            ) : <p className="analysis-empty-note">No important interactive elements were identified.</p>}
          </section>
          <section className="analysis-group">
            <h3>Forms and fields</h3>
            {result.forms_and_fields.length ? (
              <div className="analysis-card-grid">
                {result.forms_and_fields.map((formGroup) => {
                  const page = pages.find((candidate) => candidate.id === formGroup.page_id);
                  const formElements = formGroup.form_element_ids.map((id) => page?.elements.find((element) => element.id === id)).filter((element): element is AnalysisPageElement => Boolean(element));
                  const fields = formGroup.field_element_ids.map((id) => page?.elements.find((element) => element.id === id)).filter((element): element is AnalysisPageElement => Boolean(element));
                  return (
                    <article className="analysis-card" key={formGroup.page_id}>
                      <h4>{page?.title || "Saved page"}</h4>
                      <span className="analysis-kind">Observed DOM elements · grouped by page</span>
                      <p>{pageLabel(page, formGroup.page_id)}</p>
                      {formElements.length > 0 && <div><strong>Form elements</strong><ul>{formElements.map((element) => <li key={element.id}>{evidenceElementName(element)} <code>{element.selector || element.tag_name}</code></li>)}</ul></div>}
                      {fields.length > 0 && <div><strong>Fields</strong><ul>{fields.map((element) => <li key={element.id}>{evidenceElementName(element)} <small>Role: {element.role || element.kind} · Tag: {element.tag_name}</small><code>{element.selector || "Locator unavailable"}</code></li>)}</ul></div>}
                      <small>Exact field-to-form ownership was not captured by the crawler.</small>
                    </article>
                  );
                })}
              </div>
            ) : <p className="analysis-empty-note">No form or field elements were saved in this exploration.</p>}
          </section>
          <section className="analysis-group">
            <h3>Potential user workflows</h3>
            {result.potential_workflows.length ? (
              <div className="analysis-card-grid">
                {result.potential_workflows.map((workflow, index) => (
                  <article className="analysis-card workflow-card" key={`${workflow.title}-${index}`}>
                    <h4>{workflow.title}</h4>
                    <span className="analysis-kind">AI-generated test suggestion</span>
                    <p><strong>Objective:</strong> {workflow.objective}</p>
                    <div><strong>Relevant pages</strong><ul>{workflow.relevant_page_ids.map((pageId) => <li key={pageId}>{pageLabel(pages.find((page) => page.id === pageId), pageId)}</li>)}</ul></div>
                    <ol>{workflow.steps.map((step, stepIndex) => <li key={stepIndex}><p>{step.instruction}</p><EvidenceList evidence={step.evidence} pages={pages} /></li>)}</ol>
                    <p><strong>Expected outcome:</strong> {workflow.expected_outcome}</p>
                  </article>
                ))}
              </div>
            ) : <p className="analysis-empty-note">No safe workflow suggestions could be supported by the saved elements.</p>}
          </section>
        </div>
      )}
    </section>
  );
}
