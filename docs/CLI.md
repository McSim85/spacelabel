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
CGS-read and JSON-store layers, then exits. Shell tab-completion is available via
`spacelabel completion install` (§3.10).

The two design invariants that make the CLI scriptable:

1. **stdout is the data channel; stderr is the diagnostics channel.** Anything a
   script should parse (`spaces`, `label list`, `note list`, `config get`,
   `status --json`) is written to **stdout** via `click.echo`. Every log line, warning, error,
   and progress message goes to **stderr** (or the agent log file). You can
   always do `spacelabel spaces > spaces.txt` and get clean data with no log
   noise mixed in.
2. **Exit codes are stable and meaningful** (§4) so commands compose in shell
   conditionals (`spacelabel status && …`).

---

## 2. Global options

These attach to the root group and apply to every subcommand. `--verbose` and
`--debug` work in **either** position — before *or* after the subcommand
(`spacelabel --debug spaces` and `spacelabel spaces --debug` are equivalent).
`--config` and `--version` must appear **before** the subcommand.

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

- **Requires the pipx install.** The login agent must point at the durable
  `~/.local/bin/spacelabel` shim (launchd has no shell `$PATH`), so `install`
  **refuses** (exit 1) unless that canonical shim exists — run
  `pipx install spacelabel` first. It will never write a transient venv/dev-shell
  path into the plist.

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

Reads the live Space topology via the CGS path (DESIGN §3) and prints the Spaces
across all displays, marking each display's current one. **Data → stdout.**

- **Default output** is a **space-aligned table** (header on stdout), stable
  column order, no decorative borders:

  ```text
  CURRENT  DISPLAY           SPACE_UUID                            LABEL        NOTES
  *        LG UltraFine (1)  6622AC87-2FD2-48E8-934D-F6EB303AC9BA  Email        2
           LG UltraFine (1)  1A0F5C2E-...                          (unlabeled)  1
  *        DELL 4K (2)       (none)                                (no UUID)
  ```

  The columns are padded for readability, so this is **not** cleanly
  `cut`/`awk`-parseable — pass **`--json`** for a self-describing structure that
  scripts can consume. The **`NOTES`** column is the task-queue size (blank when 0),
  so a Space carrying notes — including a notes-only one shown `(unlabeled)` — is
  visible here without probing each UUID with `note list` (DECISIONS 9.10).
- **`--json`:** array of objects
  `[{"uuid","display_uuid","display_name","label","notes","current","labelable"}, …]`
  (`uuid` is `null` and `labelable` is `false` for a Space with no assigned UUID;
  `label` is `null` for an unlabeled/notes-only Space; `notes` is the task count).
- **`--active-display`:** restrict to the menu-bar-owning display.
- Special/fullscreen Spaces (`type != 0`, `TileLayoutManager`) are **excluded**.
  An ordinary Space that macOS has **not yet assigned a UUID** (a display's single
  default Space — empty `uuid`, often a `wsid` key) is **surfaced** as `(none)` /
  `(no UUID)` so the display is visible, but it **cannot be labeled** until macOS
  assigns a UUID (e.g. after you add a Space on that display). With separate Spaces
  per display, each display marks its own current Space (so more than one `*` row
  is normal). (DESIGN §3.4, DECISIONS §9.2 revised)
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
time via the live CGS read. A literal `<uuid>` target must be a **well-formed UUID**
(copy one from `spacelabel spaces`); any other value — e.g. a transposed
`label set list "Email"` — is a **usage error (exit 2)**, not a silent write to a
Space that can't exist. (A valid UUID for a currently-absent Space is still allowed —
labels are retained for Spaces that aren't live right now, DECISIONS 5.6.)

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
- **`label list`** — print all stored labels. **Data → stdout** as a
  space-aligned `SPACE_UUID` / `LABEL` table (header on stdout); pass `--json` for
  a structured array (with `color`/`last_display`) that scripts can parse.
  This reads only `labels.json`, so it works even if the CGS path is unavailable.
