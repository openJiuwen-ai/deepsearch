"""Structural nodes representing files and folders."""

from dataclasses import dataclass

from .core import BaseNode


@dataclass(frozen=True, slots=True)
class FileNode(BaseNode):
    """A parsed source file.  *children* holds top-level code nodes."""

    path: str = ""
    language: str = ""


@dataclass(frozen=True, slots=True)
class FolderNode(BaseNode):
    """A directory.  *children* holds ``FileNode`` instances."""

    path: str = ""
