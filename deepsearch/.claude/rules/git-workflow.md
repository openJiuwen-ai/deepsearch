---
description: Git commit style, branch naming, PR checks, and artifact hygiene for DeepSearch.
language: chinese
paths: []
alwaysApply: true
---

# Git Workflow Rules

## Commit Messages

- Prefer conventional commits: `type(scope): description`.
- Common types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `chore`.
- Use imperative mood and keep the subject concise.

## Before Committing

- Review the exact diff: `git diff --stat` and `git diff`.
- Run targeted tests for changed areas, for example:
  `uv run pytest tests/path/to/test_file.py`.
- For broad Python changes, run `uv run pytest -m "not llm"`.
- Do not commit `.env`, service config, logs, generated reports, `.docx`,
  `.html`, `.mmd`, `.error.txt`, local databases, or large runtime artifacts.

## Staging

- Stage files intentionally; do not use `git add .` blindly.
- When this AI-guidance package is the only intended change, inspect:
  `git diff -- AGENTS.md CLAUDE.md .claude`.

## Large Changes

- Keep PRs focused on one logical change.
- If a change affects public SDK behavior or server API behavior, update tests
  and user-facing docs in the same change.
