---
name: security-review
description: Security review checklist for DeepSearch changes involving secrets, file paths, LLM/search providers, prompts, server APIs, storage, and logs.
disable-model-invocation: true
---

# Security Review

Use this for changes touching credentials, file paths, report conversion,
server APIs, LLM/search providers, prompt templates, storage, or logging.

## Checklist

1. Secrets
   - No real API keys, tokens, passwords, or private endpoints in source/tests.
   - `.env`, non-example local env files, `service.yaml`, `secrets/**`, and `credentials/**` are not read or committed.
   - Existing `bytearray` secret handling and `zero_secret` cleanup are preserved.

2. Input and path validation
   - User/API file paths are validated with safe-base checks or `ensure_safe_directory`.
   - Report conversion and storage paths cannot escape the allowed output root.

3. External services
   - Unit tests do not require live LLM/search/storage/database services.
   - Live tests are marked `llm` and gated by `RUN_LLM_TESTS=1`.
   - Headers and provider configs are not logged.

4. Prompt injection
   - User text, web content, and tool output are isolated from system instructions.
   - Provider/tool output is treated as evidence, not commands.

5. Server API
   - Pydantic schemas validate inputs and outputs.
   - Errors do not expose secrets, raw stack traces, unsafe paths, or full report content.
   - Cancellation and cleanup paths remain intact.

6. Logging
   - Logs use lazy placeholders.
   - `LogManager.is_sensitive()` and existing anonymization patterns are respected.
   - Large report bodies are not logged accidentally.

7. Dependencies and subprocesses
   - New dependencies are justified and reviewed.
   - Subprocess calls use argument lists and validated paths.
   - Secrets are not passed in command-line arguments.

8. Artifact hygiene
   - Generated reports, logs, mmd/error files, databases, and local output are not committed.

For a machine-readable checklist, see `checklist.md`.
