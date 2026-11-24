const workspaceSlugs: Record<string, string> = {
  Overview: "overview",
  Projects: "projects",
  Exploration: "exploration",
  Interactions: "interactions",
  "AI Analysis": "ai-analysis",
  "Knowledge Graph": "knowledge-graph",
  "Workflow Intelligence": "workflow-intelligence",
  "Test Cases": "test-cases",
  Reports: "reports",
  Settings: "settings",
};

const slugWorkspaces = Object.fromEntries(
  Object.entries(workspaceSlugs).map(([workspace, slug]) => [slug, workspace]),
) as Record<string, string>;

export function workspaceFromSearch(search: string, fallback: string): string {
  const slug = new URLSearchParams(search).get("workspace");
  return (slug && slugWorkspaces[slug]) || fallback;
}

export function workspaceLocation(href: string, workspace: string): string {
  const slug = workspaceSlugs[workspace];
  if (!slug) return href;
  const url = new URL(href);
  url.searchParams.set("workspace", slug);
  return url.pathname + url.search + url.hash;
}

export function sidebarWorkspace(workspace: string): string {
  return workspace === "Interactions" || workspace === "AI Analysis" ? "Exploration" : workspace;
}
