@AGENTS.md

# Claude Code Notes

- Keep shared project rules in `AGENTS.md` so all coding agents use the same
  DeepSearch architecture guidance.
- Use this file only for Claude-specific imports or workflow notes.
- Detailed rules by topic live in `.claude/rules/`.
- Reusable operational guides live in `.claude/skills/`.
- Permissions, denied secret files, default language, and env vars live in
  `.claude/settings.json`.
- Run `/memory` to manage auto memory.
- Run `/context` to inspect the files loaded in the current Claude session.
