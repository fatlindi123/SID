export type GraphNodeType =
  | "exploration"
  | "page"
  | "ui_element"
  | "form"
  | "workflow"
  | "test_case"
  | "interaction_state";

export type GraphEdgeType =
  | "contains_page"
  | "contains_element"
  | "links_to"
  | "belongs_to_form"
  | "participates_in_workflow"
  | "has_test_case"
  | "transitions_to_state";

export type GraphEvidenceReference = {
  source_type: "exploration" | "page" | "element" | "analysis" | "test_case" | "interaction_observation";
  source_id: string;
  page_id: string | null;
  element_id: string | null;
};

export type KnowledgeGraphNode = {
  id: string;
  exploration_id: string;
  node_type: GraphNodeType;
  source_key: string;
  label: string;
  page_id: string | null;
  provenance: "verified" | "suggested";
  evidence: GraphEvidenceReference[];
  properties: Record<string, unknown>;
};

export type KnowledgeGraphEdge = {
  id: string;
  exploration_id: string;
  source_node_id: string;
  target_node_id: string;
  edge_type: GraphEdgeType;
  provenance: "verified" | "suggested";
  evidence: GraphEvidenceReference[];
  properties: Record<string, unknown>;
};

export type KnowledgeGraphResponse = {
  exploration_id: string;
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  total_nodes: number;
  total_edges: number;
  returned_nodes: number;
  truncated: boolean;
};

export type KnowledgeGraphBuildResponse = {
  exploration_id: string;
  nodes_written: number;
  edges_written: number;
  verified_nodes: number;
  suggested_nodes: number;
  verified_edges: number;
  suggested_edges: number;
  skipped_invalid_workflows: number;
  skipped_invalid_interactions: number;
};

export function knowledgeGraphPath(
  explorationId: string,
  filters: { nodeType?: GraphNodeType | "all"; pageId?: string | "all"; provenance?: "verified" | "suggested" | "all"; limit?: number } = {},
) {
  const query = new URLSearchParams();
  if (filters.nodeType && filters.nodeType !== "all") query.set("node_type", filters.nodeType);
  if (filters.pageId && filters.pageId !== "all") query.set("page_id", filters.pageId);
  if (filters.provenance && filters.provenance !== "all") query.set("provenance", filters.provenance);
  if (filters.limit) query.set("limit", String(filters.limit));
  const suffix = query.toString();
  return `/explorations/${explorationId}/knowledge-graph${suffix ? `?${suffix}` : ""}`;
}

export function rebuildKnowledgeGraphPath(explorationId: string) {
  return `/explorations/${explorationId}/knowledge-graph/rebuild`;
}