- **`label clear`** — remove one label. **Idempotent:** clearing a UUID with no
  stored label prints an informational note to **stderr** and still exits `0`,
  so scripts can clear unconditionally. Resolving `current` that can't be read → exit 1.
- **`label prune`** — drop labels whose UUID is absent from the **current**
  Spaces set (orphans). Requires a live read. **`--dry-run` (proposed)** lists
  what *would* be removed (to stdout) and changes nothing. Retain-by-default
  policy means this is the explicit, opt-in cleanup (DECISIONS 5.6). It prunes
  **labels, not tasks**: an orphan that still has a note queue is demoted to a
  notes-only entry (its tasks survive), so prune never silently deletes a task
  list (DECISIONS 9.10); a notes-only orphan is left untouched.
- **Exit:** `0` success (incl. idempotent clear); `1` if a required live read
  fails or `current` can't be resolved; `2` on missing arguments.

### 3.7 `note` — per-Space task queue

Each Space can carry a small **task queue** alongside its label, keyed by the same
**Space UUID** (DECISIONS 9.10) so the list follows the Space through reorders — not
by display. The corner overlay renders it as the bold label/`Desktop N` title above
the task lines. The CLI is the edit surface; `current` resolves to the active Space, and
a literal `<uuid>` target must be a well-formed Space UUID (a transposed
`note add list current` is a usage error, exit 2 — same rule as `label`).

