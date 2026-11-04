import { useEffect, useMemo, useState } from "react";
import type { GraphData, GraphNode } from "../types";
import { useTheme } from "../ThemeContext";
import NodeDetail from "./NodeDetail";

interface TreeNode {
  node: GraphNode;
  children: TreeNode[];
}

interface Props {
  data: GraphData;
  selectedId: string | null;
  onSelectedIdChange: (id: string | null) => void;
}

export default function TreeView({ data, selectedId, onSelectedIdChange }: Props) {
  const { theme } = useTheme();
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    try {
      const stored = localStorage.getItem("jcp-tree-expanded");
      return stored ? new Set(JSON.parse(stored) as string[]) : new Set();
    } catch {
      return new Set();
    }
  });

  useEffect(() => {
    localStorage.setItem("jcp-tree-expanded", JSON.stringify([...expanded]));
  }, [expanded]);

  const selected = useMemo(
    () => data.nodes.find((n) => n.id === selectedId) ?? null,
    [data.nodes, selectedId],
  );

  const tree = useMemo(() => buildTree(data), [data]);

  const filteredTree = useMemo(() => {
    if (!filter.trim()) return tree;
    const lowerFilter = filter.toLowerCase();
    return tree
      .map((root) => filterTree(root, lowerFilter))
      .filter((t): t is TreeNode => t !== null);
  }, [tree, filter]);

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="flex h-full">
      <div className="flex-1 overflow-y-auto p-4">
        <input
          type="text"
          placeholder="Filter by name or type..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className={`w-full mb-4 px-3 py-2 ${theme.colors.panelBg} border border-gray-600 rounded text-gray-200 placeholder-gray-500 focus:outline-none focus:border-indigo-500`}
        />
        <div className="space-y-0.5">
          {filteredTree.map((root) => (
            <TreeItem
              key={root.node.id}
              item={root}
              depth={0}
              expanded={expanded}
              onToggle={toggle}
              onSelect={(node) => onSelectedIdChange(node.id)}
            />
          ))}
        </div>
      </div>
      {selected && (
        <div className={`w-96 border-l ${theme.colors.border} overflow-y-auto`}>
          <NodeDetail node={selected} onClose={() => onSelectedIdChange(null)} />
        </div>
      )}
    </div>
  );
}

function TreeItem({
  item,
  depth,
  expanded,
  onToggle,
  onSelect,
}: {
  item: TreeNode;
  depth: number;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onSelect: (node: GraphNode) => void;
}) {
  const isOpen = expanded.has(item.node.id);
  const hasChildren = item.children.length > 0;

  return (
    <div>
      <div
        className="flex items-center gap-1 py-0.5 px-2 rounded hover:bg-gray-800 cursor-pointer text-sm"
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        onClick={() => onSelect(item.node)}
      >
        {hasChildren && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggle(item.node.id);
            }}
            className="w-4 text-gray-400 hover:text-white"
          >
            {isOpen ? "▼" : "▶"}
          </button>
        )}
        {!hasChildren && <span className="w-4" />}
        <TypeBadge type={item.node.node_type} />
        <span className="text-gray-200 truncate">{item.node.name}</span>
      </div>
      {isOpen &&
        item.children.map((child) => (
          <TreeItem
            key={child.node.id}
            item={child}
            depth={depth + 1}
            expanded={expanded}
            onToggle={onToggle}
            onSelect={onSelect}
          />
        ))}
    </div>
  );
}

function TypeBadge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    file: "bg-slate-500",
    folder: "bg-slate-400",
    class: "bg-yellow-500",
    interface: "bg-green-500",
    function: "bg-blue-500",
    property: "bg-orange-500",
    enum: "bg-red-500",
    duck_type: "bg-fuchsia-500",
    code_block: "bg-zinc-500",
    type_alias: "bg-teal-500",
    struct: "bg-purple-500",
    module: "bg-cyan-500",
    annotation: "bg-amber-500",
  };
  return (
    <span
      className={`px-1.5 py-0.5 rounded text-[10px] font-medium text-white ${colors[type] ?? "bg-gray-600"}`}
    >
      {type}
    </span>
  );
}

function buildTree(data: GraphData): TreeNode[] {
  const nodeMap = new Map<string, GraphNode>();
  for (const node of data.nodes) nodeMap.set(node.id, node);

  // Build raw CONTAINS adjacency
  const rawChildren = new Map<string, string[]>();
  for (const edge of data.edges) {
    if (edge.relation !== "CONTAINS") continue;
    if (!rawChildren.has(edge.source)) rawChildren.set(edge.source, []);
    rawChildren.get(edge.source)!.push(edge.target);
  }

  // Find folders that own a module — the module replaces the folder in
  // the tree.  Map: folder id → module id.
  const folderToModule = new Map<string, string>();
  for (const edge of data.edges) {
    if (
      edge.relation === "CONTAINS" &&
      edge.source.startsWith("folder::") &&
      edge.target.startsWith("module::")
    ) {
      folderToModule.set(edge.source, edge.target);
    }
  }

  // Build the resolved children map: replace folder nodes that have a
  // module with the module itself.  A replaced folder's non-module
  // children (plain subfolders without their own module) get re-parented
  // to the module.
  const childrenMap = new Map<string, string[]>();
  const hasParent = new Set<string>();

  function addChild(parent: string, child: string) {
    if (!childrenMap.has(parent)) childrenMap.set(parent, []);
    childrenMap.get(parent)!.push(child);
    hasParent.add(child);
  }

  for (const [parentId, kids] of rawChildren) {
    // If this parent is a folder replaced by a module, skip it entirely —
    // the module inherits its position via its grandparent.
    if (folderToModule.has(parentId)) continue;

    for (const childId of kids) {
      // If the child is a folder that has a module, promote the module.
      const replacement = folderToModule.get(childId);
      addChild(parentId, replacement ?? childId);
    }
  }

  // Module nodes own their own children (files + submodules) from
  // rawChildren — add those.
  for (const [parentId, kids] of rawChildren) {
    if (!parentId.startsWith("module::")) continue;
    for (const childId of kids) {
      const replacement = folderToModule.get(childId);
      addChild(parentId, replacement ?? childId);
    }
  }

  const roots = data.nodes.filter(
    (n) => !hasParent.has(n.id) && !folderToModule.has(n.id),
  );

  function buildSubtree(nodeId: string): TreeNode | null {
    const node = nodeMap.get(nodeId);
    if (!node) return null;
    const childIds = childrenMap.get(nodeId) ?? [];
    const children = childIds
      .map(buildSubtree)
      .filter((t): t is TreeNode => t !== null);
    return { node, children };
  }

  return roots
    .map((r) => buildSubtree(r.id))
    .filter((t): t is TreeNode => t !== null);
}

function filterTree(tree: TreeNode, query: string): TreeNode | null {
  const matches =
    tree.node.name.toLowerCase().includes(query) ||
    tree.node.node_type.toLowerCase().includes(query);

  const filteredChildren = tree.children
    .map((c) => filterTree(c, query))
    .filter((c): c is TreeNode => c !== null);

  if (matches || filteredChildren.length > 0) {
    return { node: tree.node, children: matches ? tree.children : filteredChildren };
  }
  return null;
}
