from typing import Callable, Awaitable, Any
from pydantic import BaseModel, ConfigDict, Field

class ToolOutcome(BaseModel):
    """Result of a single coder tool execution."""
    message: str = ""
    patch_submitted: bool = False
    error: str = ""

ToolExecutor = Callable[[Any, dict], Awaitable[ToolOutcome]]

class ToolSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    name: str
    schema_: dict = Field(alias="schema")
    executor: ToolExecutor

def build_default_registry() -> dict[str, ToolSpec]:
    from openjiuwen_codesearch.algorithm.coder_tools import (
        edit_file,
        view_file,
        run_bash,
        retrieve_context,
        submit_patch,
    )

    specs = [
        view_file.SPEC,
        edit_file.SPEC,
        run_bash.SPEC,
        retrieve_context.SPEC,
        submit_patch.SPEC,
    ]
    return {spec.name: spec for spec in specs}

def registry_schemas(registry: dict[str, ToolSpec]) -> list[dict]:
    return [spec.schema_ for spec in registry.values()]
