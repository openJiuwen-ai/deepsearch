---
description: Python-specific security rules for DeepSearch credentials, files, dependencies, and subprocesses.
language: chinese
paths:
  - "openjiuwen_deepsearch/**/*.py"
  - "server/**/*.py"
  - "tests/**/*.py"
alwaysApply: false
---

# Python Security

Extends `.claude/rules/security.md`.

## Secret Handling

- Do not store real secrets in source, tests, docs examples, or generated
  fixtures.
- Use `bytearray` secret fields where existing config models do so.
- Clear mutable secrets with `zero_secret` after handoff when following existing
  patterns.
- Never log API keys, tokens, Authorization headers, or full provider configs.

## Filesystem

- Use `Path.resolve()` plus safe-base checks or existing helpers such as
  `ensure_safe_directory`.
- Report conversion and storage code must not allow path traversal.
- Temporary files in tests should use `tmp_path`.

## Subprocess and Conversion Tools

- Prefer Python APIs over shell commands.
- If subprocess execution is necessary, pass argument lists rather than shell
  strings and validate all user-controlled paths.
- Do not pass secrets through command-line arguments.

## Dependency Review

- Before adding a dependency, review why the standard library or an existing
  dependency is insufficient.
- Network-facing, parsing, conversion, and crypto dependencies need extra
  scrutiny.
- If security tools such as `bandit` or `pip-audit` are available in the
  environment, run them for security-sensitive changes and report the result.
