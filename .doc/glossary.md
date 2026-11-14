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
- `test target` — the Python module a test suite is generated for: its public functions,
  classes, and existing tests. Code: `reviewer.test_target.TestTarget`.
- `test case` — one generated test function with an `intent` (`happy`, `edge`,
  `failure`) and a final `status` (`passed`, `xfail`, `discarded`).
  Code: `reviewer.model.TestCase`.
- `test suite` — the generated test file for one test target, plus its cases, iteration
  count, and coverage before/after. Code: `reviewer.model.TestSuite`.
- `quality score` — 0–100 weighted score: review 40 %, coverage 30 %, passing tests 15 %,
  lint and complexity 15 %. Code: `reviewer.model.QualityScore`.
- `verdict` — `pass` (score ≥ 80), `warn` (60–79), or `fail` (< 60 or any `critical`
  finding).
- `quality report` — review + test results + coverage + static metrics + quality score
  + verdict for one target. Code: `reviewer.model.QualityReport`.

## Naming Alignment
- Keep this glossary aligned with naming decisions in `../.claude/rules/naming.md`.
- If a new domain term is introduced, add it here before broad usage.
- Avoid synonyms for existing terms unless explicitly approved and documented here.
