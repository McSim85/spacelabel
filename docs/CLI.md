# spacelabel — CLI specification

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
(`config.json`, `labels.json`). `--config` overrides the file spacelabel reads/writes.

> **Logging never touches stdout.** `--verbose`/`--debug` only change the stderr
> log level; they never add output to the data channel.

---

## 3. Commands

Legend: `arg` = required positional · `[arg]` = optional · `{a|b}` = choice.

### 3.1 `agent` — run the menu-bar agent (foreground)

```text
spacelabel agent
```

Starts the `NSApplication` accessory app (menu-bar item, optional buttons row,
HUD/overlay per `config.json`) and **blocks** until the agent quits.
This is exactly what the LaunchAgent's `ProgramArguments` invoke.

- Honors the global `--config`.
- Logging in agent mode is quiet (`WARNING+`) and goes to the agent log file
  (`~/Library/Logs/spacelabel/agent.log`), **not** stdout.
  Running `spacelabel --debug agent` from a terminal raises the level for that
  foreground run (useful for development; `uv run spacelabel agent --debug`).
- **Exit:** `0` on a clean menu **Quit**; non-zero if it crashes. This pairs
  with the LaunchAgent's `KeepAlive={SuccessfulExit:false}` so a deliberate Quit
  stays stopped while a crash is restarted.
- Single-instance: the agent refuses to start a second copy (logs a warning and
  exits non-zero) to avoid the Tahoe ControlCenter visibility negotiation loop.

### 3.2 `install` / `uninstall` — manage the login LaunchAgent

```text
spacelabel install   [--no-load]
spacelabel uninstall [--purge] [--yes] [--dry-run]    (--keep-labels: deprecated, hidden)
```

`install` writes `~/Library/LaunchAgents/dev.mcsim.spacelabel.plist` with the
real `$HOME` substituted into the absolute paths, creates
`~/Library/Logs/spacelabel/`, and loads it via
`launchctl bootstrap gui/$UID`. `uninstall` runs
`launchctl bootout …` and removes the plist.

- **Points the agent at the app bundle.** `install` resolves the cask-installed
  `spacelabel.app` executable (the running process is inside the bundle) and writes
  *that* absolute path into the plist, so the agent process **is** the bundle and
  Accessibility keys on `dev.mcsim.spacelabel`. Falls back to the
  source/dev console script beside the interpreter (for `uv pip install -e .` dev
  installs); **refuses** (exit 1) when neither resolves (a transient runner venv).
- **`--no-load`:** write/refresh the plist but don't load it now (load happens at next
  login). Useful in dotfiles bootstrap.
- **`uninstall` (default = `apt remove`):** removes the LaunchAgent, **keeps** all user
  data; prints a breadcrumb pointing to `--purge`.
- **`uninstall --purge` (= `apt purge`):** after removing the agent, deletes **only**
  paths spacelabel *exclusively owns* — the default store
  `~/Library/Application Support/spacelabel/` (removed wholesale, only for the default
  config), the global `~/Library/Caches/spacelabel/` and `~/Library/Logs/spacelabel/`,
  and the per-shell completion scripts. A **custom `--config`** purges those global dirs
  but **never deletes files in the `--config`'s own directory** (spacelabel doesn't own
  that directory — a sibling `labels.json` could be another app's); its config/labels
  there are left for manual removal (the command says so). Refuses if a **foreground
  agent** still holds `agent.lock`. **Never** touches files outside the
  spacelabel-owned paths listed above. Mirrors the cask's `zap` stanza.
  - `--dry-run` prints the resolved paths to **stdout** and deletes nothing (exit 0).
  - `--yes`/`-y` skips the confirmation; **non-TTY without `--yes` refuses (exit 2)**.
  - Each delete is independent/best-effort; a partial failure lists what remained and
    exits 1.
- **`--keep-labels` (deprecated, hidden):** data is kept by default, so it is a no-op;
  it now emits a stderr deprecation pointing to `--purge`.
- Progress (“Removed dev.mcsim.spacelabel …”) → stderr. **Exit:** `0` on success;
  `1` if `launchctl` fails / a purge delete fails; `2` for the non-TTY purge guard.

### 3.3 `status` — install + run state

```text
spacelabel status [--json]
```

Reports the agent's **install** state (LaunchAgent plist present / loaded) and **run**
state — detecting **any** running agent, the managed LaunchAgent **or** a foreground
`spacelabel agent` (both hold `agent.lock`; the agent records its pid there).

