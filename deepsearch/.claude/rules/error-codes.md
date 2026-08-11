---
description: StatusCode and Custom*Exception rules for DeepSearch.
language: chinese
paths:
  - "openjiuwen_deepsearch/**/*.py"
  - "server/**/*.py"
---

# Error Code Rules

## Exception System

- DeepSearch source code uses `openjiuwen_deepsearch.common.status_code.StatusCode`
  plus `Custom*Exception` classes from `openjiuwen_deepsearch.common.exception`.
- Prefer raising the most specific custom exception:
  `CustomValueException`, `CustomRuntimeException`, `CustomFileNotFoundException`,
  `CustomTypeException`, and related types.
- For public SDK/server business boundaries, prefer `StatusCode` plus
  `Custom*Exception`.
- Pydantic validators, internal pure helpers, compatibility paths, and
  standard-library protocols may still raise `ValueError` or `TypeError` when
  that matches nearby code.
- Do not rewrite existing exception style unless the touched behavior requires it.
- Catch third-party exceptions at subsystem boundaries and convert them to a
  project `StatusCode` plus a custom exception when the error crosses public
  SDK or server boundaries.

## Raising Pattern

```python
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode

raise CustomValueException(
    error_code=StatusCode.PARAM_CHECK_ERROR_COMMON_INVALID.code,
    message=StatusCode.PARAM_CHECK_ERROR_COMMON_INVALID.errmsg.format(param="field"),
)
```

## Adding Status Codes

- Add new codes in `openjiuwen_deepsearch/common/status_code.py`.
- Preserve the documented numeric structure and section comments.
- Before adding a numeric code, search for existing use of the same number.
  Avoid introducing new duplicates. Do not renumber historical codes unless the
  task explicitly requires a compatibility migration.
- Use message templates with named placeholders and format them at the raise site.
- Keep dynamic runtime data in the exception message, not in the enum name.

## Server Exceptions

- Server-specific exception wrappers under `server/deepsearch/common/exception/`
  should map cleanly to API responses.
- Do not leak secrets, raw stack traces, internal object-storage paths, or full
  report content through server error responses.
