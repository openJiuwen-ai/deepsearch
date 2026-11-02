import type { GraphData, SelectedEdge } from "../types";
import { useTheme } from "../ThemeContext";

interface Props {
  edge: SelectedEdge;
  data: GraphData;
  onClose: () => void;
  onSelectNode: (id: string | null) => void;
}

export default function EdgeDetail({ edge, data, onClose, onSelectNode }: Props) {
  const { theme } = useTheme();

  const sourceNode = data.nodes.find((n) => n.id === edge.source);
  const targetNode = data.nodes.find((n) => n.id === edge.target);

  const fullEdge = data.edges.find(
    (e) => e.source === edge.source && e.target === edge.target && e.relation === edge.relation,
  );

  return (
    <div className={`absolute top-4 right-4 w-96 max-h-[80vh] overflow-y-auto ${theme.colors.panelBg} border border-gray-600 rounded-lg shadow-xl p-4 z-50`}>
      <div className="flex justify-between items-start mb-3">
        <h3 className="text-lg font-semibold text-white truncate pr-2">
          {edge.relation}
        </h3>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-white text-xl leading-none"
        >
          &times;
        </button>
      </div>

      <div className="space-y-3 text-sm">
        <NodeLink label="Source" node={sourceNode} onClick={() => sourceNode && onSelectNode(sourceNode.id)} />
        <NodeLink label="Target" node={targetNode} onClick={() => targetNode && onSelectNode(targetNode.id)} />

        <Field label="Relation" value={edge.relation} />
        {fullEdge?.confidence !== undefined && (
          <Field label="Confidence" value={String(fullEdge.confidence)} />
        )}
        {fullEdge?.resolved_by && (
          <Field label="Resolved By" value={fullEdge.resolved_by} />
        )}
      </div>
    </div>
  );
}

function NodeLink({ label, node, onClick }: { label: string; node: { name: string; node_type: string } | undefined; onClick: () => void }) {
  if (!node) return null;
  return (
    <div>
      <span className="text-gray-400 text-xs uppercase">{label}</span>
      <button
        onClick={onClick}
        className="block text-indigo-400 hover:text-indigo-300 hover:underline break-all text-left"
      >
        {node.name}
        <span className="text-gray-500 ml-1 text-xs">({node.node_type})</span>
      </button>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-gray-400 text-xs uppercase">{label}</span>
      <p className="text-gray-200 break-all">{value}</p>
    </div>
  );
}
