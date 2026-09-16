import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, {
  type ForceGraphMethods,
} from "react-force-graph-2d";
import type { GraphData, GraphNode, SelectedEdge } from "../types";
import { useTheme } from "../ThemeContext";
import CodePanel from "./CodePanel";
import EdgeDetail from "./EdgeDetail";
import NodeDetail from "./NodeDetail";

const NODE_COLORS: Record<string, string> = {
  file: "#94a3b8",       // slate — neutral anchor, not a "code" node
  folder: "#cbd5e1",     // lighter slate — structural container
  class: "#facc15",      // yellow — primary OOP construct, high visibility
  interface: "#4ade80",  // green — contracts/protocols
  function: "#60a5fa",   // blue — callable
  property: "#fb923c",   // orange — data/fields
  enum: "#f87171",       // red — fixed value sets
  duck_type: "#ffffff",  // white — inferred types
  code_block: "#a1a1aa",  // zinc — ambient code
  type_alias: "#2dd4bf",  // teal — type system
  struct: "#c084fc",     // purple — value types
  module: "#22d3ee",     // cyan — structural grouping
  annotation: "#fbbf24", // amber — metadata (distinct from class yellow)
};

const EDGE_COLORS: Record<string, string> = {
  CONTAINS: "#475569",
  EXPECTS: "#f59e0b",
  IS_SUBSET_OF: "#ec4899",
  IMPORTS: "#38bdf8",       // sky blue
  INHERITS: "#a78bfa",      // violet
  IMPLEMENTS: "#34d399",     // emerald
  OVERRIDES: "#e879f9",      // fuchsia — method redefinition
  DECORATED_BY: "#fbbf24",   // amber
  METACLASS: "#f472b6",      // pink
  CALLS: "#fb923c",          // orange
  INSTANTIATES: "#c084fc",   // purple
  TYPE_OF: "#2dd4bf",        // teal
};

const NODE_SIZES: Record<string, number> = {
  folder: 8,
  module: 8,
  file: 6,
  class: 5,
  interface: 5,
  enum: 4.5,
  struct: 4.5,
  duck_type: 4,
  function: 3.5,
  property: 3,
  type_alias: 3,
  annotation: 3,
  code_block: 3,
};

// --- Search / filter language ---

const EDGE_FIELDS = new Set(["relation", "confidence", "resolved_by"]);

interface ParsedQuery {
  text: string;
  predicates: { key: string; re: RegExp }[];
  hasEdgePredicate: boolean;
}

function globToRegex(pattern: string): RegExp {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*");
  return new RegExp(`^${escaped}$`, "i");
}

function parseQuery(raw: string): ParsedQuery | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;

  const predicates: { key: string; re: RegExp }[] = [];
  let hasEdgePredicate = false;
  const textParts: string[] = [];

  const predRe = /\{(\w+):([^}]+)\}/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = predRe.exec(trimmed)) !== null) {
    const before = trimmed.slice(last, m.index).trim();
    if (before) textParts.push(before);
    last = m.index + m[0].length;
    const key = m[1];
    predicates.push({ key, re: globToRegex(m[2].trim()) });
    if (EDGE_FIELDS.has(key)) hasEdgePredicate = true;
  }
  const tail = trimmed.slice(last).trim();
  if (tail) textParts.push(tail);

  const text = textParts.join(" ").toLowerCase();
  return { text, predicates, hasEdgePredicate };
}

const NODE_FIELD_ALIASES: Record<string, string[]> = {
  type: ["type", "node_type"],
};

function nodeMatchesQuery(node: GraphNode, q: ParsedQuery): boolean {
  if (q.text) {
    const name = node.name.toLowerCase();
    const sig = (node.signature ?? "").toLowerCase();
    if (!name.includes(q.text) && !sig.includes(q.text)) return false;
  }
  for (const { key, re } of q.predicates) {
    if (EDGE_FIELDS.has(key)) continue;
    const fields = NODE_FIELD_ALIASES[key] ?? [key];
    const matched = fields.some((f) => {
      const val = node[f];
      return val !== undefined && val !== null && re.test(String(val));
    });
    if (!matched) return false;
  }
  return true;
}

const TAG_GROUP_LABELS: Record<string, string> = {
  cat: "Category",
  type: "Node Type",
  lang: "Language",
  dir: "Directory",
};

