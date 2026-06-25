# Fix — `brew upgrade --cask` race: LaunchAgent KeepAlive restart loop  (item AB)

**Model:** Sonnet 4.6 · **effort:** low. **Fresh session + fresh branch off latest `main`.**
Part of the Phase-6 fix set — see [`fix-sessions-overview.md`](fix-sessions-overview.md).

## Item (full diagnosis in `improvements.md` item AB)

After `brew upgrade --cask spacelabel`, two agent instances race for the lock.
The loser exits 1; `KeepAlive: {SuccessfulExit: false}` retries forever → log spam.
Two cooperating bugs:

1. **Exit code:** losing lock-race exits 1 → LaunchAgent KeepAlive restart loop.
2. **Cask:** no `uninstall launchctl:` stanza → Homebrew never boots out the service
   before installing the new binary → `RunAtLoad` + brew "Reopen" race.

## Do this

**Fix A — `agent/app.py` `_acquire_single_instance_lock`:**
Change the losing instance's `raise SystemExit(1)` to `raise SystemExit(0)` with a
clear log message. Exit 0 = "another agent is handling this; I'm done." KeepAlive
ignores it. The winning agent keeps running normally.

**Fix B — `Casks/spacelabel.rb`:**
Add an `uninstall` stanza with `launchctl: "dev.mcsim.spacelabel"` so `brew upgrade`
properly boots out the service (killing the managed agent) before swapping in the new
binary. After installation `RunAtLoad` fires once with no race.

```ruby
  uninstall launchctl: "dev.mcsim.spacelabel"
```

Keep the existing `zap` stanza unchanged.

## Read first

`agent/app.py` (`_acquire_single_instance_lock`, the `SystemExit(1)` at the
lock-lost path), `Casks/spacelabel.rb` (current `uninstall`/`zap` stanzas, or absence
thereof), DECISIONS §6.4 (LaunchAgent `KeepAlive` policy), §6.8 (cask distribution).

## Acceptance

- `brew upgrade --cask spacelabel` produces **no** repeated "another agent is already
  running" log entries after the upgrade completes.
- A manual second `spacelabel agent` start (while the real agent runs) exits 0 and
  logs a clear one-line message; it does **not** fill the log with retries.
- `spacelabel status` still correctly reports the agent as running (lock-held check,
  §9.1 / `install.py`).

## Before committing

Gates + codex review loop until clean. Conventional Commit
(`fix(agent): exit 0 on lock-busy so KeepAlive doesn't loop; fix(cask): bootout on upgrade`).
Ask before commit/push. Mark AB done in `improvements.md`, tick overview row.
