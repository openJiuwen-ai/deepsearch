---
name: verification-loop
description: Verify DeepSearch changes with scoped diff review, import checks, pytest, optional live tests, and artifact hygiene.
disable-model-invocation: true
---

# Verification Loop

Use this after implementing a feature, fixing a bug, or preparing a PR.

## Phase 1: Scope Review

```bash
git diff --stat
git diff --name-only
```

Confirm the changed files match the task. Generated reports, logs, `.env`,
databases, and runtime artifacts should not appear unless explicitly requested.

## Phase 2: Import Check

For Python source changes:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run python -m compileall -q openjiuwen_deepsearch server
```

Skip this for docs-only changes and state that it was skipped.

## Phase 3: Targeted Tests

Run tests closest to the changed area:

```bash
uv run pytest tests/path/to/test_file.py
```

Examples:
- Query understanding -> `tests/algorithm/query_understanding/`
- Report generation -> `tests/report/`
- Server manager/API -> `tests/server/`
- User feedback -> `tests/user_feedback_processor/`

## Phase 4: Broad Non-Live Tests

For changes spanning multiple areas:

```bash
uv run pytest -m "not llm"
```

## Phase 5: Live Tests

Only run live LLM/search tests when the user requested it and credentials are
configured:

```bash
RUN_LLM_TESTS=1 uv run pytest -m llm
```

## Phase 6: Final Diff Review

```bash
git diff -- AGENTS.md CLAUDE.md .claude
git diff
```

For non-AI-guidance tasks, review all changed files. Check that public behavior
has tests and docs when needed.

## Report Format

```text
Verification Report
Scope:     PASS
Import:    PASS / SKIPPED
Targeted:  PASS / SKIPPED
Broad:     PASS / SKIPPED
Live:      PASS / SKIPPED
Diff:      PASS
Status:    READY
```
