# spacelabel — CLI specification

> **Status:** finalized in Phase 3 (design-only). The command tree below is the
> **canonical surface** — it matches the scaffold in `src/spacelabel/cli.py` and
> the README verbatim. **Command names are locked; do not rename them in Phase 4.**
> Flags marked **(proposed)** are new in Phase 3 and are flagged for Phase-4
> implementation in [`DECISIONS.md`](../DECISIONS.md) §9; everything else is
> already wired in the scaffold.
>
> Companion docs: [`DESIGN.md`](../DESIGN.md) §8 (CLI surface & logging),
> [`DECISIONS.md`](../DECISIONS.md) §2.5/2.6 (click + logging), [`UI.md`](./UI.md).

---

## 1. Synopsis

```text
spacelabel [GLOBAL OPTIONS] COMMAND [ARGS...]
```

`spacelabel` is a single `click` group with one console entry point
(`spacelabel = "spacelabel.cli:main"`). The long-lived menu-bar agent is the
**`agent`** subcommand (what the LaunchAgent runs). **There is no `run`
subcommand** — every other subcommand is a one-shot action that shares the same
CGS-read and JSON-store layers, then exits.

The two design invariants that make the CLI scriptable:

1. **stdout is the data channel; stderr is the diagnostics channel.** Anything a
   script should parse (`spaces`, `label list`, `config get`, `status --json`)
   is written to **stdout** via `click.echo`. Every log line, warning, error,
   and progress message goes to **stderr** (or the agent log file). You can
   always do `spacelabel spaces > spaces.txt` and get clean data with no log
   noise mixed in.
2. **Exit codes are stable and meaningful** (§4) so commands compose in shell
   conditionals (`spacelabel status && …`).

---

## 2. Global options

These attach to the root group and apply to every subcommand. They must appear
**before** the subcommand (`spacelabel --debug spaces`, not
`spacelabel spaces --debug`).

| Option | Type | Default | Effect |
|---|---|---|---|
| `--config PATH` | file path (`dir_okay=False`) | platform default¹ | Use an alternate `config.json`. The file need not exist for read commands (defaults are used); it is created on first write. The **labels** store path is derived from the same directory. |
| `--verbose` | flag | off | Raise stderr logging from `WARNING` to `INFO`. |
| `--debug` | flag | off | Raise stderr logging to `DEBUG` (takes precedence over `--verbose`). |
| `-V`, `--version` | flag | — | Print `spacelabel, version X.Y.Z` to stdout and exit 0. |
| `-h`, `--help` | flag | — | Print help for the group or subcommand and exit 0. |

¹ Default config dir = `~/Library/Application Support/spacelabel/`
(`config.json`, `labels.json`) per DESIGN §7. `--config` overrides the file
spacelabel reads/writes.

> **Logging never touches stdout.** `--verbose`/`--debug` only change the stderr
> log level; they never add output to the data channel. This is enforced by
> `setup_logging(LogMode.CLI, …)` (DESIGN §8.2).

---

## 3. Commands

Legend: `arg` = required positional · `[arg]` = optional · `{a|b}` = choice.

### 3.1 `agent` — run the menu-bar agent (foreground)

```text
spacelabel agent
```

Starts the `NSApplication` accessory app (menu-bar item, optional buttons row,
HUD/overlay/wallpaper per `config.json`) and **blocks** until the agent quits.
This is exactly what the LaunchAgent's `ProgramArguments` invoke
(`…/spacelabel agent`, DESIGN §9.2).

- Honors the global `--config`.
- Logging in agent mode is quiet (`WARNING+`) and goes to the agent log file
  (`~/Library/Logs/spacelabel/agent.log`), **not** stdout — see DECISIONS 2.6.
  Running `spacelabel --debug agent` from a terminal raises the level for that
  foreground run (useful for development; `uv run spacelabel agent --debug`).
- **Exit:** `0` on a clean menu **Quit**; non-zero if it crashes. This pairs
  with the LaunchAgent's `KeepAlive={SuccessfulExit:false}` so a deliberate Quit
  stays stopped while a crash is restarted (DECISIONS 6.4).
- Single-instance: the agent refuses to start a second copy (logs a warning and
  exits non-zero) to avoid the Tahoe ControlCenter visibility negotiation loop
  (DECISIONS 6.5).

### 3.2 `install` / `uninstall` — manage the login LaunchAgent

```text
spacelabel install   [--no-load]        (--no-load: proposed)
spacelabel uninstall [--keep-labels]    (--keep-labels: proposed — no-op today)
```

`install` writes `~/Library/LaunchAgents/dev.mcsim.spacelabel.plist` with the
real `$HOME` substituted into the absolute paths, creates
`~/Library/Logs/spacelabel/`, and loads it via
`launchctl bootstrap gui/$UID` (DESIGN §9.2). `uninstall` runs
`launchctl bootout …` and removes the plist.

