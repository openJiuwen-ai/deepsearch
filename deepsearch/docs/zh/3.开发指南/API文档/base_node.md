# openjiuwen_deepsearch.framework.openjiuwen.agent.base_node

## class openjiuwen_deepsearch.framework.openjiuwen.agent.base_node.BaseNode
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.base_node.BaseNode()
```
**BaseNode** 是节点封装类，继承自 `WorkflowComponent` 与 `ComponentExecutable`。它提供统一的节点接口，但不会自动编排 `_pre_handle` / `_post_handle`。

**约定方法**：
- `_pre_handle`：从 `Runtime` / `Context` 中取数（默认抛异常）。
- `_do_invoke`：核心逻辑（默认抛异常）。
- `_post_handle`：写回运行时状态（默认抛异常）。

`invoke` 仅负责调用 `_do_invoke`，并由 `async_time_logger` 记录耗时。

### __init__
```python
__init__()
```
初始化 BaseNode。

### invoke
```python
async invoke(inputs: Input, runtime: Runtime, context: Context) -> Output
```
执行节点逻辑，内部仅调用 `_do_invoke`。

**参数**：
- **inputs**(Input)：节点输入（openjiuwen 源码路径：`openjiuwen/core/graph/executable.py`）。
- **runtime**(Runtime)：运行时上下文（openjiuwen 源码路径：`openjiuwen/core/runtime/runtime.py`）。
- **context**(Context)：上下文对象（openjiuwen 源码路径：`openjiuwen/core/context_engine/base.py`）。

**返回**：
- **Output**：节点输出。

**样例**：

```python
>>> from openjiuwen_deepsearch.framework.openjiuwen.agent.base_node import BaseNode
>>> from openjiuwen.core.graph.executable import Input, Output
>>> from openjiuwen.core.runtime.runtime import Runtime
>>> from openjiuwen.core.context_engine.base import Context

>>> class CustomNode(BaseNode):
...     async def _do_invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
...         # 可自行调用 _pre_handle / _post_handle
...         return Output()
```

### _pre_handle
```python
_pre_handle(inputs: Input, runtime: Runtime, context: Context)
```
默认抛出 `CustomJiuWenBaseException`。子类如需使用应自行实现并在 `_do_invoke` 中调用。

### _do_invoke
```python
async _do_invoke(inputs: Input, runtime: Runtime, context: Context) -> Output
```
核心节点逻辑函数，子类必须实现。

### _post_handle
```python
_post_handle(inputs: Input, algorithm_output: object, runtime: Runtime, context: Context)
```
默认抛出 `CustomJiuWenBaseException`。子类如需使用应自行实现并在 `_do_invoke` 中调用。
