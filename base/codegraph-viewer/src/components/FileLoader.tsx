import { useCallback } from "react";
import type { GraphData } from "../types";
import { loadGraphFromFile } from "../utils/parse-jsonl";

interface Props {
  onLoad: (data: GraphData) => void;
}

export default function FileLoader({ onLoad }: Props) {
  const handleFile = useCallback(
    async (file: File) => {
      try {
        const data = await loadGraphFromFile(file);
        onLoad(data);
      } catch (err) {
        alert(err instanceof Error ? err.message : "Failed to load file");
      }
    },
    [onLoad]
  );

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      const file = e.dataTransfer.files[0];
      if (file) await handleFile(file);
    },
    [handleFile]
  );

  const handleFileChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) await handleFile(file);
    },
    [handleFile]
  );

  return (
    <div
      onDragOver={(e) => e.preventDefault()}
      onDrop={handleDrop}
      className="flex flex-col items-center justify-center gap-6 p-12 border-2 border-dashed border-gray-600 rounded-xl"
    >
      <p className="text-lg text-gray-300">
        Drop a <code className="text-indigo-400">.jcp</code> file here, or
        select one below:
      </p>
      <label className="cursor-pointer px-6 py-2 bg-indigo-600 rounded hover:bg-indigo-500 transition text-white font-medium">
        Choose .jcp file
        <input
          type="file"
          accept=".jcp"
          className="hidden"
          onChange={handleFileChange}
        />
      </label>
      <p className="text-sm text-gray-500">
        Generate with:{" "}
        <code className="text-gray-400">
          python -m openjiuwen_search_base.codegraph.export ./your_project
        </code>
      </p>
    </div>
  );
}