const TAG_GROUP_ORDER = ["cat", "type", "lang", "dir"];

interface Props {
  data: GraphData;
  selectedId: string | null;
  onSelectedIdChange: (id: string | null) => void;
  selectedEdge: SelectedEdge | null;
  onSelectedEdgeChange: (edge: SelectedEdge | null) => void;
  codePanelOpen: boolean;
  onCodePanelChange: (open: boolean) => void;
}

export default function ForceGraph({ data, selectedId, onSelectedIdChange, selectedEdge, onSelectedEdgeChange, codePanelOpen, onCodePanelChange }: Props) {
  const { theme } = useTheme();
  const fgRef = useRef<ForceGraphMethods | undefined>(undefined);
  const [filterOpen, setFilterOpen] = useState(false);
  const [searchFocused, setSearchFocused] = useState(false);
  const [highlightK, setHighlightK] = useState(0);
  const [showEdgeLabels, setShowEdgeLabels] = useState(
    () => localStorage.getItem("jcp-edge-labels") === "true",
  );

  useEffect(() => {
    localStorage.setItem("jcp-edge-labels", String(showEdgeLabels));
  }, [showEdgeLabels]);
  const [searchQuery, setSearchQuery] = useState(
    () => localStorage.getItem("jcp-graph-search") ?? "",
  );

  useEffect(() => {
    localStorage.setItem("jcp-graph-search", searchQuery);
  }, [searchQuery]);

  const [hiddenTags, setHiddenTags] = useState<Set<string>>(() => {
    try {
      const stored = localStorage.getItem("jcp-graph-hidden-tags");
      return stored ? new Set(JSON.parse(stored) as string[]) : new Set();
    } catch {
      return new Set();
    }
  });

  useEffect(() => {
    localStorage.setItem("jcp-graph-hidden-tags", JSON.stringify([...hiddenTags]));
  }, [hiddenTags]);

  const selected = useMemo(
    () => data.nodes.find((n) => n.id === selectedId) ?? null,
    [data.nodes, selectedId],
  );

  const [, setTick] = useState(0);
  useEffect(() => {
    if (!selectedId && !selectedEdge) return;
    const id = setInterval(() => setTick((t) => t + 1), 40);
    return () => clearInterval(id);
  }, [selectedId, selectedEdge]);

  const allTags = useMemo(() => {
    const grouped = new Map<string, Set<string>>();
    for (const node of data.nodes) {
      for (const tag of node.tags ?? []) {
        const [prefix] = tag.split(":", 2);
        if (!grouped.has(prefix)) grouped.set(prefix, new Set());
        grouped.get(prefix)!.add(tag);
      }
    }
    return grouped;
  }, [data]);

  const hiddenNodeIds = useMemo(() => {
    if (hiddenTags.size === 0) return new Set<string>();

    // Group hidden tags by prefix
    const hiddenByGroup = new Map<string, Set<string>>();
    for (const tag of hiddenTags) {
      const prefix = tag.split(":")[0];
      if (!hiddenByGroup.has(prefix)) hiddenByGroup.set(prefix, new Set());
      hiddenByGroup.get(prefix)!.add(tag);
    }

    const hidden = new Set<string>();
    for (const node of data.nodes) {
      const tags = node.tags ?? [];
      if (tags.length === 0) continue;

      // Group node's tags by prefix
      const nodeByGroup = new Map<string, string[]>();
      for (const tag of tags) {
        const prefix = tag.split(":")[0];
        if (!nodeByGroup.has(prefix)) nodeByGroup.set(prefix, []);
        nodeByGroup.get(prefix)!.push(tag);
      }

      // A node is hidden if in ANY active filter group, ALL of the
      // node's tags in that group are hidden.
      // For dir: tags, also check ancestor paths (hiding a parent hides children).
      let shouldHide = false;
      for (const [prefix, hiddenSet] of hiddenByGroup) {
        const nodeTags = nodeByGroup.get(prefix);
        if (!nodeTags) continue;
        if (prefix === "dir") {
          const allDirHidden = nodeTags.every((t) => {
            if (hiddenSet.has(t)) return true;
            const dirPath = t.slice(4);
            const parts = dirPath.split("/");
            for (let i = 1; i < parts.length; i++) {
              if (hiddenSet.has(`dir:${parts.slice(0, i).join("/")}`)) return true;
            }
            return false;
          });
          if (allDirHidden) { shouldHide = true; break; }
        } else {
          if (nodeTags.every((t) => hiddenSet.has(t))) {
            shouldHide = true;
            break;
          }
        }
      }
      if (shouldHide) hidden.add(node.id);
    }
    return hidden;
  }, [data, hiddenTags]);

  const matchedNodeIds = useMemo((): Set<string> | null => {
    const q = parseQuery(searchQuery);
    if (!q) return null;

    const matched = new Set<string>();
    for (const node of data.nodes) {
      if (nodeMatchesQuery(node, q)) matched.add(node.id);
    }

    if (q.hasEdgePredicate) {
      for (const edge of data.edges) {
        let edgeMatches = true;
        for (const { key, re } of q.predicates) {
          if (!EDGE_FIELDS.has(key)) continue;
          const val = (edge as unknown as Record<string, unknown>)[key];
          if (val === undefined || val === null || !re.test(String(val))) {
            edgeMatches = false;
            break;
          }
        }
        if (edgeMatches) {
          matched.add(edge.source);
          matched.add(edge.target);
        }
      }
    }

    return matched;
  }, [data, searchQuery]);

  const searchActive = matchedNodeIds !== null;

  const kNeighborIds = useMemo((): Set<string> | null => {
    if (highlightK <= 0) return null;

    const seeds: string[] = [];
    if (matchedNodeIds !== null) {
      seeds.push(...matchedNodeIds);
    } else if (selectedId) {
      seeds.push(selectedId);
    }
    if (seeds.length === 0) return null;

    const adj = new Map<string, Set<string>>();
    for (const e of data.edges) {
      if (!adj.has(e.source)) adj.set(e.source, new Set());
      if (!adj.has(e.target)) adj.set(e.target, new Set());
      adj.get(e.source)!.add(e.target);
      adj.get(e.target)!.add(e.source);
    }

    const visited = new Set<string>(seeds);
    let frontier = seeds;
    for (let step = 0; step < highlightK && frontier.length > 0; step++) {
      const next: string[] = [];
      for (const id of frontier) {
        for (const nb of adj.get(id) ?? []) {
          if (!visited.has(nb)) {
            visited.add(nb);
            next.push(nb);
          }
        }
      }
      frontier = next;
    }
    return visited;
  }, [data.edges, selectedId, highlightK, matchedNodeIds]);

  const graphData = useMemo(() => {
    const nodeMap = new Set(data.nodes.map((n) => n.id));
    return {
      nodes: data.nodes.map((n) => ({ ...n })),
      links: data.edges
        .filter((e) => nodeMap.has(e.source) && nodeMap.has(e.target))
        .map((e) => ({
          source: e.source,
          target: e.target,
          relation: e.relation,
        })),
    };
  }, [data]);

  const handleNodeClick = useCallback(
    (node: { id?: string | number }) => {
      const found = data.nodes.find((n) => n.id === node.id) ?? null;
      onSelectedIdChange(found?.id ?? null);
    },
    [data.nodes, onSelectedIdChange],
  );

  const handleLinkClick = useCallback(
    (link: { source: { id?: string } | string; target: { id?: string } | string; relation?: string }) => {
      const srcId = typeof link.source === "string" ? link.source : link.source.id ?? "";
      const tgtId = typeof link.target === "string" ? link.target : link.target.id ?? "";
      onSelectedEdgeChange({ source: srcId, target: tgtId, relation: link.relation ?? "" });
    },
    [onSelectedEdgeChange],
  );

  const toggleTag = (tag: string) => {
    setHiddenTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  };

  const toggleGroup = (prefix: string) => {
    const groupTags = allTags.get(prefix);
    if (!groupTags) return;
    const allHidden = [...groupTags].every((t) => hiddenTags.has(t));
    setHiddenTags((prev) => {
      const next = new Set(prev);
      for (const t of groupTags) {
        if (allHidden) next.delete(t);
        else next.add(t);
      }
      return next;
    });
  };

  return (
    <div className="relative w-full h-full">
      <ForceGraph2D
        ref={fgRef}
        graphData={graphData}
        nodeLabel={(node) => {
          const n = node as GraphNode;
          return n.signature || n.name;
        }}
        nodeVisibility={(node) => {
          const n = node as GraphNode;
          return !hiddenNodeIds.has(n.id);
        }}
        linkVisibility={(link) => {
          const l = link as { source: { id?: string } | string; target: { id?: string } | string };
          const srcId = typeof l.source === "string" ? l.source : l.source.id ?? "";
          const tgtId = typeof l.target === "string" ? l.target : l.target.id ?? "";
          return !hiddenNodeIds.has(srcId) && !hiddenNodeIds.has(tgtId);
        }}
        nodeCanvasObject={(node, ctx, globalScale) => {
          const n = node as GraphNode & { x: number; y: number };
          if (hiddenNodeIds.has(n.id)) return;
          const inKHop = kNeighborIds !== null && kNeighborIds.has(n.id);
          const dimBySearch = searchActive && !matchedNodeIds!.has(n.id) && !inKHop;
          const dimByK = kNeighborIds !== null && !inKHop;
          const isDimmed = dimBySearch || dimByK;
          const baseSize = NODE_SIZES[n.node_type] ?? 3;
          const size = isDimmed ? baseSize * 0.6 : baseSize;
          const color = NODE_COLORS[n.node_type] ?? "#9ca3af";
          const isSelected = n.id === selectedId;

          const prevAlpha = ctx.globalAlpha;
          if (isDimmed) ctx.globalAlpha = 0.08;

          if (isSelected) {
            const t = Date.now() / 1000;
            const pulse = 0.5 + 0.5 * Math.sin(t * 3);
            const ringRadius = size + 4 + pulse * 3;
            const alpha = 0.25 + 0.15 * pulse;

            ctx.beginPath();
            ctx.arc(n.x, n.y, ringRadius, 0, 2 * Math.PI);
            ctx.fillStyle = `rgba(99, 102, 241, ${alpha * 0.5})`;
            ctx.fill();
            ctx.strokeStyle = `rgba(129, 140, 248, ${0.6 + 0.4 * pulse})`;
            ctx.lineWidth = (2 + pulse) / globalScale;
            ctx.stroke();

            ctx.beginPath();
            ctx.arc(n.x, n.y, ringRadius + 2, 0, 2 * Math.PI);
            ctx.strokeStyle = `rgba(129, 140, 248, ${0.15 + 0.1 * pulse})`;
            ctx.lineWidth = 1 / globalScale;
            ctx.stroke();
          }

          ctx.beginPath();
          ctx.arc(n.x, n.y, size, 0, 2 * Math.PI);
          ctx.fillStyle = color;
          ctx.fill();

          if (!isDimmed && globalScale > 2) {
            ctx.font = `${10 / globalScale}px sans-serif`;
            ctx.textAlign = "center";
            ctx.textBaseline = "top";
            ctx.fillStyle = "#d1d5db";
            ctx.fillText(n.name, n.x, n.y + size + 2);
          }

          ctx.globalAlpha = prevAlpha;
        }}
        nodePointerAreaPaint={(node, color, ctx) => {
          const n = node as GraphNode & { x: number; y: number };
          if (hiddenNodeIds.has(n.id)) return;
          const size = NODE_SIZES[n.node_type] ?? 3;
          ctx.beginPath();
          ctx.arc(n.x, n.y, size + 3, 0, 2 * Math.PI);
          ctx.fillStyle = color;
          ctx.fill();
        }}
        linkColor={(link) => {
          const l = link as { relation?: string; source: { id?: string } | string; target: { id?: string } | string };
          const srcId = typeof l.source === "string" ? l.source : l.source.id ?? "";
          const tgtId = typeof l.target === "string" ? l.target : l.target.id ?? "";
          const srcInK = kNeighborIds !== null && kNeighborIds.has(srcId);
          const tgtInK = kNeighborIds !== null && kNeighborIds.has(tgtId);
          const bothInK = srcInK && tgtInK;
          if (kNeighborIds !== null && !bothInK) {
            return "rgba(71,85,105,0.04)";
          }
          if (searchActive && !bothInK && (!matchedNodeIds!.has(srcId) || !matchedNodeIds!.has(tgtId))) {
            return "rgba(71,85,105,0.04)";
          }
          return EDGE_COLORS[l.relation ?? "CONTAINS"] ?? "#475569";
        }}
        linkWidth={(link) => {
          if (!selectedEdge) return 1;
          const l = link as { relation?: string; source: { id?: string } | string; target: { id?: string } | string };
          const srcId = typeof l.source === "string" ? l.source : l.source.id ?? "";
          const tgtId = typeof l.target === "string" ? l.target : l.target.id ?? "";
          if (srcId === selectedEdge.source && tgtId === selectedEdge.target && (l.relation ?? "") === selectedEdge.relation) {
            return 3;
          }
          return 1;
        }}
        linkCanvasObjectMode={(link) => {
          if (showEdgeLabels) return "after";
          if (!selectedEdge) return undefined as unknown as string;
          const l = link as { relation?: string; source: { id?: string } | string; target: { id?: string } | string };
          const srcId = typeof l.source === "string" ? l.source : l.source.id ?? "";
          const tgtId = typeof l.target === "string" ? l.target : l.target.id ?? "";
          if (srcId === selectedEdge.source && tgtId === selectedEdge.target && (l.relation ?? "") === selectedEdge.relation) {
            return "after";
          }
          return undefined as unknown as string;
        }}
        linkCanvasObject={(link, ctx, globalScale) => {
          const l = link as { relation?: string; source: { x: number; y: number; id?: string } | string; target: { x: number; y: number; id?: string } | string };
          const src = l.source as { x: number; y: number; id?: string };
          const tgt = l.target as { x: number; y: number; id?: string };
          const srcId = typeof l.source === "string" ? l.source : src.id ?? "";
          const tgtId = typeof l.target === "string" ? l.target : tgt.id ?? "";

          const isSelected = selectedEdge && srcId === selectedEdge.source && tgtId === selectedEdge.target && (l.relation ?? "") === selectedEdge.relation;
          if (!showEdgeLabels && !isSelected) return;

          const edgeSrcInK = kNeighborIds !== null && kNeighborIds.has(srcId);
          const edgeTgtInK = kNeighborIds !== null && kNeighborIds.has(tgtId);
          const edgeBothInK = edgeSrcInK && edgeTgtInK;
          const dimByK = kNeighborIds !== null && !edgeBothInK;
          const dimBySearch = searchActive && !edgeBothInK && (!matchedNodeIds!.has(srcId) || !matchedNodeIds!.has(tgtId));
          if (!isSelected && (dimBySearch || dimByK)) return;

          if (!isSelected && showEdgeLabels && globalScale < 1.5) return;

          const midX = (src.x + tgt.x) / 2;
          const midY = (src.y + tgt.y) / 2;
          const label = l.relation ?? "";
          const fontSize = isSelected ? 3 : Math.min(3, 10 / globalScale);

          ctx.save();
          ctx.font = `${fontSize}px sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";

          const tw = ctx.measureText(label).width;
          const pad = 1.5;
          ctx.fillStyle = isSelected ? "rgba(15, 23, 42, 0.85)" : "rgba(15, 23, 42, 0.6)";
          ctx.fillRect(midX - tw / 2 - pad, midY - fontSize / 2 - pad, tw + pad * 2, fontSize + pad * 2);

          ctx.globalAlpha = isSelected ? 1 : 0.7;
          ctx.fillStyle = EDGE_COLORS[label] ?? "#94a3b8";
          ctx.fillText(label, midX, midY);
          ctx.restore();
        }}
        linkDirectionalArrowLength={4}
        linkDirectionalArrowRelPos={1}
        onNodeClick={handleNodeClick}
        onLinkClick={handleLinkClick}
        backgroundColor={theme.colors.canvasBg}
      />

      {selected && !codePanelOpen && (
        <NodeDetail node={selected} onClose={() => onSelectedIdChange(null)} />
      )}
      {selected && codePanelOpen && (
        <CodePanel node={selected} files={data.files} onClose={() => onSelectedIdChange(null)} />
      )}
      {!selected && selectedEdge && (
        <EdgeDetail edge={selectedEdge} data={data} onClose={() => onSelectedEdgeChange(null)} onSelectNode={onSelectedIdChange} />
      )}

      {/* Search bar */}
      <div className="absolute top-4 left-1/2 -translate-x-1/2 flex flex-col items-center gap-1 z-40">
        <div className="flex items-center gap-1">
          <div className={`relative flex items-center ${theme.colors.panelBgAlpha} backdrop-blur-sm rounded-lg border ${theme.colors.border}`}>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onFocus={() => setSearchFocused(true)}
              onBlur={() => setTimeout(() => setSearchFocused(false), 150)}
              placeholder="Search... e.g. BaseNode {type:class}"
              className="bg-transparent text-sm text-gray-200 placeholder-gray-500 px-3 py-1.5 w-72 outline-none"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="pr-2 text-gray-500 hover:text-gray-300 text-sm"
              >
                &times;
              </button>
            )}
          </div>
          {searchActive && (
            <span className="text-xs text-gray-400 whitespace-nowrap">
              {matchedNodeIds!.size} match{matchedNodeIds!.size !== 1 ? "es" : ""}
            </span>
          )}
        </div>
        {searchFocused && !searchQuery && (
          <div className={`${theme.colors.panelBgAlpha} backdrop-blur-sm rounded-lg border ${theme.colors.border} px-3 py-2 text-xs text-gray-400 w-72 space-y-1.5`}>
            <p className="text-gray-300 font-medium">Search syntax</p>
            <p>Type to search by name or signature</p>
            <p>
              Use <code className="text-gray-300 bg-gray-700/50 px-1 rounded">{"{field:pattern}"}</code> to filter by field
            </p>
            <div className="space-y-0.5 text-gray-500">
              <p><code className="text-gray-400">{"{type:class}"}</code> — node type</p>
              <p><code className="text-gray-400">{"{owner:*Node}"}</code> — glob wildcards</p>
              <p><code className="text-gray-400">{"{relation:CALLS}"}</code> — edge type</p>
              <p><code className="text-gray-400">{"{language:python}"}</code> — language</p>
            </div>
            <p className="text-gray-500">Combine freely: <code className="text-gray-400">{"{type:function}"} parse</code></p>
          </div>
        )}
      </div>

      {/* K-hop highlight slider */}
      {(selectedId || searchActive) && (
        <div className={`absolute bottom-14 right-4 flex items-center gap-2 ${theme.colors.panelBgAlpha} backdrop-blur-sm rounded-lg px-3 py-1.5 border ${theme.colors.border}`}>
          <span className="text-xs text-gray-400 whitespace-nowrap">Show Neighbours</span>
          <input
            type="range"
            min={0}
            max={6}
            value={highlightK}
            onChange={(e) => setHighlightK(Number(e.target.value))}
            className="w-24 accent-indigo-500"
          />
          <span className="text-xs text-gray-300 w-14 text-center">
            {highlightK === 0 ? "disabled" : `dist <= ${highlightK}`}
          </span>
        </div>
      )}

      {/* Top-left controls */}
      <div className="absolute top-4 left-4 flex items-center gap-2">
        <button
          onClick={() => setFilterOpen((p) => !p)}
          className={`px-3 py-1.5 rounded-lg text-sm font-medium transition ${
            filterOpen
              ? "bg-indigo-600 text-white"
              : `${theme.colors.panelBgAlpha} text-gray-300 hover:${theme.colors.surfaceBg}`
          }`}
        >
          Filters{hiddenTags.size > 0 ? ` (${hiddenTags.size})` : ""}
        </button>

        {selected && Object.keys(data.files).length > 0 && (
          <div className={`flex rounded-lg overflow-hidden border ${theme.colors.border}`}>
            <button
              onClick={() => onCodePanelChange(false)}
              className={`px-3 py-1.5 text-sm font-medium transition ${
                !codePanelOpen
                  ? "bg-indigo-600 text-white"
                  : `${theme.colors.panelBgAlpha} text-gray-400 hover:text-gray-200`
              }`}
            >
              Info
            </button>
            <button
              onClick={() => onCodePanelChange(true)}
              className={`px-3 py-1.5 text-sm font-medium transition ${
                codePanelOpen
                  ? "bg-indigo-600 text-white"
                  : `${theme.colors.panelBgAlpha} text-gray-400 hover:text-gray-200`
              }`}
            >
              Code
            </button>
          </div>
        )}
      </div>

      {/* Filter panel */}
      {filterOpen && (
        <div className={`absolute top-14 left-4 ${theme.colors.panelBgAlpha} backdrop-blur-sm rounded-lg p-3 text-xs space-y-3 max-h-[70vh] overflow-y-auto min-w-[200px] border ${theme.colors.border}`}>
          <label className="flex items-center gap-2 cursor-pointer text-gray-300 hover:text-white mb-2">
            <span
              className={`w-3 h-3 rounded-sm border flex items-center justify-center ${
                showEdgeLabels
                  ? "border-indigo-500 bg-indigo-500"
                  : "border-gray-600 bg-transparent"
              }`}
              onClick={() => setShowEdgeLabels((p) => !p)}
            >
              {showEdgeLabels && <span className="text-white text-[8px]">✓</span>}
            </span>
            <span className="text-sm" onClick={() => setShowEdgeLabels((p) => !p)}>Display Edge Labels</span>
          </label>
          <div className="border-t border-gray-700 pt-2" />
          <div className="flex items-center justify-between mb-1">
            <span className="text-gray-300 font-semibold text-sm">Layers</span>
            {hiddenTags.size > 0 && (
              <button
                onClick={() => setHiddenTags(new Set())}
                className="text-indigo-400 hover:text-indigo-300"
              >
                Show all
              </button>
            )}
          </div>
          {TAG_GROUP_ORDER.map((prefix) => {
            const tags = allTags.get(prefix);
            if (!tags || tags.size === 0) return null;

            if (prefix === "dir") {
              return (
                <DirTreeFilter
                  key={prefix}
                  tags={tags}
                  hiddenTags={hiddenTags}
                  onToggleTag={toggleTag}
                  onToggleGroup={toggleGroup}
                />
              );
            }

            const sorted = [...tags].sort();
            const allHidden = sorted.every((t) => hiddenTags.has(t));
            const someHidden = sorted.some((t) => hiddenTags.has(t));
            return (
              <div key={prefix}>
                <button
                  onClick={() => toggleGroup(prefix)}
                  className="flex items-center gap-1.5 text-gray-400 hover:text-gray-200 font-medium mb-1"
                >
                  <span
                    className={`w-3 h-3 rounded-sm border flex items-center justify-center ${
                      allHidden
                        ? "border-gray-600 bg-transparent"
                        : someHidden
                        ? "border-indigo-500 bg-indigo-500/50"
                        : "border-indigo-500 bg-indigo-500"
                    }`}
                  >
                    {!allHidden && <span className="text-white text-[8px]">✓</span>}
                  </span>
                  {TAG_GROUP_LABELS[prefix] ?? prefix}
                </button>
                <div className="ml-4 space-y-0.5">
                  {sorted.map((tag) => {
                    const label = tag.split(":")[1];
                    const isHidden = hiddenTags.has(tag);
                    const colorDot = prefix === "type" ? NODE_COLORS[label] : undefined;
                    return (
                      <label
                        key={tag}
                        className="flex items-center gap-1.5 cursor-pointer text-gray-300 hover:text-white"
                      >
                        <input
                          type="checkbox"
                          checked={!isHidden}
                          onChange={() => toggleTag(tag)}
                          className="hidden"
                        />
                        <span
                          className={`w-3 h-3 rounded-sm border flex items-center justify-center ${
                            isHidden
                              ? "border-gray-600 bg-transparent"
                              : "border-indigo-500 bg-indigo-500"
                          }`}
                        >
                          {!isHidden && <span className="text-white text-[8px]">✓</span>}
                        </span>
                        {colorDot && (
                          <span
                            className="w-2 h-2 rounded-full inline-block"
                            style={{ backgroundColor: colorDot }}
                          />
                        )}
                        {label}
                      </label>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Legend — horizontal bar at bottom */}
      <div className={`absolute bottom-4 left-4 right-4 ${theme.colors.panelBgAlpha} rounded-lg px-4 py-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs`}>
        {Object.entries(NODE_COLORS).map(([type, color]) => (
          <div key={type} className="flex items-center gap-1.5">
            <span
              className="w-2.5 h-2.5 rounded-full inline-block"
              style={{ backgroundColor: color }}
            />
            <span className="text-gray-300">{type}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// --- Directory tree filter ---

interface DirTreeNode {
  name: string;
  tag: string;
  children: DirTreeNode[];
}

function buildDirTree(tags: Set<string>): DirTreeNode[] {
  const root: DirTreeNode[] = [];
  const sorted = [...tags].sort();

  for (const tag of sorted) {
    const dirPath = tag.slice(4); // strip "dir:"
    const segments = dirPath.split("/");
    let level = root;
    let accumulated = "";
    for (const seg of segments) {
      accumulated = accumulated ? `${accumulated}/${seg}` : seg;
      let existing = level.find((n) => n.name === seg);
      if (!existing) {
        existing = { name: seg, tag: `dir:${accumulated}`, children: [] };
        level.push(existing);
      }
      level = existing.children;
    }
  }
  return root;
}

function collectDescendantTags(node: DirTreeNode): string[] {
  const result = [node.tag];
  for (const child of node.children) {
    result.push(...collectDescendantTags(child));
  }
  return result;
}

function DirTreeFilter({
  tags,
  hiddenTags,
  onToggleTag,
  onToggleGroup,
}: {
  tags: Set<string>;
  hiddenTags: Set<string>;
  onToggleTag: (tag: string) => void;
  onToggleGroup: (prefix: string) => void;
}) {
  const tree = useMemo(() => buildDirTree(tags), [tags]);
  const allTags = [...tags];
  const allHidden = allTags.every((t) => hiddenTags.has(t));
  const someHidden = allTags.some((t) => hiddenTags.has(t));

  return (
    <div>
      <button
        onClick={() => onToggleGroup("dir")}
        className="flex items-center gap-1.5 text-gray-400 hover:text-gray-200 font-medium mb-1"
      >
        <span
          className={`w-3 h-3 rounded-sm border flex items-center justify-center ${
            allHidden
              ? "border-gray-600 bg-transparent"
              : someHidden
              ? "border-indigo-500 bg-indigo-500/50"
              : "border-indigo-500 bg-indigo-500"
          }`}
        >
          {!allHidden && <span className="text-white text-[8px]">✓</span>}
        </span>
        Directory
      </button>
      <div className="ml-4 space-y-0.5">
        {tree.map((node) => (
          <DirTreeItem key={node.tag} node={node} hiddenTags={hiddenTags} allDirTags={tags} onToggleTag={onToggleTag} depth={0} />
        ))}
      </div>
    </div>
  );
}

function DirTreeItem({
  node,
  hiddenTags,
  allDirTags,
  onToggleTag,
  depth,
}: {
  node: DirTreeNode;
  hiddenTags: Set<string>;
  allDirTags: Set<string>;
  onToggleTag: (tag: string) => void;
  depth: number;
}) {
  const [expanded, setExpanded] = useState(depth < 1);
  const hasChildren = node.children.length > 0;

  const descendantTags = useMemo(() => collectDescendantTags(node), [node]);
  const allDescHidden = descendantTags.filter((t) => allDirTags.has(t)).every((t) => hiddenTags.has(t));
  const someDescHidden = descendantTags.filter((t) => allDirTags.has(t)).some((t) => hiddenTags.has(t));

  const handleToggle = () => {
    const relevantTags = descendantTags.filter((t) => allDirTags.has(t));
    if (allDescHidden) {
      for (const t of relevantTags) {
        if (hiddenTags.has(t)) onToggleTag(t);
      }
    } else {
      for (const t of relevantTags) {
        if (!hiddenTags.has(t)) onToggleTag(t);
      }
    }
  };

  return (
    <div>
      <div className="flex items-center gap-1 cursor-pointer text-gray-300 hover:text-white">
        {hasChildren ? (
          <button
            onClick={() => setExpanded((p) => !p)}
            className="w-3 text-center text-gray-500 hover:text-gray-300 text-[10px] flex-shrink-0"
          >
            {expanded ? "▾" : "▸"}
          </button>
        ) : (
          <span className="w-3 flex-shrink-0" />
        )}
        <span
          onClick={handleToggle}
          className={`w-3 h-3 rounded-sm border flex items-center justify-center flex-shrink-0 ${
            allDescHidden
              ? "border-gray-600 bg-transparent"
              : someDescHidden
              ? "border-indigo-500 bg-indigo-500/50"
              : "border-indigo-500 bg-indigo-500"
          }`}
        >
          {!allDescHidden && <span className="text-white text-[8px]">✓</span>}
        </span>
        <span onClick={() => hasChildren && setExpanded((p) => !p)} className="truncate">
          {node.name}
        </span>
      </div>
      {hasChildren && expanded && (
        <div className="ml-4 space-y-0.5">
          {node.children.map((child) => (
            <DirTreeItem key={child.tag} node={child} hiddenTags={hiddenTags} allDirTags={allDirTags} onToggleTag={onToggleTag} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}