- **Default (human):** one line to **stdout**, e.g.
  `running (managed)  pid=4213  label=dev.mcsim.spacelabel`,
  `running (foreground)  pid=50803  …`, or `not running (installed, not running)` /
  `not running (not installed)`.
- **`--json`:** `{"installed": true, "loaded": true, "running": true, "pid": 4213,
  "managed": true, "label": "dev.mcsim.spacelabel"}` to stdout.
- **Exit codes** (LSB-style, unchanged so it still composes in conditionals):
  - `0` — an agent is running (managed **or** foreground)
  - `3` — **not** running (clean negative; `installed`/`loaded` are informational and
    do not change the exit code)
  - `1` — could not determine status (e.g. `launchctl` query failed)

  ```sh
  spacelabel status >/dev/null && echo "up" || echo "down"
  ```

### 3.4 `spaces` — list current Spaces (live CGS read)

```text
spacelabel spaces [--json] [--all-displays/--active-display]
```

Reads the live Space topology via the CGS path and prints the Spaces
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
  visible here without probing each UUID with `note list`.
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
  is normal).
- **Exit:** `0` on success; `1` if the CGS path **and** the plist fallback both
  fail (logged via the no-silent-except policy).

### 3.5 `mode` — show or toggle a display mode

```text
spacelabel mode {menubar|hud|overlay} [--on | --off]
```

With **neither** `--on` nor `--off`, prints the mode's current state to stdout
(`menubar: on`); with one of them, sets it in `config.json` and prints the new
state. The mode name is a `click.Choice` (invalid value → exit 2).

- A running agent picks up the change live via its config file-watch — no restart needed.
- **Exit:** `0` on success; `2` on an invalid mode name.

### 3.6 `label` — create / list / remove labels

The whole point of the tool. Labels are keyed by **Space UUID** — never by position
or index. `current` is a convenience target resolved to the active Space's UUID at
call time via the live CGS read. A literal `<uuid>` target must be a **well-formed UUID**
(copy one from `spacelabel spaces`); any other value — e.g. a transposed
`label set list "Email"` — is a **usage error (exit 2)**, not a silent write to a
Space that can't exist. (A valid UUID for a currently-absent Space is still allowed —
labels are retained for Spaces that aren't live right now.)

```text
spacelabel label set   {<uuid>|current} <text>
spacelabel label list  [--json]
spacelabel label clear {<uuid>|current}
spacelabel label prune [--dry-run]
```

- **`label set`** — assign/replace the label for a Space. `<text>` may contain
  spaces (quote it); empty text is rejected (use `clear`). Resolving `current`
  requires a live read; if it fails → exit 1. On success the entry's
  `updated_at` (and `created_at` if new) is stamped and `last_display` recorded
  for grouping.
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
  Spaces set (orphans). Requires a live read. **`--dry-run`** lists what *would*
  be removed (to stdout) and changes nothing. Retain-by-default policy means this
  is the explicit, opt-in cleanup. It prunes **labels, not tasks**: an orphan that
  still has a note queue is demoted to a notes-only entry (its tasks survive), so
  prune never silently deletes a task list; a notes-only orphan is left untouched.
- **Exit:** `0` success (incl. idempotent clear); `1` if a required live read
  fails or `current` can't be resolved; `2` on missing arguments.

### 3.7 `note` — per-Space task queue

Each Space can carry a small **task queue** alongside its label, keyed by the same
**Space UUID** so the list follows the Space through reorders — not
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
  never captures clicks; toggling is done here via `note done`. And
  `label clear` keeps any tasks (the entry becomes notes-only), so clearing a label
  never discards its task list.
- **Exit:** `0` success (incl. idempotent clear); `1` if `current` can't be resolved
  or a write fails; `2` a bad/out-of-range index or missing arguments.

### 3.8 `config` — read / write configuration

```text
spacelabel config get [<key>]
spacelabel config set  <key> <value>
```

Keys are **dotted paths** into `config.json`, e.g.
`modes.hud`, `hud.position`, `hud.duration_ms`, `overlay.corner`,
`overlay.show_notes`, `overlay.note_font_size`, `menubar.show_buttons_row`,
`debounce_ms`, `log_level`.

```sh
# Move the HUD — any of the 9 anchors (default center):
spacelabel config set hud.position top-left
```

