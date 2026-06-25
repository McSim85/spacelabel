# Contributing to spacelabel

Thanks for your interest! This is a small, focused macOS tool. Please read
[`DESIGN.md`](DESIGN.md) and [`DECISIONS.md`](DECISIONS.md) first — they capture
the locked architecture and the rationale (with confidence levels) behind every
choice, so a change that contradicts them needs a deliberate decision update.

## Development setup

Requires macOS, [`uv`](https://docs.astral.sh/uv/), and Python 3.11+ (the
reference interpreter is Homebrew `python@3.14`).

```sh
git clone https://github.com/McSim85/spacelabel
cd spacelabel
uv venv
uv pip install -e '.[dev]'
pre-commit install            # run the lint/type gates on every commit
```

Run the tool from the dev environment:

```sh
uv run spacelabel --help
uv run spacelabel agent --debug
```

> `uv` is the dev environment. Distribution is the Homebrew cask — see `DESIGN.md` §9.

## Quality gates (must pass before a PR)

These are enforced by both `pre-commit` and CI:

```sh
uv run ruff check .            # lint
uv run ruff format --check .   # format
uv run mypy src                # type-check
uv run pytest                  # tests
```

`ruff format` is the autoformatter — run `uv run ruff format .` to fix style.

## Engineering standards

- **PEP 8** style, **PEP 257** docstrings, **PEP 484** type hints — enforced by
  ruff + mypy (`strict`).
- **No silent exception handling.** Never `except: pass` / `continue`. Catch
  specific exceptions, log with context (`logging.getLogger(__name__)`), then
  recover or re-raise. See `DESIGN.md` §8.2.
- **Prefer the standard library.** Add a third-party dependency only when it
  clearly earns its keep. The current set is PyObjC (unavoidable) + `click`.
- Library/module code never configures logging — only `setup_logging()` does.

## Commits & pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:` …).
- Keep PRs focused; describe the change and reference the relevant `DESIGN.md` /
  `DECISIONS.md` section. If a change revises a decision, update `DECISIONS.md` in
  the same PR.
- Fill in the pull-request template.

## Reporting issues

Use the bug-report or feature-request templates. For bugs, include your macOS
version, whether "Displays have separate Spaces" is on/off, your display setup,
and any output from `spacelabel spaces --debug`.
