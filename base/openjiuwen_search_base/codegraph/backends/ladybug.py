"""Ladybug export backend and query helpers."""

import csv
import json
import re
import tempfile
import threading
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable

from .base import ExportArtifacts, LadybugWriteConfig

try:
    from warnings import filterwarnings

    from tqdm.rich import tqdm
    from tqdm.std import TqdmExperimentalWarning

    filterwarnings("ignore", category=TqdmExperimentalWarning)
except ImportError:
    from tqdm.auto import tqdm

_NODE_TABLE_PREFIX = "JiuwenNode"
_EDGE_TABLE_PREFIX = "JiuwenEdge"
_NODE_RESERVED_FIELDS = frozenset({"id", "type", "name", "node_type", "path", "span", "signature", "docstring", "tags"})

_db_cache: dict[str, Any] = {}
_conn_cache: dict[str, Any] = {}
_cache_lock = threading.Lock()


class LadybugUnavailableError(RuntimeError):
    """Raised when Ladybug bindings are unavailable."""


def _import_real_ladybug() -> Any:
    try:
        import real_ladybug as ladybug
    except ImportError as exc:
        raise LadybugUnavailableError(
            "real_ladybug is required for Ladybug export/query support. Install it with `uv sync --group ladybug`."
        ) from exc
    return ladybug


def reset_connection_cache() -> None:
    """Close cached Ladybug handles so later calls reopen from disk."""
    global _db_cache, _conn_cache
    with _cache_lock:
        for cache in (_conn_cache, _db_cache):
            for connectable in cache.values():
                close_fn: Callable | None = getattr(connectable, "close", None)
                if callable(close_fn):
                    close_fn()
        _db_cache = {}
        _conn_cache = {}


def get_connection(path: str | Path) -> Any:
    """Return a cached Ladybug connection for ``path``."""
    db_path = Path(path).expanduser().resolve()
    key = str(db_path)
    with _cache_lock:
        conn = _conn_cache.get(key)
        if conn is not None:
            return conn
        if not db_path.is_file():
            raise FileNotFoundError(f"Ladybug database not found: {db_path}")
        ladybug = _import_real_ladybug()
        db = ladybug.Database(key)
        conn = ladybug.Connection(db)
        _db_cache[key] = db
        _conn_cache[key] = conn
        return conn


def execute(path: str | Path, cypher: str, parameters: dict[str, Any] | None = None) -> list[Any]:
    """Run a Cypher query against a Ladybug database and materialize rows."""
    raw = get_connection(path).execute(cypher, parameters or {})
    if isinstance(raw, list):
        rows: list[Any] = []
        for part in raw:
            rows.extend(list(part))
        return rows
    return list(raw)


def _decode_json_text(value: Any) -> Any:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _decode_node(raw: dict[str, Any]) -> dict[str, Any]:
    node = dict(raw)
    tags = _decode_json_text(node.get("tags"))
    payload = _decode_json_text(node.get("payload"))
    line_start = node.pop("line_start", 0)
    col_start = node.pop("col_start", 0)
    line_end = node.pop("line_end", 0)
    col_end = node.pop("col_end", 0)
    node["span"] = [line_start, col_start, line_end, col_end]
    if tags is not None:
        node["tags"] = tags
    if isinstance(payload, dict):
        node.update(payload)
    node.pop("payload", None)
    return node


def _decode_edge(raw: dict[str, Any]) -> dict[str, Any]:
    edge = dict(raw)
    confidence = edge.get("confidence")
    if confidence not in (None, ""):
        try:
            edge["confidence"] = float(confidence)
        except (TypeError, ValueError):
            pass
    elif "confidence" in edge:
        edge.pop("confidence")
    return edge


def get_node(path: str | Path, node_id: str) -> dict[str, Any] | None:
    """Return one Jiuwen node by ID."""
    rows = execute(path, "MATCH (n {id: $id}) RETURN n LIMIT 1", {"id": node_id})
    if not rows or not rows[0]:
        return None
    cell = rows[0][0]
    if not isinstance(cell, dict):
        return None
    return _decode_node(cell)