- **`--no-load` (proposed):** write/refresh the plist but don't load it now
  (load happens at next login). Useful in dotfiles bootstrap.
- `uninstall` never deletes `labels.json`/`config.json`; the `--keep-labels`
  flag is reserved for a future destructive variant and is documented now so the
  default (keep) is explicit.
- Progress (“Loaded dev.mcsim.spacelabel”) → stderr. **Exit:** `0` on success;
  `1` if `launchctl` fails or the plist can't be written.

### 3.3 `status` — is the agent running?

```text
spacelabel status [--json]              (--json: proposed)
```

Reports whether the menu-bar agent / LaunchAgent is currently running.

- **Default (human):** one line to **stdout**, e.g.
  `running  pid=4213  label=dev.mcsim.spacelabel` or `not running`.
- **`--json` (proposed):** `{"running": true, "pid": 4213, "label": "dev.mcsim.spacelabel"}`
  to stdout.
- **Exit codes** (LSB-style, so it composes in conditionals):
  - `0` — agent is running
  - `3` — agent is **not** running (clean negative; distinct from an error)
  - `1` — could not determine status (e.g. `launchctl` query failed)

  ```sh
  spacelabel status >/dev/null && echo "up" || echo "down"
  ```

### 3.4 `spaces` — list current Spaces (live CGS read)

```text
spacelabel spaces [--json] [--all-displays/--active-display]   (flags: proposed)
```

Reads the live Space topology via the CGS path (DESIGN §3) and prints every
labelable Space across all displays, marking the active one. **Data → stdout.**

- **Default output** is one record per line, **tab-separated**, stable column
  order, no decorative borders — parseable with `cut`/`awk`:

  ```text
  CURRENT  DISPLAY                          SPACE_UUID                            LABEL
  *        LG UltraFine (874A623F...)       6622AC87-2FD2-48E8-934D-F6EB303AC9BA  Email
           LG UltraFine (874A623F...)       1A0F5C2E-...                          Code
           DELL 4K (6FBB92D9...)            9C44E7B1-...                          (unlabeled)
  ```

  The header line is printed to stderr (not stdout) so a pipe stays pure data;
  pass `--json` for a self-describing structure instead.
- **`--json` (proposed):** array of objects
  `[{"uuid","display_uuid","display_name","label":null,"current":true}, …]`.
- **`--active-display` (proposed):** restrict to the menu-bar-owning display.
- Special/fullscreen Spaces (`type != 0`, `TileLayoutManager`, empty `uuid`) are
  **excluded** (DESIGN §3.4).
- **Exit:** `0` on success; `1` if the CGS path **and** the plist fallback both
  fail (logged via the no-silent-except policy, DESIGN §8.2).

### 3.5 `mode` — show or toggle a display mode

```text
spacelabel mode {menubar|hud|overlay|wallpaper} [--on | --off]
```

With **neither** `--on` nor `--off`, prints the mode's current state to stdout
(`menubar: on`); with one of them, sets it in `config.json` and prints the new
state. The mode name is a `click.Choice` (invalid value → exit 2).

- A running agent picks up the change live via its config file-watch (DESIGN §7.3) —
  no restart needed.
- `wallpaper` is **experimental / disabled by default**; enabling it prints a
  one-line caveat to stderr (cosmetic, may revert — DESIGN §6.4 / §7).
- **Exit:** `0` on success; `2` on an invalid mode name.

### 3.6 `label` — create / list / remove labels

The whole point of the tool. Labels are keyed by **Space UUID** (DECISIONS 1.4).
`current` is a convenience target resolved to the active Space's UUID at call
time via the live CGS read.

```text
spacelabel label set   {<uuid>|current} <text>
spacelabel label list  [--json]                       (--json: proposed)
spacelabel label clear {<uuid>|current}
spacelabel label prune [--dry-run]                    (--dry-run: proposed)
```

- **`label set`** — assign/replace the label for a Space. `<text>` may contain
  spaces (quote it); empty text is rejected (use `clear`). Resolving `current`
  requires a live read; if it fails → exit 1. On success the entry's
  `updated_at` (and `created_at` if new) is stamped and `last_display` recorded
  for grouping (DESIGN §7.1).
  ```sh
  spacelabel label set current "Email"
  spacelabel label set 6622AC87-2FD2-48E8-934D-F6EB303AC9BA "Code review"
  ```
- **`label list`** — print all stored labels. **Data → stdout**, default
  tab-separated `UUID<TAB>LABEL` (optional `last_display` column under `--json`).
  This reads only `labels.json`, so it works even if the CGS path is unavailable.
- **`label clear`** — remove one label. **Idempotent:** clearing a UUID with no
  stored label prints an informational note to **stderr** and still exits `0`,
  so scripts can clear unconditionally. Resolving `current` that can't be read → exit 1.
- **`label prune`** — drop labels whose UUID is absent from the **current**
  Spaces set (orphans). Requires a live read. **`--dry-run` (proposed)** lists
  what *would* be removed (to stdout) and changes nothing. Retain-by-default
  policy means this is the explicit, opt-in cleanup (DECISIONS 5.6).
