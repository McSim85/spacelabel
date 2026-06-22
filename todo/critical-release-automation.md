# spacelabel — Critical: Release Automation Pipeline

**Recommended model:** Sonnet 4.6 · **effort:** medium. Set `/model` and `/effort`
before running.
**Run in a fresh session.**

> **Status (2026-06-22):** release-please + Renovate are **shipped** and releasing (currently v0.6.1); the repo is now **public**. Two things are **deferred — pipx-only for now** (Max's call: fewer manual/maintenance steps until the product is stable):
> - **PyPI publish (§3):** keep the workflow staged but **do not flip it on** until the product is stable. Teammates install via `pipx install git+https://github.com/McSim85/spacelabel`.
> - **Homebrew tap (§4): DEFERRED to post-PyPI / v1.0.** When revived, change the plan below: use an **in-repo self-tap** (`Formula/spacelabel.rb` in *this* repo, mirroring quiknode-labs `optic`/`ssh-mcp`) rather than a separate `McSim85/homebrew-spacelabel` repo; ship a Python `virtualenv_install_with_resources` formula (not a frozen binary); and **land the `_resolve_install_shim` brew-path fix FIRST** (prerequisite — `spacelabel install` must resolve the real brew bin path, not the hardcoded `~/.local/bin/spacelabel` pipx shim, or the agent can't be installed under a brew install). Cross-refs: phase-6 §1C (deferred), `todo/uninstall-purge.md`.

---

## Shared Baseline

- **Project:** `spacelabel` — open-source (MIT) macOS menu-bar + CLI tool that labels
  Spaces, keyed by Space UUID. One package (`src/spacelabel/`), one `click` entry
  point, pipx distribution.
- **Stack:** Python; PyObjC; `click`. No SIP disable. CI is macOS-only (`macos-latest`
  in GitHub Actions, because PyObjC wheels are macOS-only). Tooling: ruff + mypy
  `--strict` + pytest + pre-commit.
- **Commit standard:** Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, etc.)
  — Max's standing standard; every commit in this repo already follows it.
- **Repo:** private under github.com/McSim85/spacelabel; MIT © Max Kramarenko.
  Will go public before v1.0.
- **Version source of truth:** `version` field in `pyproject.toml` (PEP 517/518).
  Currently `0.1.0-dev` or similar — check before starting.
- **Hand-off rule:** read `DESIGN.md` + `DECISIONS.md` (incl. §8 repo layout, §6.5
  single-instance) before acting; update `DECISIONS.md` at the end with any new
  decisions.

---

## Background

Max needs a release-please-style automated release pipeline so every merge to
`main` that contains a Conventional Commit bump is handled automatically. The
goals, in delivery order:

1. **Changelog + version PR** — a bot-maintained PR that accumulates changes in
   `CHANGELOG.md` and bumps `version` in `pyproject.toml`.
2. **Tagged GitHub Release** — on PR merge, tag `v<version>` and cut a GitHub
   Release with the changelog section.
3. **PyPI publish** — push the wheel + sdist to PyPI via **OIDC trusted publishing**
   (no stored API tokens; the GHA job gets a short-lived OIDC token).
4. **Homebrew tap** — after PyPI publish, update the formula in a tap repo
   (e.g. `McSim85/homebrew-spacelabel`) so `brew install mcsim85/spacelabel/spacelabel`
   works.
5. **Renovate** — auto-PR dep updates for `pyproject.toml` deps (PyObjC + click),
   GitHub Actions versions, and pre-commit hook revs; weekly batched schedule to
   limit CI minutes churn.

**Timing note:** the repo is currently private. Release-please and Renovate work
fine on private repos. PyPI publish will not be wired to a real PyPI project until
the repo goes public and the PyPI project is created — but the workflow should be
ready to flip on. Document the go-public checklist in `.github/SETTINGS.md`
(a file already mentioned in DECISIONS §8.5 as the go-public checklist location).

---

## Your task this session

### 1. Read first
- `DECISIONS.md` §8 (repo layout + notes, incl. the go-public checklist note in §8.5)
- `pyproject.toml` — current `[project]` `version`, `[project.urls]`, any existing
  GHA workflows under `.github/workflows/`
- `.github/SETTINGS.md` if it exists — check what's already documented

### 2. Release-please workflow

Create `.github/workflows/release-please.yml`:
- Trigger: `push` to `main`
- Use `googleapis/release-please-action` (pin to a specific SHA, not a mutable tag)
- `release-type: python` (updates `pyproject.toml` version)
- `package-name: spacelabel`
- Generates / maintains `CHANGELOG.md` from Conventional Commits
- On the release PR merge, the action creates the git tag and GitHub Release

If release-please's `python` release type does not natively update
`pyproject.toml`'s `version =` field, configure a
`extra-files` / `version-file` override so it does. Verify against the current
`pyproject.toml` structure before wiring.

### 3. PyPI publish workflow

Create `.github/workflows/publish.yml`:
- Trigger: `release` event (`published`)
- Build: `uv build` (or `python -m build`) to produce the wheel + sdist
- Publish: `pypa/gh-action-pypi-publish` with **OIDC trusted publishing** (no
  `password:` / `token:` secrets — the workflow uses `id-token: write` permission)
- The PyPI project must be pre-registered under the name `spacelabel` before this
  fires in production; document the setup step in `.github/SETTINGS.md`
- macOS runner is **not** required for the build/publish job (the pure wheel and
  sdist are architecture-independent from pip's perspective; the macOS-specific
  PyObjC wheels are already on PyPI and are resolved at install time, not build time)

### 4. Homebrew tap — DEFERRED (post-PyPI / v1.0; see Status callout above)

Create (or document) a tap repo update step:
- After a successful PyPI publish, bump the `url:` + `sha256:` in the Homebrew
  formula
- Options (choose the simplest that works):
  a. A `workflow_dispatch`-triggered job that fetches the PyPI release, computes
     the sha256, and opens a PR on `McSim85/homebrew-spacelabel`
  b. A step in `publish.yml` that does the same after the PyPI upload succeeds
- The tap formula itself lives in a separate repo (`McSim85/homebrew-spacelabel`);
  create a stub `Formula/spacelabel.rb` there if it doesn't exist yet, or document
  how to create it
- Document the tap in `README.md` under installation

### 5. Renovate

Create `renovate.json` at the repo root:
```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["config:recommended"],
  "schedule": ["every weekend"],
  "groupName": "all deps",
  "packageRules": [
    { "matchManagers": ["pip_requirements", "pep621"], "groupName": "python deps" },
    { "matchManagers": ["github-actions"], "groupName": "GitHub Actions" },
    { "matchManagers": ["pre-commit"], "groupName": "pre-commit hooks" }
  ],
  "labels": ["dependencies"],
  "automerge": false
}
```
Adjust as needed. Document that Max must install the Renovate GitHub App on the
private repo (Settings → GitHub Apps → Renovate). Add a note to `.github/SETTINGS.md`.

### 6. `.github/SETTINGS.md` update

Ensure the go-public checklist includes:
- [ ] Flip repo to public
- [ ] Register `spacelabel` on PyPI and configure OIDC trusted publishing
      (publisher: `McSim85/spacelabel`, workflow: `publish.yml`, env: `pypi`)
- [ ] Create `McSim85/homebrew-spacelabel` tap repo + formula
- [ ] Install Renovate GitHub App on the repo
- [ ] Enable branch protection on `main` (required status checks: CI)
- [ ] Enable GitHub Discussions

### 7. `DECISIONS.md` update

Add a decision entry (or extend an existing §8 row) documenting:
- The release tool chosen (release-please vs alternatives considered, e.g. semantic-release)
- The PyPI publishing method (OIDC trusted publishing)
- Renovate schedule / grouping rationale (weekly batched to limit macOS CI minutes)

---

## Deliverables

1. `.github/workflows/release-please.yml`
2. `.github/workflows/publish.yml`
3. `renovate.json`
4. Updated `.github/SETTINGS.md` (go-public checklist)
5. Stub Homebrew tap step (or documented plan if cross-repo creation is out of scope)
6. `DECISIONS.md` update in §8
7. All existing CI gates still green: `uv run ruff check .` / `mypy src` / `pytest`
   (the new YAML files don't affect Python linting, but pre-commit + ruff format must pass)

## Before committing

Run **codex review** in a loop until no critical findings remain:

```sh
git add <changed files>
codex review "<focused prompt: list changed files; flag only crash risks, logic errors,
  missing error handling, security issues in the CI/CD config; skip style/naming>"
# fix findings → re-run gates → re-stage → repeat
```

Note: `--uncommitted` conflicts with a positional prompt; stage first.
