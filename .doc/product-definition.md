# Product Definition

## Purpose
AI Code Reviewer: a Python library and CLI that sends source code to Claude and returns
a structured, actionable review.

## Product Vision
Every change gets a fast, consistent first-pass review that points at exact lines and
proposes a fix, so human reviewers can focus on design and intent.

## Problem Statement
Manual review is slow and uneven; generic LLM chat output is free text that cannot be
filtered, gated in CI, or trusted to cite real line numbers.

## Value Proposition
Schema-validated findings with verified line numbers, severity gating via exit codes,
and output in text, JSON, or Markdown.

## Product Scope
- In scope: review of files, directories, and unified diffs (plan 001); unit test
  generation and quality report (plan 002).
- Out of scope: web UI, PR/CI integrations, auto-applying fixes, persistence beyond files.

## Target Users
- Primary users: developers reviewing their own changes before opening a PR.
- Secondary users: tech leads gating merges with `--fail-on`.


## Acceptance Criteria
Each criterion must be provable by a test.
- AC01 — Quality gates: `ruff check .` is clean and `pytest` (unit suite, `not live`)
  passes. (The original `npx tsc --noEmit` gate applies only once a TypeScript
  `frontend/` exists.)
- AC02 — `Finding` rejects `line_start < 1`, `line_end < line_start`, and absolute or
  parent-escaping file paths.
- AC03 — Source collection skips `venv/`, `.git/`, binary files, and every env/secret
  file; a unified diff yields the correct new-file line numbers.
- AC04 — No chunk exceeds its token budget and every input line is sent at least once
  (no silent truncation).
- AC05 — Findings from all chunks are merged, de-duplicated, and sorted by severity;
  refusals become skipped sources and `max_tokens` truncation triggers a split retry.
- AC06 — CLI exit codes: `0` no finding at or above `--fail-on`, `1` such findings
  exist, `2` usage/configuration error, `3` API error; `--format json` validates against
  the `Review` schema.
- AC07 — The API key never appears in logs or output, including with `--verbose`.
