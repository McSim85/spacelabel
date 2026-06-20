# Suggested GitHub repository settings

These are recommendations (documentation, not enforced code) for when the repo is
configured on GitHub. Adjust to taste.

## Repository description

> Label macOS Spaces (virtual desktops) by their stable UUID — reorder-proof.
> Menu-bar + CLI, pipx-installable, no SIP disable.

## Topics

`macos`, `spaces`, `virtual-desktops`, `menu-bar`, `menubar`, `appkit`,
`pyobjc`, `window-management`, `cli`, `python`, `mission-control`

## General

- Default branch: `main`.
- Features: enable **Issues** and **Discussions** (the issue config links to
  Discussions for questions); disable Wiki/Projects unless needed.
- Pull requests: allow **squash merge** only; "Automatically delete head
  branches" on.

## Branch protection — `main`

- Require a pull request before merging (≥1 approval; dismiss stale approvals on
  new commits).
- Require status checks to pass before merging, and require branches to be up to
  date:
  - `lint-type-test` (the CI job in `.github/workflows/ci.yml`)
- Require conversation resolution before merging.
- Do not allow force pushes or deletions on `main`.
- (Optional) Require signed commits.

## Security

- Enable Dependabot alerts and security updates.
- Enable secret scanning and push protection.

## Releases

- Tags and GitHub Releases are managed automatically by release-please
  (`.github/workflows/release-please.yml`).  Merge the release-please PR to
  ship; the action creates the tag and release, which triggers `publish.yml`.
- `version =` in `pyproject.toml` is the single source of truth;
  `__init__.__version__` reads it at runtime via `importlib.metadata`.
- Built wheel + sdist are attached to each GitHub Release by `publish.yml`, so
  users can install with pipx directly from the release:
  ```sh
  pipx install https://github.com/McSim85/spacelabel/releases/download/vX.Y.Z/spacelabel-X.Y.Z-py3-none-any.whl
  # or from the source tag:
  pipx install git+https://github.com/McSim85/spacelabel.git@vX.Y.Z
  ```

---

## Go-public checklist

Complete these steps before or at the time the repo is made public.

### Repository visibility

- [ ] Flip repo to public (Settings → General → Danger Zone → Change visibility)

### Renovate

- [x] **Done** — Renovate app installed and onboarding PR merged (PR #5).
  Weekly batched dep-update PRs (python deps, GHA versions, pre-commit hook revs)
  are active.  GHA `uses:` lines will be SHA-pinned automatically via
  `pinDigests: true` in `renovate.json`.

### Homebrew formula (in this repo — no separate tap repo needed)

The formula lives at `Formula/spacelabel.rb` in this repo.  Homebrew can tap
any public GitHub repo with a full URL:

```sh
brew tap McSim85/spacelabel https://github.com/McSim85/spacelabel
brew install McSim85/spacelabel/spacelabel
```

Steps to activate:

- [ ] Flip repo to **public** (Homebrew cannot tap private repos)
- [ ] Copy the stub template into this repo:
  ```sh
  mkdir -p Formula
  cp packaging/homebrew/spacelabel.rb Formula/spacelabel.rb
  ```
- [ ] Fill in real sha256 values for all dependencies:
  ```sh
  brew update-python-resources Formula/spacelabel.rb
  ```
- [ ] Commit `Formula/spacelabel.rb` to `main`
- [ ] Test locally:
  ```sh
  brew tap McSim85/spacelabel https://github.com/McSim85/spacelabel
  brew install McSim85/spacelabel/spacelabel
  spacelabel --version
  ```
- [ ] Enable the automated formula-update job by adding a **repository variable**
  `FORMULA_IN_REPO=true` (Settings → Secrets and variables → Variables).
  After that, each new release will open a PR bumping `Formula/spacelabel.rb`.

### Branch protection — `main`

- [ ] Enable branch protection (Settings → Branches → Add rule):
  - Require a pull request before merging (≥ 1 approval for collaborators)
  - Require status checks: `lint-type-test`
  - Require branches to be up to date
  - Do not allow force pushes or deletions
- [ ] Optionally require signed commits

### GitHub Discussions

- [ ] Enable Discussions (Settings → General → Features → Discussions)
