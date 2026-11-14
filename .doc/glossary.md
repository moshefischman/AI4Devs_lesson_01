# Glossary

## Purpose
- Define canonical domain terms and approved short forms used across code, API routes, docs, and plans.

## Core Terms
- `review` — the result of analysing one set of sources with Claude: a list of findings,
  a summary, token usage, and any skipped sources. Code: `reviewer.model.Review`.
- `finding` — one issue Claude reports: file, line range, severity, category, title,
  explanation, optional suggestion. Code: `reviewer.model.Finding`. Do not call it
  "issue", "comment", or "problem".
- `severity` — how urgent a finding is: `critical` > `high` > `medium` > `low` > `info`.
- `category` — what kind of finding it is: `bug`, `security`, `performance`,
  `maintainability`, `style`, `test`.
- `source` — one input unit sent for review: a file, or the hunks of a file taken from a
  diff, with original line numbers preserved. Code: `reviewer.source.Source`.
- `chunk` — a group of sources (or slices of one large source) that fits in a single
  Claude request. Code: `reviewer.chunk.Chunk`.
- `skipped source` — a source that was not reviewed (refusal, invalid response) and is
  reported as such instead of being silently dropped.

## Naming Alignment
- Keep this glossary aligned with naming decisions in `../.claude/rules/naming.md`.
- If a new domain term is introduced, add it here before broad usage.
- Avoid synonyms for existing terms unless explicitly approved and documented here.
