export interface GraphNode {
  id: string;
  type: string;
  name: string;
  node_type: string;
  path: string;
  span: [number, number, number, number];
  signature?: string;
  docstring?: string;
  tags?: string[];
  [key: string]: unknown;
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: string;
  confidence?: number;
  resolved_by?: string;
}

export interface SelectedEdge {
  source: string;
  target: string;
  relation: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  files: Record<string, string>;
}
