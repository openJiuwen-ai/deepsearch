"""Common types shared by export backends."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ExportArtifacts:
    """Paths produced by one or more export backends."""

    nodes_path: Path | None = None
    edges_path: Path | None = None
    jcp_path: Path | None = None
    ladybug_path: Path | None = None


@dataclass(slots=True)
class LadybugWriteConfig:
    """Tuning knobs for Ladybug export writes."""

    path: Path
    node_batch_size: int = 1000
    edge_batch_size: int = 5000
    csv_chunk_size: int = 1000
    export_workers: int = 2
    overwrite: bool = True

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        for name in ("node_batch_size", "edge_batch_size", "csv_chunk_size", "export_workers"):
            value = getattr(self, name)
            if value < 1:
                raise ValueError(f"{name} must be >= 1")
