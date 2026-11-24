export type ReportType = "exploration" | "project";

export type ReportHistoryFilters = {
  reportType?: ReportType;
  projectId?: string;
  explorationId?: string;
  generatedFrom?: string;
  generatedTo?: string;
  limit?: number;
  offset?: number;
};

export function reportHistoryPath(filters: ReportHistoryFilters = {}): string {
  const query = new URLSearchParams();
  if (filters.reportType) query.set("report_type", filters.reportType);
  if (filters.projectId) query.set("project_id", filters.projectId);
  if (filters.explorationId) query.set("exploration_id", filters.explorationId);
  if (filters.generatedFrom) query.set("generated_from", filters.generatedFrom);
  if (filters.generatedTo) query.set("generated_to", filters.generatedTo);
  if (filters.limit !== undefined) query.set("limit", String(filters.limit));
  if (filters.offset !== undefined) query.set("offset", String(filters.offset));
  const suffix = query.toString();
  return "/reports" + (suffix ? "?" + suffix : "");
}

export function reportGenerationPath(type: ReportType, scopeId: string): string {
  const scope = encodeURIComponent(scopeId);
  return type === "project" ? `/reports/projects/${scope}` : `/reports/explorations/${scope}`;
}

export function reportDetailPath(reportId: string): string {
  return `/reports/${encodeURIComponent(reportId)}`;
}

export function reportExportPath(reportId: string, format: "pdf" | "json" | "print"): string {
  const suffix = format === "print" ? "/print" : `/export.${format}`;
  return reportDetailPath(reportId) + suffix;
}