- **Exit:** `0` success (incl. idempotent clear); `1` if a required live read
  fails or `current` can't be resolved; `2` on missing arguments.

### 3.7 `config` — read / write configuration

```text
spacelabel config get [<key>]        (bare `get` dumps all — proposed)
spacelabel config set  <key> <value>
```

Keys are **dotted paths** into `config.json` (DESIGN §7.2), e.g.
`modes.hud`, `hud.position`, `hud.duration_ms`, `overlay.corner`,
`menubar.show_buttons_row`, `debounce_ms`, `log_level`.

```sh
# Move the HUD — any of the 9 anchors (default center):
spacelabel config set hud.position top-left
```

- **`config get <key>`** — print the value to **stdout** (raw scalar, no quotes,
  so it's substitutable: `dur=$(spacelabel config get hud.duration_ms)`).
  Unknown key → exit 1 with an error on stderr.
- **`config get` (no key, proposed)** — pretty-print the full effective config
  to stdout as JSON.
- **`config set <key> <value>`** — validate `<value>` against the key's expected
  type (bool/int/enum/string) and the `schema_version`, write atomically
  (temp → `fsync` → `os.replace` under an `fcntl.flock`, DESIGN §7.3), then echo
  the stored value. Type/enum violations → exit 1 with a specific message
  (e.g. `overlay.corner must be one of top-left,top-right,bottom-left,bottom-right`).
- A running agent reloads on write (file-watch). **Exit:** `0` success;
  `1` unknown key or invalid value; `2` missing arguments.

---

## 4. Exit codes (whole-CLI contract)

| Code | Meaning | Emitted by |
|---|---|---|
| `0` | Success | any command |
| `1` | Runtime / application error — CGS read failed *and* plist fallback failed; unknown config key or invalid value; `current` could not be resolved; `launchctl`/install failure; agent crash | command body (`click.ClickException`, logged) |
| `2` | **Usage** error — unknown command/option, missing required argument, invalid `Choice` (bad mode name) | `click` itself |
| `3` | `status` only: agent is **not running** (a clean negative, not an error) | `status` |

Notes:
- Codes `1` and `2` are click's own conventions (verified against the scaffold:
  `ClickException` → 1, `UsageError` → 2). Phase 4 should not invent ad-hoc
  codes beyond the reserved `3` for `status`.
- Every non-zero exit is accompanied by a human message on **stderr**
  (`Error: …` for code 1/2). stdout stays empty or partial-but-valid is avoided —
  on error, prefer writing nothing to stdout.

---

## 5. stdout vs stderr — the parsing contract

| Channel | Carries | Examples |
|---|---|---|
| **stdout** | Machine-readable results only | `spaces`, `label list`, `config get`, `status` line / `--json` payloads, `--version` |
| **stderr** | Everything else: log lines (all levels), warnings, errors, progress, table headers, the experimental-wallpaper caveat, idempotent-clear notes | `Error: …`, `INFO: reloaded config`, `WARNING: wallpaper mode is experimental` |

Consequence: any command's stdout can be redirected/piped and parsed without
filtering log noise, at any `--verbose`/`--debug` level. `--json` variants exist
specifically so consumers never have to parse human formatting.

---

## 6. Examples

```sh
# Label the current Space, then list everything (clean, parseable)
spacelabel label set current "Email"
spacelabel label list

# Pure data pipeline — no log noise even with --debug
spacelabel --debug spaces | awk -F'\t' '$1=="*"{print $4}'   # active Space's label

# Script the agent lifecycle
spacelabel install
spacelabel status >/dev/null && echo "agent up" || echo "agent down"

# Toggle modes (agent reloads live)
spacelabel mode hud --on
spacelabel mode wallpaper --on        # prints experimental caveat to stderr

# Tune config and read it back for substitution
spacelabel config set hud.duration_ms 900
dur=$(spacelabel config get hud.duration_ms)

# Clean up orphaned labels, preview first
spacelabel label prune --dry-run
spacelabel label prune

# Run the agent in the foreground for development
uv run spacelabel agent --debug
```

---

## 7. Phase-4 hand-off (flags introduced here)

The following are **new in Phase 3** and must be implemented in Phase 4 (also
recorded in [`DECISIONS.md`](../DECISIONS.md) §9):

- `--json` on `spaces`, `label list`, `status`, and bare `config get`.
- `--active-display/--all-displays` on `spaces`.
- `--dry-run` on `label prune`.
- `--no-load` on `install`; `--keep-labels` reserved on `uninstall`.
- Exit code **3** reserved for `status` (agent not running).
- Default `spaces`/`label list` output = **tab-separated, header-to-stderr** —
  Phase 4 must keep stdout pure (no aligned-column padding) for parseability.

None of these rename or remove a scaffolded command; they only add optional
flags and define output/exit semantics around the locked command tree.
