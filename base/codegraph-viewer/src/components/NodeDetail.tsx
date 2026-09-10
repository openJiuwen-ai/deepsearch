import type { GraphNode } from "../types";
import { useTheme } from "../ThemeContext";

interface Props {
  node: GraphNode | null;
  onClose: () => void;
}

export default function NodeDetail({ node, onClose }: Props) {
  const { theme } = useTheme();
  if (!node) return null;

  return (
    <div className={`absolute top-4 right-4 w-96 max-h-[80vh] overflow-y-auto ${theme.colors.panelBg} border border-gray-600 rounded-lg shadow-xl p-4 z-50`}>
      <div className="flex justify-between items-start mb-3">
        <h3 className="text-lg font-semibold text-white truncate pr-2">
          {node.name}
        </h3>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-white text-xl leading-none"
        >
          &times;
        </button>
      </div>

      <div className="space-y-2 text-sm">
        <Field label="Type" value={node.type} />
        <Field label="Node Type" value={node.node_type} />
        <Field label="Path" value={node.path} />
        <Field
          label="Span"
          value={`L${node.span[0]}:${node.span[2]} - L${node.span[1]}:${node.span[3]}`}
        />
        {node.signature && <Field label="Signature" value={node.signature} />}
        {node.docstring && (
          <div>
            <span className="text-gray-400 text-xs uppercase">Docstring</span>
            <pre className="mt-1 text-xs text-gray-300 bg-gray-900 p-2 rounded overflow-x-auto whitespace-pre-wrap">
              {node.docstring}
            </pre>
          </div>
        )}

        {Object.entries(node)
          .filter(
            ([k]) =>
              ![
                "id",
                "type",
                "name",
                "node_type",
                "path",
                "span",
                "signature",
                "docstring",
                "source",
              ].includes(k)
          )
          .filter(([, v]) => v !== null && v !== undefined)
          .map(([k, v]) => (
            <Field
              key={k}
              label={k}
              value={typeof v === "object" ? JSON.stringify(v) : String(v)}
            />
          ))}
      </div>
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
