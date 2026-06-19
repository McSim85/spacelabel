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

- Tag releases as `vX.Y.Z`; keep the tag in sync with `__version__` in
  `src/spacelabel/__init__.py` (pyproject reads it dynamically).
