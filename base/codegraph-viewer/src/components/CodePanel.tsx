import { useMemo } from "react";
import type { GraphNode } from "../types";
import { useTheme } from "../ThemeContext";

interface Props {
  node: GraphNode | null;
  files: Record<string, string>;
  onClose: () => void;
}

function resolveFileContent(
  node: GraphNode,
  files: Record<string, string>,
): string | null {
  const nodePath = node.path;
  if (files[nodePath]) return files[nodePath];

  for (const [key, content] of Object.entries(files)) {
    if (nodePath.endsWith(key) || key.endsWith(nodePath)) return content;
    const normNode = nodePath.replace(/\\/g, "/");
    const normKey = key.replace(/\\/g, "/");
    if (normNode.endsWith(normKey) || normKey.endsWith(normNode)) return content;
  }
  return null;
}

export default function CodePanel({ node, files, onClose }: Props) {
  const { theme } = useTheme();

  const snippet = useMemo(() => {
    if (!node) return null;
    const content = resolveFileContent(node, files);
    if (!content) return null;

    const allLines = content.split("\n");
    const start = Math.max(0, node.span[0] - 1);
    const end = Math.min(allLines.length, node.span[1]);
    if (start >= end) return null;

    return {
      lines: allLines.slice(start, end),
      startLine: node.span[0],
    };
  }, [node, files]);

  if (!node || !snippet) return null;

  return (
    <div className={`absolute top-4 right-4 w-[480px] max-h-[85vh] flex flex-col ${theme.colors.panelBg} border ${theme.colors.border} rounded-lg shadow-xl z-50`}>
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-700">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs text-gray-500 uppercase font-medium">Code</span>
          <span className="text-sm text-gray-300 truncate">{node.name}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">
            L{node.span[0]}&ndash;{node.span[1]}
          </span>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white text-lg leading-none"
          >
            &times;
          </button>
        </div>
      </div>
      <div className="overflow-auto flex-1">
        <table className="text-xs leading-relaxed font-mono border-collapse w-full">
          <tbody>
            {snippet.lines.map((line, i) => {
              const lineNum = snippet.startLine + i;
              return (
                <tr key={lineNum} className="hover:bg-white/5">
                  <td className="select-none text-gray-600 text-right pr-3 pl-2 align-top sticky left-0 bg-inherit w-10"
                      style={{ minWidth: "2.5rem" }}>
                    {lineNum}
                  </td>
                  <td className="text-gray-300 whitespace-pre">{line || " "}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="px-4 py-1.5 border-t border-gray-700">
        <span className="text-xs text-gray-500 truncate block">{node.path}</span>
      </div>
    </div>
  );
}
