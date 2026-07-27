# Security Review Checklist

| ID | Check |
|----|-------|
| SEC-1.1 | No real API keys, tokens, passwords, or private endpoints in source/tests/docs |
| SEC-1.2 | `.env`, non-example local env files, `service.yaml`, `secrets/**`, and `credentials/**` are not read or committed |
| SEC-1.3 | Existing `bytearray` secret handling is preserved |
| SEC-1.4 | Mutable secrets are cleared with `zero_secret` where existing code requires it |
| SEC-2.1 | User/API file paths are validated before read/write/delete |
| SEC-2.2 | Writable directories stay inside approved output roots |
| SEC-2.3 | Report conversion and storage paths reject traversal |
| SEC-3.1 | Unit tests do not require live external services |
| SEC-3.2 | Live LLM/search tests are marked `llm` and gated by `RUN_LLM_TESTS=1` |
| SEC-3.3 | Provider headers and configs are not logged |
| SEC-4.1 | User text is separated from system prompt instructions |
| SEC-4.2 | Web/tool output is treated as untrusted evidence |
| SEC-5.1 | Pydantic schemas validate server API boundaries |
| SEC-5.2 | API errors do not expose secrets, raw stack traces, or unsafe paths |
| SEC-5.3 | Long-running task cancellation preserves cleanup |
| SEC-6.1 | Logs use lazy placeholders |
| SEC-6.2 | Sensitive mode and anonymization patterns are respected |
| SEC-6.3 | Large report bodies are not logged accidentally |
| SEC-7.1 | New dependencies are justified and reviewed |
| SEC-7.2 | Subprocess calls use argument lists and validated paths |
| SEC-7.3 | Secrets are not passed through command-line arguments |
| SEC-8.1 | Generated reports, logs, databases, and local output are not committed |
