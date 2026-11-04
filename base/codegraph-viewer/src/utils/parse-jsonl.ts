import type { GraphNode, GraphEdge, GraphData } from "../types";

export function parseJsonl<T>(text: string): T[] {
  return text
    .split("\n")
    .filter((line) => line.trim().length > 0)
    .map((line) => JSON.parse(line) as T);
}

function isMarker(line: string): boolean {
  try {
    return JSON.parse(line).marker === "break";
  } catch {
    return false;
  }
}

export function parseJcp(text: string): GraphData {
  const lines = text.split("\n").filter((line) => line.trim().length > 0);

  const markerIndices: number[] = [];
  for (let i = 0; i < lines.length; i++) {
    if (isMarker(lines[i])) markerIndices.push(i);
  }

  if (markerIndices.length < 1) {
    throw new Error('Invalid .jcp file: missing {"marker": "break"} separator');
  }

  const nodeLines = lines.slice(0, markerIndices[0]);
  const edgeLines = markerIndices.length >= 2
    ? lines.slice(markerIndices[0] + 1, markerIndices[1])
    : lines.slice(markerIndices[0] + 1);

  const files: Record<string, string> = {};
  if (markerIndices.length >= 2) {
    const fileLines = lines.slice(markerIndices[1] + 1);
    for (const line of fileLines) {
      const rec = JSON.parse(line) as { path: string; content: string };
      files[rec.path] = rec.content;
    }
  }

  return {
    nodes: nodeLines.map((l) => JSON.parse(l) as GraphNode),
    edges: edgeLines.map((l) => JSON.parse(l) as GraphEdge),
    files,
  };
}

async function decompressGzip(file: File): Promise<string> {
  const ds = new DecompressionStream("gzip");
  const decompressed = file.stream().pipeThrough(ds);
  const reader = decompressed.getReader();
  const chunks: Uint8Array[] = [];
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
  }
  const decoder = new TextDecoder();
  return chunks.map((c) => decoder.decode(c, { stream: true })).join("") +
    decoder.decode();
}

export async function loadGraphFromFile(file: File): Promise<GraphData> {
  if (!file.name.endsWith(".jcp")) {
    throw new Error("Unsupported file format. Please load a .jcp file.");
  }
  const text = await decompressGzip(file);
  return parseJcp(text);
}

const STORAGE_KEY = "jiuwen-graph-data";

export function saveToStorage(data: GraphData): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    // localStorage might be full; silently fail
  }
}

export function loadFromStorage(): GraphData | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw) as GraphData;
    if (!data.files) data.files = {};
    return data;
  } catch {
    return null;
  }
}

export function clearStorage(): void {
  localStorage.removeItem(STORAGE_KEY);
}
