---
description: LLM coding behavior guidelines: clarify assumptions, keep changes simple, edit surgically, and verify goals.
language: chinese
paths: []
alwaysApply: true
---

# Coding Principles

These principles complement the technical rules in `AGENTS.md` and
`.claude/rules/`. They govern how the agent thinks and acts.

## Think Before Coding

- Do not assume unclear behavior. Inspect code, docs, tests, and configuration.
- State assumptions when they matter.
- Ask before making a risky product or API choice that local context cannot
  resolve.

## Simplicity First

- Implement the smallest change that solves the requested problem.
- Avoid speculative options, abstractions, and configurability.
- Match existing project style, even when another style is personally tempting.

## Surgical Changes

- Touch only files required by the task.
- Do not refactor adjacent code unless the requested change requires it.
- Remove only unused code created by your own change.
- Mention unrelated issues instead of silently changing them.

## Goal-Driven Verification

- Turn tasks into verifiable outcomes.
- Add or update tests when behavior changes.
- Run targeted checks appropriate to the changed area.
- Report what was verified and what was intentionally skipped.