- **`config get <key>`** — print the value to **stdout** (raw scalar, no quotes,
  so it's substitutable: `dur=$(spacelabel config get hud.duration_ms)`).
  Unknown key → exit 1 with an error on stderr.
- **`config get` (no key)** — pretty-print the full effective config to stdout as JSON.
- **`config set <key> <value>`** — validate `<value>` against the key's expected
  type (bool/int/enum/string) and the `schema_version`, write atomically
  (temp → `fsync` → `os.replace` under an `fcntl.flock`), then echo
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

Enable tab-completion for the `spacelabel` CLI. `completion install` writes click's
**generated completion script** into your shell's auto-load directory (so the shell
picks it up automatically — it does not run `spacelabel` on every startup, and for
fish/bash it needs no rc edit). `--shell auto` (the default) detects the shell from
`$SHELL`. Writing is idempotent (rewrites only when the script changes).

> **Homebrew cask installs:** completion is **not** set up by `brew install --cask`
> (only Homebrew *formulae* can install completions, not casks), so run
> `spacelabel completion install` once after installing.

- **`--dry-run`** — print the generated completion script to **stdout** (the data
  channel) and the target path to stderr, without writing anything. Works even
  when `$HOME` can't be resolved (the script itself needs no home dir).
- **Where it writes:**
  - **fish** → `~/.config/fish/completions/spacelabel.fish` (auto-loaded; honors
    `$XDG_CONFIG_HOME`). No rc edit.
  - **bash** → `~/.local/share/bash-completion/completions/spacelabel` (auto-loaded
    by bash-completion v2; honors `$BASH_COMPLETION_USER_DIR`/`$XDG_DATA_HOME`). No
    rc edit. **Requires bash ≥ 4.4 + bash-completion v2** — the macOS system bash
    3.2 cannot use click completion (install Homebrew `bash` + `bash-completion@2`).
  - **zsh** → `_spacelabel` dropped into a dedicated completion directory already on
    your `$fpath` (prefers `~/.zfunc`, then a `…/completions` dir, then a
    `…/site-functions` dir; framework plugin/cache dirs are never used). If no
    suitable `$fpath` directory exists, it creates `~/.zfunc` and adds it to `fpath`
    in `~/.zshrc` (one time). After install, restart the shell or run
    `autoload -Uz compinit && compinit` (if it doesn't appear, `rm -f ~/.zcompdump*`
    and restart).
- **Exit:** `0` success (including the idempotent no-op); `1` if `$SHELL` can't be
  detected (pass `--shell`) or the target can't be written.

**Dynamic completions** (beyond command/option names): the `{<uuid>|current}`
arguments of `label set/clear`, `note …`, and `display set/clear` complete to
`current` plus live Space/display UUIDs (stored labeled/note-bearing UUIDs are also
offered on the `clear`/operate paths so an offline entry stays completable);
`config get/set` keys complete from the live config schema; `mode` names complete
from the fixed choice set. All completion reads are best-effort — if the live
CGS/display read is unavailable, completion degrades to `current` (and any stored
UUIDs) rather than erroring, and never prints to your shell.

> Prefer not to install a file? Activate for the current shell only (zsh):
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
- Codes `1` and `2` are click's own conventions (`ClickException` → 1, `UsageError` → 2).
  Do not add new codes beyond the reserved `3` for `status`.
- Every non-zero exit is accompanied by a human message on **stderr**
  (`Error: …` for code 1/2). stdout stays empty or partial-but-valid is avoided —
  on error, prefer writing nothing to stdout.

---

## 5. stdout vs stderr — the parsing contract

| Channel | Carries | Examples |
|---|---|---|
| **stdout** | Command results: the aligned text table (incl. its header) or the `--json` payload; `config get`/`status` lines; `--version` | `spaces`, `label list`, `config get`, `status` |
| **stderr** | Everything else: log lines (all levels), warnings, errors, progress, idempotent-clear notes | `Error: …`, `INFO: reloaded config`, `WARNING: …` |

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
spacelabel mode overlay --on

# Tune config and read it back for substitution
spacelabel config set hud.duration_ms 900
dur=$(spacelabel config get hud.duration_ms)

# Clean up orphaned labels, preview first
spacelabel label prune --dry-run
spacelabel label prune

# Run the agent in the foreground for development
uv run spacelabel agent --debug
```
