# System Architecture

## Purpose
- Provide a concise architecture reference for service boundaries, ownership, and major flows.

## System Overview
A single Python package, `reviewer/`, used as a library or through its CLI
(`python -m reviewer`). It runs locally; the only remote dependency is the Claude API.

## Primary Components
| Module | Responsibility |
|---|---|
| `reviewer/cli.py` | Argument parsing, exit codes, wiring |
| `reviewer/source.py` | Collect files, directories, and diffs; skip ignored, binary, and secret files |
| `reviewer/chunk.py` | Pack sources into token-bounded chunks; split oversized sources with overlap |
| `reviewer/claude_client.py` | The only module that imports the Anthropic SDK; structured output, errors |
| `reviewer/review_service.py` | Orchestrates chunk → Claude → validate → merge → sort |
| `reviewer/render.py` | Text, JSON, and Markdown output; file writing |
| `reviewer/model.py` | Pydantic models: `Review`, `Finding`, `Usage`, `SkippedSource` |
| `reviewer/prompt/` | Versioned system prompts |
| `reviewer/test_target.py` | `ast` analysis of a module: public callables, external deps, existing tests |
| `reviewer/test_gen_service.py` | Generate → run → repair loop, xfail/discard policy, order-stability check |
| `reviewer/test_runner.py` | Sandboxed pytest subprocess: junit outcomes and coverage JSON |
| `reviewer/metric.py` | Lint (ruff) and cyclomatic complexity (radon) per Python file |
| `reviewer/report_service.py` | Quality report orchestration, score, verdict |

## Data Flow
1. CLI resolves the target into `Source` objects (line numbers preserved).
2. Limits are checked (file count, then `messages.count_tokens` on the whole payload).
3. Sources are packed into chunks; each chunk is one streaming request with
   `output_config.format` (JSON schema), adaptive thinking, and configurable effort.
4. Each response is validated; findings that cite lines not present in the chunk are
   dropped and counted.
5. Findings are de-duplicated, filtered by `--min-severity`, sorted, and rendered.

### Test generation (`test-gen`)
1. `test_target.analyze` parses the module; existing `test_<module>.py` files give the
   baseline coverage and are shown to Claude to avoid duplicates.
2. Claude returns a complete pytest file plus declared cases and suspected bugs
   (`output_config.format`).
3. The runner executes it in a temporary directory with a 60 s timeout, a scrubbed
   environment (no credential variables), and sockets disabled; outcomes come from
   junit XML, coverage from `coverage.json`.
4. Unexplained failures go back to Claude, up to 3 iterations. Remaining failures:
   suspected bug → `@pytest.mark.xfail(strict=True)` + `bug` finding; otherwise removed.
5. The suite is run in normal and reversed order; failing or order-dependent tests are
   removed until stable. Output goes to `.orchestrate/test-gen/<timestamp>/`.

### Quality report (`report`)
Review → existing tests with coverage → optional test-gen → lint + complexity →
weighted score (40/30/15/15, renormalized when a component is unmeasured) → verdict.
Output goes to `.orchestrate/quality-report/report-<timestamp>.{md,json}`.

## Auth
- Credentials resolve through the Anthropic SDK (`ANTHROPIC_API_KEY` from the
  environment or `.env`, or an `ant auth login` profile). The package never reads,
  prints, or logs the key value.

## External Dependencies
- Claude API (`claude-opus-5-5` by default, `REVIEWER_MODEL` / `--model` to override).
- Server-side refusal fallback (`fallbacks: "default"`, beta
  `server-side-fallback-2026-07-01`), disable with `--no-fallback`.

## Operational Concerns
- Retries for 429/5xx/connection errors are delegated to the SDK (`max_retries`).
- `stop_reason: "max_tokens"` → the chunk is split and retried; `refusal` → the chunk's
  files are reported as skipped sources.
- `--verbose` logs request ids and token usage, never credentials.
- Env/secret files are never collected as sources.

## Change Log
- 2026-11-14 — Initial architecture for the code review engine (plan 001).
- 2026-11-14 — Test generation and quality report (plan 002); `ClaudeClient.complete`
  generalizes structured output to any schema.
