# `openjiuwen_deepsearch.framework.openjiuwen.agent.base_node`

## `BaseNode`
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.base_node.BaseNode()
```
**BaseNode** wraps a workflow node (`WorkflowComponent` + `ComponentExecutable`). It defines a uniform surface but does **not** auto-chain `_pre_handle` / `_post_handle`.

**Hooks**:
- `_pre_handle` — read from `Runtime` / `Context` (default: raises).
- `_do_invoke` — core logic (default: raises).
- `_post_handle` — write runtime state (default: raises).

`invoke` calls `_do_invoke` and wraps it with `async_time_logger` timing.

### `__init__`
```python
__init__()
```
Constructs `BaseNode`.

### `invoke`
```python
async invoke(inputs: Input, runtime: Runtime, context: Context) -> Output
```
Runs `_do_invoke` only.

**Parameters**:
- **inputs** (`Input`) — node input (openjiuwen: `openjiuwen/core/graph/executable.py`).
- **runtime** (`Runtime`) — runtime (openjiuwen: `openjiuwen/core/runtime/runtime.py`).
- **context** (`Context`) — context (openjiuwen: `openjiuwen/core/context_engine/base.py`).

**Returns**:
- **Output** — node output.

**Example**:
```python
>>> from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode
>>> from openjiuwen.core.graph.executable import Input, Output
>>> from openjiuwen.core.runtime.runtime import Runtime
>>> from openjiuwen.core.context_engine.base import Context

>>> class CustomNode(BaseNode):
...     async def _do_invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
...         # call _pre_handle / _post_handle yourself if needed
...         return Output()
```

### `_pre_handle`
```python
_pre_handle(inputs: Input, runtime: Runtime, context: Context)
```
Default raises `CustomJiuWenBaseException`. Override and call from `_do_invoke` if needed.

### `_do_invoke`
```python
async _do_invoke(inputs: Input, runtime: Runtime, context: Context) -> Output
```
Must be implemented by subclasses.

### `_post_handle`
```python
_post_handle(inputs: Input, algorithm_output: object, runtime: Runtime, context: Context)
```
Default raises `CustomJiuWenBaseException`. Override and call from `_do_invoke` if needed.
