import { useCallback, useEffect, useState } from "react";
import type { GraphData, SelectedEdge } from "./types";
import BrandLogo from "./components/BrandLogo";
import FileLoader from "./components/FileLoader";
import ForceGraph from "./components/ForceGraph";
import ThemePicker from "./components/ThemePicker";
import TreeView from "./components/TreeView";
import { useTheme } from "./ThemeContext";
import {
  clearStorage,
  loadFromStorage,
  saveToStorage,
} from "./utils/parse-jsonl";

type Tab = "graph" | "tree";

export default function App() {
  const { theme } = useTheme();
  const [data, setData] = useState<GraphData | null>(null);
  const [tab, setTab] = useState<Tab>(
    () => (localStorage.getItem("jcp-view") as Tab) || "graph",
  );
  const [graphSelectedId, setGraphSelectedId] = useState<string | null>(
    () => localStorage.getItem("jcp-sel-graph"),
  );
  const [treeSelectedId, setTreeSelectedId] = useState<string | null>(
    () => localStorage.getItem("jcp-sel-tree"),
  );
  const [selectedEdge, setSelectedEdge] = useState<SelectedEdge | null>(null);
  const [codePanelOpen, setCodePanelOpen] = useState(
    () => localStorage.getItem("jcp-code-panel") === "true",
  );

  useEffect(() => {
    localStorage.setItem("jcp-code-panel", String(codePanelOpen));
  }, [codePanelOpen]);

  useEffect(() => {
    localStorage.setItem("jcp-view", tab);
  }, [tab]);

  useEffect(() => {
    if (graphSelectedId) localStorage.setItem("jcp-sel-graph", graphSelectedId);
    else localStorage.removeItem("jcp-sel-graph");
  }, [graphSelectedId]);

  useEffect(() => {
    if (treeSelectedId) localStorage.setItem("jcp-sel-tree", treeSelectedId);
    else localStorage.removeItem("jcp-sel-tree");
  }, [treeSelectedId]);

  useEffect(() => {
    const cached = loadFromStorage();
    if (cached) setData(cached);
  }, []);

  const handleLoad = useCallback((newData: GraphData) => {
    setData(newData);
    saveToStorage(newData);
  }, []);

  const handleGraphNodeSelect = useCallback((id: string | null) => {
    setGraphSelectedId(id);
    setSelectedEdge(null);
  }, []);

  const handleEdgeSelect = useCallback((edge: SelectedEdge | null) => {
    setSelectedEdge(edge);
    if (edge) setGraphSelectedId(null);
  }, []);

  const handleReset = useCallback(() => {
    setData(null);
    setGraphSelectedId(null);
    setTreeSelectedId(null);
    setSelectedEdge(null);
    clearStorage();
  }, []);

  if (!data) {
    return (
      <div className="min-h-screen flex items-center justify-center p-8">
        <div className="max-w-lg w-full">
          <div className="flex flex-col items-center gap-4 mb-8">
            <BrandLogo size="lg" />
            <h1 className="text-3xl font-bold text-center text-white">
              Jiuwen Code Graph Viewer
            </h1>
          </div>
          <FileLoader onLoad={handleLoad} />
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col">
      <header className={`flex items-center gap-4 px-4 py-2 ${theme.colors.headerBg} border-b ${theme.colors.border}`}>
        <div className="flex items-center gap-2">
          <BrandLogo />
          <h1 className="text-lg font-semibold text-white">Jiuwen Code Graph Viewer</h1>
        </div>
        <span className="text-sm text-gray-400">
          {data.nodes.length} nodes &middot; {data.edges.length} edges
        </span>
        <div className="ml-auto flex gap-1">
          <TabButton active={tab === "graph"} onClick={() => setTab("graph")}>
            Force Graph
          </TabButton>
          <TabButton active={tab === "tree"} onClick={() => setTab("tree")}>
            Tree View
          </TabButton>
          <button
            onClick={handleReset}
            className="px-3 py-1 text-sm rounded bg-gray-700 text-gray-300 hover:bg-gray-600 ml-2"
          >
            Load New
          </button>
          <ThemePicker />
        </div>
      </header>
      <main className="flex-1 overflow-hidden">
        {tab === "graph" ? (
          <ForceGraph data={data} selectedId={graphSelectedId} onSelectedIdChange={handleGraphNodeSelect} selectedEdge={selectedEdge} onSelectedEdgeChange={handleEdgeSelect} codePanelOpen={codePanelOpen} onCodePanelChange={setCodePanelOpen} />
        ) : (
          <TreeView data={data} selectedId={treeSelectedId} onSelectedIdChange={setTreeSelectedId} />
        )}
      </main>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1 text-sm rounded transition ${
        active
          ? "bg-indigo-600 text-white"
          : "bg-gray-700 text-gray-300 hover:bg-gray-600"
      }`}
    >
      {children}
    </button>
  );
}
