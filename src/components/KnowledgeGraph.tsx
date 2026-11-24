import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import {
  knowledgeGraphPath,
  rebuildKnowledgeGraphPath,
  type KnowledgeGraphEdge,
  type GraphNodeType,
  type KnowledgeGraphNode,
  type KnowledgeGraphResponse,
} from "./knowledgeGraphApi";

type GraphPageOption = { id: string; title: string; url: string };
type GraphRequest = <T>(path: string, init?: RequestInit) => Promise<T>;
type Resource = { scope: string; data: KnowledgeGraphResponse | null; loading: boolean; error: string | null };

const NODE_TYPES: { value: GraphNodeType; label: string }[] = [
  { value: "exploration", label: "Exploration" },
  { value: "page", label: "Pages" },
  { value: "ui_element", label: "UI elements" },
  { value: "form", label: "Forms" },
  { value: "workflow", label: "Workflows" },
  { value: "test_case", label: "Test cases" },
  { value: "interaction_state", label: "Interaction states" },
];

const NODE_COLORS: Record<GraphNodeType, string> = {
  exploration: "#64748b",
  page: "#2563eb",
  ui_element: "#52525b",
  form: "#a16207",
  workflow: "#7c3aed",
  test_case: "#0f766e",
  interaction_state: "#b45309",
};

const NODE_LABELS: Record<GraphNodeType, string> = Object.fromEntries(
  NODE_TYPES.map(({ value, label }) => [value, label]),
) as Record<GraphNodeType, string>;

function messageFromError(error: unknown) {
  return error instanceof Error ? error.message : "Knowledge graph request failed.";
}