def neighbors(path: str | Path, node_id: str, relation: str | None = None) -> list[dict[str, Any]]:
    """Return incoming and outgoing neighbors for a node."""
    params = {"id": node_id}
    rel_clause = " AND e.relation = $relation" if relation else ""
    if relation:
        params["relation"] = relation

    outgoing = execute(
        path,
        (f"MATCH (src {{id: $id}})-[e]->(dst) WHERE 1 = 1{rel_clause} RETURN e, dst"),
        params,
    )
    incoming = execute(
        path,
        (f"MATCH (src)-[e]->(dst {{id: $id}}) WHERE 1 = 1{rel_clause} RETURN e, src"),
        params,
    )

    out: list[dict[str, Any]] = []
    for row in outgoing:
        if len(row) >= 2 and isinstance(row[0], dict) and isinstance(row[1], dict):
            out.append({"direction": "outgoing", "edge": _decode_edge(row[0]), "node": _decode_node(row[1])})
    for row in incoming:
        if len(row) >= 2 and isinstance(row[0], dict) and isinstance(row[1], dict):
            out.append({"direction": "incoming", "edge": _decode_edge(row[0]), "node": _decode_node(row[1])})
    return out


def search_nodes(
    path: str | Path,
    *,
    name: str | None = None,
    node_type: str | None = None,
    path_prefix: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search Jiuwen nodes by a few common predicates."""
    limit = max(1, limit)
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if name:
        clauses.append("n.name = $name")
        params["name"] = name
    if node_type:
        clauses.append("n.node_type = $node_type")
        params["node_type"] = node_type
    if path_prefix:
        clauses.append("n.path STARTS WITH $path_prefix")
        params["path_prefix"] = path_prefix
    where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = execute(path, f"MATCH (n){where_clause} RETURN n LIMIT {limit}", params)
    hits: list[dict[str, Any]] = []
    for row in rows:
        if row and isinstance(row[0], dict):
            hits.append(_decode_node(row[0]))
    return hits


def _iter_batches(items: list[dict], batch_size: int) -> Iterable[list[dict]]:
    for idx in range(0, len(items), batch_size):
        yield items[idx : idx + batch_size]


def _batch_count(item_count: int, batch_size: int) -> int:
    """Return how many batches are needed for ``item_count`` items."""
    if item_count <= 0:
        return 0
    return (item_count + batch_size - 1) // batch_size


def _node_row(node: dict) -> tuple[Any, ...]:
    span = list(node.get("span", [0, 0, 0, 0]))
    while len(span) < 4:
        span.append(0)
    payload = {key: value for key, value in node.items() if key not in _NODE_RESERVED_FIELDS}
    return (
        node["id"],
        node["type"],
        node["name"],
        node["node_type"],
        node["path"],
        span[0],
        span[1],
        span[2],
        span[3],
        node.get("signature", ""),
        node.get("docstring", ""),
        json.dumps(node.get("tags", []), ensure_ascii=False),
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )


def _edge_row(edge: dict) -> tuple[Any, ...]:
    return (
        edge["source"],
        edge["target"],
        edge["relation"],
        str(edge.get("confidence", 1.0)),
        edge.get("resolved_by", ""),
    )


def _table_fragment(value: str) -> str:
    """Sanitize a graph label fragment for use in Ladybug table names."""
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def _node_table_name(type_name: str) -> str:
    """Return the Ladybug node table name for a Jiuwen node type."""
    return f"{_NODE_TABLE_PREFIX}_{_table_fragment(type_name)}"


def _edge_table_name(relation: str, source_type: str, target_type: str) -> str:
    """Return the Ladybug relationship table name for a Jiuwen edge kind."""
    return (
        f"{_EDGE_TABLE_PREFIX}_{_table_fragment(relation)}"
        f"__{_table_fragment(source_type)}__{_table_fragment(target_type)}"
    )


def _write_csv_file(
    tmp_dir: Path,
    prefix: str,
    batch_index: int,
    rows: Iterable[tuple[Any, ...]],
    chunk_size: int,
) -> Path:
    csv_path = tmp_dir / f"{prefix}_{batch_index:05d}.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as file_obj:
        writer = csv.writer(file_obj)
        for row_index, row in enumerate(rows, start=1):
            writer.writerow(row)
            if row_index % chunk_size == 0:
                file_obj.flush()
    return csv_path


def _prepare_batches(
    tmp_dir: Path,
    prefix: str,
    items: list[dict],
    batch_size: int,
    chunk_size: int,
    row_builder: Callable[[dict], tuple[Any, ...]],
    workers: int,
) -> Iterable[Path]:
    pending: list[Future[Path]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for batch_index, batch in enumerate(_iter_batches(items, batch_size)):
            future = executor.submit(
                _write_csv_file,
                tmp_dir,
                prefix,
                batch_index,
                (row_builder(item) for item in batch),
                chunk_size,
            )
            pending.append(future)
            if len(pending) >= workers:
                yield pending.pop(0).result()
        for future in pending:
            yield future.result()


def _copy_batch_files(
    conn: Any,
    table_name: str,
    csv_paths: Iterable[Path],
    progress: Any | None = None,
) -> None:
    for csv_path in csv_paths:
        conn.execute(f"""COPY {table_name} FROM "{csv_path}" (PARALLEL=FALSE, QUOTE='"', ESCAPE='"')""")
        csv_path.unlink(missing_ok=True)
        if progress is not None:
            progress.update()


def _create_node_table(conn: Any, table_name: str) -> None:
    conn.execute(
        (
            f"CREATE NODE TABLE {table_name}("
            "id STRING PRIMARY KEY, "
            "type STRING, "
            "name STRING, "
            "node_type STRING, "
            "path STRING, "
            "line_start INT64, "
            "col_start INT64, "
            "line_end INT64, "
            "col_end INT64, "
            "signature STRING, "
            "docstring STRING, "
            "tags STRING, "
            "payload STRING)"
        )
    )


def _create_rel_table(conn: Any, table_name: str, source_table: str, target_table: str) -> None:
    conn.execute(
        (
            f"CREATE REL TABLE {table_name}("
            f"FROM {source_table} TO {target_table}, "
            "relation STRING, "
            "confidence STRING, "
            "resolved_by STRING)"
        )
    )


def _group_nodes_by_table(nodes: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for node in nodes:
        groups[_node_table_name(node["type"])].append(node)
    return dict(groups)


def _group_edges_by_table(
    edges: list[dict],
    node_type_by_id: dict[str, str],
) -> dict[str, tuple[str, str, list[dict]]]:
    groups: dict[str, tuple[str, str, list[dict]]] = {}
    for edge in edges:
        source_type = node_type_by_id[edge["source"]]
        target_type = node_type_by_id[edge["target"]]
        source_table = _node_table_name(source_type)
        target_table = _node_table_name(target_type)
        table_name = _edge_table_name(edge["relation"], source_type, target_type)
        if table_name not in groups:
            groups[table_name] = (source_table, target_table, [])
        groups[table_name][2].append(edge)
    return groups


def write_ladybug_graph(
    nodes: list[dict],
    edges: list[dict],
    *,
    config: LadybugWriteConfig,
) -> ExportArtifacts:
    """Write Jiuwen graph data to a Ladybug database file."""
    ladybug = _import_real_ladybug()
    db_path = config.path.expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if config.overwrite and db_path.exists():
        db_path.unlink()

    reset_connection_cache()
    db = ladybug.Database(str(db_path))
    conn = ladybug.Connection(db)
    try:
        node_type_by_id = {node["id"]: node["type"] for node in nodes}
        node_groups = _group_nodes_by_table(nodes)
        edge_groups = _group_edges_by_table(edges, node_type_by_id)
        total_steps = len(node_groups) + len(edge_groups)
        total_steps += sum(
            _batch_count(len(table_nodes), config.node_batch_size) for table_nodes in node_groups.values()
        )
        total_steps += sum(
            _batch_count(len(table_edges), config.edge_batch_size) for _, _, table_edges in edge_groups.values()
        )

        with tqdm(total=total_steps, desc="Persisting", unit="step") as progress:
            for table_name in sorted(node_groups):
                _create_node_table(conn, table_name)
                progress.update()
            for table_name, (source_table, target_table, _) in sorted(edge_groups.items()):
                _create_rel_table(conn, table_name, source_table, target_table)
                progress.update()

            with tempfile.TemporaryDirectory(prefix="jiuwen-lbug-", dir=str(db_path.parent)) as tmp_dir_name:
                tmp_dir = Path(tmp_dir_name)
                for table_name, table_nodes in sorted(node_groups.items()):
                    node_files = _prepare_batches(
                        tmp_dir,
                        table_name,
                        table_nodes,
                        config.node_batch_size,
                        config.csv_chunk_size,
                        _node_row,
                        config.export_workers,
                    )
                    _copy_batch_files(conn, table_name, node_files, progress)
                for table_name, (_, _, table_edges) in sorted(edge_groups.items()):
                    edge_files = _prepare_batches(
                        tmp_dir,
                        table_name,
                        table_edges,
                        config.edge_batch_size,
                        config.csv_chunk_size,
                        _edge_row,
                        config.export_workers,
                    )
                    _copy_batch_files(conn, table_name, edge_files, progress)
    finally:
        close_conn: Callable | None = getattr(conn, "close", None)
        if callable(close_conn):
            close_conn()
        close_db: Callable | None = getattr(db, "close", None)
        if callable(close_db):
            close_db()
        reset_connection_cache()

    return ExportArtifacts(ladybug_path=db_path)
