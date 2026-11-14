# Anthropic Python SDK - Part 1

Small examples for using the Anthropic Python SDK with Claude, including:

- message-based chat
- system prompts
- streaming responses

## Prerequisites

- Python 3.10+
- An Anthropic API key

## Setup

1. Install dependencies:

    ```bash
    pip install anthropic python-dotenv
    ```

2. Add your API key to `.env` in the project root:

    ```env
    ANTHROPIC_API_KEY=your-key-here
    ```

## Example Scripts

- **10-messages.py**  
   Basic multi-turn conversation using a `messages` list and helper functions.

- **20-system-prompt.py**  
   Adds a `system` prompt to shape model behavior (math tutor style).

- **30-stream.py**  
   Streams output token-by-token using `client.messages.stream(...)`.

Run any script from the project root:

```bash
python 10-messages.py
python 20-system-prompt.py
python 30-stream.py
```


## Notes

- Model name in the scripts is currently set to `claude-haiku-4-5`.

## AI Code Reviewer

The `reviewer/` package sends source code to Claude and returns a structured review:
findings with severity, category, `file:line`, explanation, and a suggested fix.
Plan: `.plan/001-2026-11-14-code-review-engine.md`.

### Install

```bash
pip install -r requirements-dev.txt
```

Credentials come from `ANTHROPIC_API_KEY` (environment or the root env file) or an
`ant auth login` profile. The reviewer never prints or logs the key.

### Usage

```bash
python -m reviewer review path/to/file.py
python -m reviewer review src/ --format json --out          # also writes .orchestrate/review/
python -m reviewer review src/ --include "*.py" --exclude "tests/*"
git diff main | python -m reviewer review - --diff
```

| Option | Default | Purpose |
|---|---|---|
| `--format text\|json\|markdown` | `text` | Output format |
| `--out [DIR]` | — (`.orchestrate/review` when given without a value) | Also write `review-<timestamp>.json` and `.md` |
| `--model` | `claude-opus-5-5` (or `REVIEWER_MODEL`) | Claude model |
| `--effort` | `high` | `low`, `medium`, `high`, `xhigh`, `max` |
| `--min-severity` | `info` | Hide findings below this severity |
| `--fail-on` | `high` | Exit `1` if any finding is at or above this severity |
| `--max-files` / `--max-tokens` | `200` / `400000` | Abort before calling the API if the input is larger |
| `--no-fallback` | off | Disable the server-side refusal fallback |
| `--verbose` | off | Log request ids and token usage |

Exit codes: `0` clean, `1` findings at or above `--fail-on`, `2` usage/configuration
error, `3` Claude API error.

Example text output:

```text
[HIGH] tests/fixtures/buggy_sample.py:8 (bug) Division by zero on empty input
    `average([])` raises ZeroDivisionError.
    > if not values:
    >     return 0.0

1 finding(s) across 1 source(s) (1 high). ...
```

### Tests

```bash
ruff check .
pytest --cov=reviewer --cov-fail-under=85     # unit suite, no network
pytest -m live                                 # real API smoke test — spends credit
```