> **Heads-up:** the corner overlay shows notes only when **overlay mode is on**
> (`spacelabel mode overlay --on`; it's off by default). A `note add` reflects live
> once the agent is running with that mode enabled.

```text
spacelabel note add    {<uuid>|current} <text>
spacelabel note list   [{<uuid>|current}] [--json]    (--json)
spacelabel note done   {<uuid>|current} <index>
spacelabel note undone {<uuid>|current} <index>
spacelabel note clear  {<uuid>|current} [<index>]
```

- **`note add`** — append a task. Creates the entry even if the Space has no label
  yet (a **notes-only** entry is valid); empty text is rejected. The TARGET is
  validated as a UUID-or-`current` (exit 2 otherwise). Confirmation → stderr.
- **`note list`** — with a TARGET, print that queue: **data → stdout** as a `#` /
  `DONE` / `TASK` table; `--json` → `[{"index","text","done"}, …]` (**1-based**
  `index`). **With no TARGET**, list every Space that has notes (`SPACE_UUID` /
  `NOTES` count; `--json` → `[{"uuid","notes"}, …]`) — so a notes-only queue stays
  discoverable even when its Space isn't live (`spaces` shows only live Spaces).
  Reads only `labels.json` (no live read for a literal UUID).
- **`note done` / `note undone`** — set/clear the `done` flag of task `<index>`
  (**1-based**, humans). The overlay shows `☑`/`☐` accordingly on its next refresh.
- **`note clear`** — remove one task (`<index>`) or, with no index, the whole queue.
  **Idempotent** when there is nothing to clear (note → stderr, exit `0`). An entry
  left with neither a label nor any task is removed entirely.
- A bad index — or any index against an empty queue — is a **usage error (exit 2)**.
- **TARGET validation is create-only.** `note add` (like `label set`) rejects a
  non-UUID literal, but `list`/`done`/`undone`/`clear` accept any **existing** key —
  so a pre-existing legacy/typo entry (e.g. a stray `list`) can still be inspected
  with `note list <key>` and removed with `note clear <key>`.
- Notes live in `labels.json` next to the label, so a `note add` from the CLI is
  reflected **live** by a running agent (the overlay re-renders on the file-watch).
- The overlay checkboxes are **display-only** glyphs — the panel is click-through and
  never captures clicks (DESIGN §6.3); toggling is done here via `note done`. And
  `label clear` keeps any tasks (the entry becomes notes-only), so clearing a label
  never discards its task list.
- **Exit:** `0` success (incl. idempotent clear); `1` if `current` can't be resolved
  or a write fails; `2` a bad/out-of-range index or missing arguments.

### 3.8 `config` — read / write configuration

```text
spacelabel config get [<key>]        (bare `get` dumps all — proposed)
spacelabel config set  <key> <value>
```

Keys are **dotted paths** into `config.json` (DESIGN §7.2), e.g.
`modes.hud`, `hud.position`, `hud.duration_ms`, `overlay.corner`,
`overlay.show_notes`, `overlay.note_font_size`, `menubar.show_buttons_row`,
`wallpaper.position`, `wallpaper.font_size` (int point size or `auto`),
`debounce_ms`, `log_level`.

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

### 3.9 `display` — name displays

```text
spacelabel display set   {<uuid>|current} <name>
spacelabel display list  [--json]
spacelabel display clear {<uuid>|current}
```

Assign a custom name to a **display** (keyed by its stable display UUID, stored in
`displays.json`). The custom name is shown in the menu's per-display headers, the
Preferences display rows, and the `spaces` DISPLAY column; clearing reverts to the
system name. `current` resolves to the active (menu-bar-owning) display.

- **`display list`** — connected displays with UUID, resolved name, and source
  (`system`/`custom`), marking the active one; `--json` →
  `[{"uuid","name","custom","active"}, …]`. Falls back to stored names when the
  live display topology is unavailable.
- **Exit:** `0` success; `1` if `current` can't be resolved or a write fails;
  `2` empty name / missing arguments.

> Find a display's UUID with `spacelabel display list` (or use `current`).

---

### 3.10 `completion` — shell tab-completion

```text
spacelabel completion install [--shell {auto|zsh|bash|fish}] [--dry-run]
```

Enable tab-completion for the `spacelabel` CLI. Completion is powered by click's
built-in support; `completion install` writes the one-line activation snippet into
the right rc file for your shell (idempotently). `--shell auto` (the default)
detects the shell from `$SHELL`.

- **`--dry-run`** — print the activation snippet to **stdout** (the data channel)
  and the target path to stderr, without writing anything. Pipe or `eval` it
  yourself, or just copy it.
- **Where it writes:** zsh → `~/.zshrc`; bash → `~/.bash_profile` (bash on macOS
  is a login shell, which sources `~/.bash_profile`/`~/.profile`, not `~/.bashrc`;
  an existing `~/.profile` is used if present); fish →
  `~/.config/fish/completions/spacelabel.fish`. Re-running is a no-op once present.
  Restart your shell (or `source` the file) to activate.
- **Exit:** `0` success (including the idempotent no-op); `1` if `$SHELL` can't be
  detected (pass `--shell`) or the rc file can't be written.

**Dynamic completions** (beyond command/option names): the `{<uuid>|current}`
arguments of `label set/clear`, `note …`, and `display set/clear` complete to
`current` plus live Space/display UUIDs (labeled or note-bearing UUIDs are added
for the `clear`/operate paths so an offline entry stays completable); `config
get/set` keys complete from the live config schema; `mode` names complete from the
fixed choice set. All completion reads are best-effort — if the live CGS/display
read is unavailable, completion degrades to `current` (and any stored UUIDs)
rather than erroring.

> Manual activation without the installer (zsh):
> `eval "$(_SPACELABEL_COMPLETE=zsh_source spacelabel)"`.

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
| **stdout** | Command results: the aligned text table (incl. its header) or the `--json` payload; `config get`/`status` lines; `--version` | `spaces`, `label list`, `config get`, `status` |
| **stderr** | Everything else: log lines (all levels), warnings, errors, progress, the experimental-wallpaper caveat, idempotent-clear notes | `Error: …`, `INFO: reloaded config`, `WARNING: wallpaper mode is experimental` |

Consequence: stdout is free of **log noise** at any `--verbose`/`--debug` level, so
diagnostics never corrupt a redirect. The default text tables are **aligned for
humans, not for `cut`/`awk`** — **`--json`** is the machine-readable channel and
exists on `spaces`/`label list`/`status`/`config get` specifically so consumers
never parse the padded human formatting.

**Color:** output is colorized **only on an interactive terminal** — a bold table
header and a green current row on stdout, level-colored log lines on stderr. When
stdout/stderr is piped or redirected (or `NO_COLOR` is set) all ANSI is stripped,
so redirected output is plain text.

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