function propertyText(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

export function KnowledgeGraphSection({
  explorationId,
  pages,
  request,
}: {
  explorationId: string | null;
  pages: GraphPageOption[];
  request: GraphRequest;
}) {
  const [nodeType, setNodeType] = useState<GraphNodeType | "all">("all");
  const [pageId, setPageId] = useState("all");
  const [provenance, setProvenance] = useState<"verified" | "suggested" | "all">("verified");
  const [refreshToken, setRefreshToken] = useState(0);
  const [resource, setResource] = useState<Resource>({ scope: "", data: null, loading: false, error: null });
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);
  const [buildError, setBuildError] = useState<string | null>(null);
  const [buildMessage, setBuildMessage] = useState<string | null>(null);
  const currentExplorationId = useRef(explorationId);
  currentExplorationId.current = explorationId;

  const effectivePageId = pageId !== "all" && pages.some((page) => page.id === pageId) ? pageId : "all";
  const scope = `${explorationId || ""}|${nodeType}|${effectivePageId}|${provenance}`;
  useEffect(() => {
    setPageId("all");
    setSelectedNodeId(null);
    setBuildError(null);
    setBuildMessage(null);
  }, [explorationId]);
  useEffect(() => {
    let current = true;
    if (!explorationId) {
      setResource({ scope, data: null, loading: false, error: null });
      setSelectedNodeId(null);
      return () => { current = false; };
    }
    setResource((old) => ({ scope, data: old.scope === scope ? old.data : null, loading: true, error: null }));
    void request<KnowledgeGraphResponse>(
      knowledgeGraphPath(explorationId, { nodeType, pageId: effectivePageId, provenance, limit: 1000 }),
    ).then((data) => {
      if (current) setResource({ scope, data, loading: false, error: null });
    }).catch((error: unknown) => {
      if (current) setResource({ scope, data: null, loading: false, error: messageFromError(error) });
    });
    return () => { current = false; };
  }, [explorationId, nodeType, effectivePageId, provenance, refreshToken, request]);

  const graph = resource.scope === scope ? resource.data : null;
  const loading = Boolean(explorationId) && (resource.scope !== scope || resource.loading);
  const selectedNode = graph?.nodes.find((node) => node.id === selectedNodeId) || null;
  const nodeById = useMemo(() => new Map((graph?.nodes || []).map((node) => [node.id, node])), [graph]);
  const connected = useMemo<Array<{
    edge: KnowledgeGraphEdge;
    node: KnowledgeGraphNode;
    direction: "incoming" | "outgoing";
  }>>(() => {
    if (!graph || !selectedNode) return [];
    const results: Array<{
      edge: KnowledgeGraphEdge;
      node: KnowledgeGraphNode;
      direction: "incoming" | "outgoing";
    }> = [];
    for (const edge of graph.edges) {
      if (edge.source_node_id === selectedNode.id) {
        const node = nodeById.get(edge.target_node_id);
        if (node) results.push({ edge, node, direction: "outgoing" });
        continue;
      }
      if (edge.target_node_id === selectedNode.id) {
        const node = nodeById.get(edge.source_node_id);
        if (node) results.push({ edge, node, direction: "incoming" });
      }
    }
    return results;
  }, [graph, nodeById, selectedNode]);

  useEffect(() => {
    if (!graph) return;
    if (selectedNodeId && graph.nodes.some((node) => node.id === selectedNodeId)) return;
    const root = graph.nodes.find((node) => node.node_type === "exploration");
    setSelectedNodeId(root?.id || graph.nodes[0]?.id || null);
  }, [graph, selectedNodeId]);

  const rebuild = async () => {
    if (!explorationId || building) return;
    setBuilding(true);
    setBuildError(null);
    setBuildMessage(null);
    try {
      const result = await request<{
        nodes_written: number;
        edges_written: number;
        skipped_invalid_workflows: number;
        skipped_invalid_interactions: number;
      }>(rebuildKnowledgeGraphPath(explorationId), { method: "POST" });
      if (currentExplorationId.current !== explorationId) return;
      setBuildMessage(
        `Synchronized ${result.nodes_written} nodes and ${result.edges_written} relationships` +
          (result.skipped_invalid_workflows || result.skipped_invalid_interactions
            ? `; skipped ${result.skipped_invalid_workflows} invalid workflow suggestions and ${result.skipped_invalid_interactions} invalid interaction records.`
            : "."),
      );
      setRefreshToken((value) => value + 1);
    } catch (error) {
      if (currentExplorationId.current === explorationId) setBuildError(messageFromError(error));
    } finally {
      setBuilding(false);
    }
  };

  const renderedNeighbors = connected.slice(0, 24);
  const focusNodes = selectedNode ? [selectedNode, ...renderedNeighbors.map((entry) => entry.node)] : [];
  const positions = new Map<string, { x: number; y: number }>();
  if (focusNodes.length) positions.set(focusNodes[0].id, { x: 500, y: 270 });
  renderedNeighbors.forEach((entry, index) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * index) / Math.max(1, renderedNeighbors.length);
    positions.set(entry.node.id, { x: 500 + Math.cos(angle) * 350, y: 270 + Math.sin(angle) * 185 });
  });
  const canvasEdges = graph?.edges.filter((edge) => positions.has(edge.source_node_id) && positions.has(edge.target_node_id)) || [];

  const selectGraphNode = (node: KnowledgeGraphNode) => setSelectedNodeId(node.id);
  const onGraphKeyDown = (event: KeyboardEvent<SVGGElement>, node: KnowledgeGraphNode) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectGraphNode(node);
    }
  };

  return (
    <section className="knowledge-graph-panel" aria-labelledby="knowledge-graph-title">
      <div className="knowledge-graph-heading">
        <div>
          <h2 id="knowledge-graph-title">Knowledge graph</h2>
          <p>Verified structure from saved exploration evidence. AI workflow suggestions are labeled separately.</p>
        </div>
        <button className="secondary" type="button" onClick={() => void rebuild()} disabled={!explorationId || building}>
          {building ? "Building graph…" : "Build / refresh graph"}
        </button>
      </div>

      {!explorationId && <p className="analysis-empty-note" role="status">Select an exploration to view its graph.</p>}
      {buildMessage && <p className="knowledge-graph-message" role="status">{buildMessage}</p>}
      {buildError && <p className="form-error" role="alert">Graph build failed: {buildError}</p>}

      <div className="knowledge-graph-filters">
        <label>Node type
          <select value={nodeType} onChange={(event) => setNodeType(event.target.value as GraphNodeType | "all")}>
            <option value="all">All node types</option>
            {NODE_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        <label>Page
          <select value={effectivePageId} onChange={(event) => setPageId(event.target.value)}>
            <option value="all">All pages</option>
            {pages.map((page) => <option key={page.id} value={page.id}>{page.title || page.url}</option>)}
          </select>
        </label>
        <label>Relationship layer
          <select value={provenance} onChange={(event) => setProvenance(event.target.value as "verified" | "suggested" | "all")}>
            <option value="verified">Verified relationships</option>
            <option value="all">All relationships</option>
            <option value="suggested">Suggested with evidence context</option>
          </select>
        </label>
        {graph && <span>{graph.total_nodes} nodes · {graph.total_edges} relationships</span>}
      </div>

      {loading && !graph && <p className="workspace-message" role="status">Loading saved graph…</p>}
      {resource.error && <p className="form-error" role="alert">Knowledge graph could not be loaded: {resource.error}</p>}
      {!loading && !resource.error && graph && graph.nodes.length === 0 && (
        <div className="knowledge-graph-empty" role="status">
          <p>No graph records match these filters yet. Build or refresh the graph to synchronize saved evidence.</p>
          <button className="secondary" type="button" onClick={() => void rebuild()} disabled={building}>
            {building ? "Building graph…" : "Build from saved evidence"}
          </button>
        </div>
      )}

      {!resource.error && graph && graph.nodes.length > 0 && (
        <>
          {graph.truncated && <p className="knowledge-graph-message" role="status">This view is limited to {graph.returned_nodes} nodes. Filter by page or type to inspect other parts of the graph.</p>}
          <div className="knowledge-graph-workspace">
            <div className="knowledge-graph-canvas-wrap">
              <svg className="knowledge-graph-canvas" viewBox="0 0 1000 540" role="img" aria-label="Selected knowledge graph node and its connected nodes">
                {canvasEdges.map((edge) => {
                  const start = positions.get(edge.source_node_id)!;
                  const end = positions.get(edge.target_node_id)!;
                  return <line key={edge.id} x1={start.x} y1={start.y} x2={end.x} y2={end.y} className={`kg-edge ${edge.provenance}`} />;
                })}
                {focusNodes.map((node, index) => {
                  const point = positions.get(node.id)!;
                  const selected = node.id === selectedNode?.id;
                  const showLabel = selected || index <= 7;
                  return (
                    <g
                      key={node.id}
                      className={`kg-node${selected ? " selected" : ""}`}
                      transform={`translate(${point.x} ${point.y})`}
                      role="button"
                      tabIndex={0}
                      aria-label={`${NODE_LABELS[node.node_type]}: ${node.label}`}
                      onClick={() => selectGraphNode(node)}
                      onKeyDown={(event) => onGraphKeyDown(event, node)}
                    >
                      <circle r={selected ? 12 : 8} fill={NODE_COLORS[node.node_type]} />
                      {showLabel && <text x={selected ? 17 : 12} y="4">{node.label.slice(0, selected ? 48 : 24)}</text>}
                      <title>{`${NODE_LABELS[node.node_type]} · ${node.label} · ${node.provenance}`}</title>
                    </g>
                  );
                })}
              </svg>
              {selectedNode && <p className="knowledge-graph-focus">Selected: {NODE_LABELS[selectedNode.node_type]} · {selectedNode.label}</p>}
              {connected.length > renderedNeighbors.length && <p className="knowledge-graph-focus">Showing {renderedNeighbors.length} of {connected.length} connections; use filters to narrow this view.</p>}
            </div>

            <aside className="knowledge-graph-inspector" aria-label="Selected graph node details">
              <label>Inspect node
                <select value={selectedNode?.id || ""} onChange={(event) => setSelectedNodeId(event.target.value || null)}>
                  {graph.nodes.map((node) => <option key={node.id} value={node.id}>{NODE_LABELS[node.node_type]} · {node.label}</option>)}
                </select>
              </label>
              {selectedNode ? (
                <>
                  <dl>
                    <div><dt>Type</dt><dd>{NODE_LABELS[selectedNode.node_type]}</dd></div>
                    <div><dt>Source ID</dt><dd>{selectedNode.source_key}</dd></div>
                    <div><dt>Evidence</dt><dd>{selectedNode.provenance === "suggested" ? "AI suggestion" : "Persisted record"}</dd></div>
                  </dl>
                  <h3>Connected nodes</h3>
                  {connected.length === 0 ? <p className="analysis-empty-note">No relationships in this filtered view.</p> : (
                    <ul className="knowledge-graph-connections">
                      {connected.map(({ edge, node, direction }) => (
                        <li key={edge.id}>
                          <button type="button" onClick={() => selectGraphNode(node)}>
                            <span>{direction === "outgoing" ? "→" : "←"} {edge.edge_type}</span>
                            <strong>{NODE_LABELS[node.node_type]} · {node.label}</strong>
                            <small>{edge.provenance} · {edge.evidence.length} evidence references</small>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                  <h3>Source evidence</h3>
                  <ul className="knowledge-graph-evidence">
                    {selectedNode.evidence.map((reference, index) => (
                      <li key={`${reference.source_type}:${reference.source_id}:${index}`}>
                        <span>{reference.source_type}</span>
                        <code>{reference.source_id}</code>
                      </li>
                    ))}
                  </ul>
                  {Object.keys(selectedNode.properties).length > 0 && (
                    <details className="knowledge-graph-properties">
                      <summary>Node facts</summary>
                      <dl>{Object.entries(selectedNode.properties).map(([key, value]) => (
                        <div key={key}><dt>{key.replace(/_/g, " ")}</dt><dd>{propertyText(value)}</dd></div>
                      ))}</dl>
                    </details>
                  )}
                </>
              ) : <p className="analysis-empty-note">Select a node to inspect its evidence and connections.</p>}
            </aside>
          </div>
        </>
      )}
    </section>
  );
}
