cask "spacelabel" do
  version "0.6.1"
  # PLACEHOLDER sha256 -> this cask is NOT installable until the first signed-.app
  # release. The release pipeline (publish.yml `update-cask`) PR-bumps `version` + this
  # `sha256` (of the zipped, ad-hoc-signed spacelabel.app asset) when that release is cut;
  # `brew install --cask` only works once THAT bump PR is merged to the default branch.
  # Until then, build + install the bundle locally with `tools/build_app.sh`.
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"

  url "https://github.com/McSim85/spacelabel/releases/download/v#{version}/spacelabel-#{version}.zip",
      verified: "github.com/McSim85/spacelabel/"
  name "spacelabel"
  desc "Label Spaces (virtual desktops) by their stable UUID, reorder-proof"
  homepage "https://github.com/McSim85/spacelabel"

  # Verified on macOS 26 "Tahoe" (private CGS reads). The ad-hoc build is arm64-only
  # for now; universal2 + Developer-ID notarization are a deferred follow-on.
  depends_on macos: :tahoe
  depends_on arch: :arm64

  app "spacelabel.app"
  # Expose the CLI on PATH via the bundle's shim (Contents/Resources/spacelabel), NOT the
  # py2app stub directly: a stub invoked through a symlink resolves @executable_path to the
  # symlink's dir and can't find its embedded Python. The shim execs the stub by absolute
  # path. Same bundle, one dev.mcsim.spacelabel identity for both agent and CLI.
  binary "#{appdir}/spacelabel.app/Contents/Resources/spacelabel"

  # The login agent is managed by `spacelabel install` (it writes the LaunchAgent
  # plist). On uninstall: unload + quit it, and trash the (user-owned) plist so a normal
  # `brew uninstall` leaves no stale login item pointing at the removed bundle. Use
  # `trash:` not `delete:` -- `delete:` shells out to `sudo rm` (a password prompt), wrong
  # for a file under the user's own ~/Library.
  uninstall launchctl: "dev.mcsim.spacelabel",
            quit:      "dev.mcsim.spacelabel",
            trash:     "~/Library/LaunchAgents/dev.mcsim.spacelabel.plist"

  # `brew uninstall --zap` deep-clean — mirrors `spacelabel uninstall --purge`
  # (install.purge_targets) rather than nuking the whole data dir:
  #   * stop the agent FIRST (launchctl/quit/signal) so the store is never trashed out from
  #     under a running instance whose agent.lock still guards single-instance startup;
  #   * trash only spacelabel-OWNED files in Application Support (config/labels/displays +
  #     their .lock + agent.lock + leaked "<json>.<rand>.tmp" temps) so a foreign file a
  #     user kept there (e.g. an alternate --config) survives, plus the dedicated
  #     caches/logs and the well-known DEFAULT completion paths (best effort);
  #   * `rmdir` the data dir LAST — removed only if it is now empty (preserves foreign files).
  # Completions at non-default locations (zsh resolves a writable dir on $fpath;
  # fish/bash honor $XDG_CONFIG_HOME/$XDG_DATA_HOME/$BASH_COMPLETION_USER_DIR) can't be
  # enumerated statically — `spacelabel uninstall --purge` resolves those at runtime.
  zap launchctl: "dev.mcsim.spacelabel",
      quit:      "dev.mcsim.spacelabel",
      signal:    ["TERM", "dev.mcsim.spacelabel"],
      trash:     [
        "~/.config/fish/completions/spacelabel.fish",
        "~/.local/share/bash-completion/completions/spacelabel",
        "~/Library/Application Support/spacelabel/agent.lock",
        "~/Library/Application Support/spacelabel/config.json",
        "~/Library/Application Support/spacelabel/config.json.*.tmp",
        "~/Library/Application Support/spacelabel/config.json.lock",
        "~/Library/Application Support/spacelabel/displays.json",
        "~/Library/Application Support/spacelabel/displays.json.*.tmp",
        "~/Library/Application Support/spacelabel/displays.json.lock",
        "~/Library/Application Support/spacelabel/labels.json",
        "~/Library/Application Support/spacelabel/labels.json.*.tmp",
        "~/Library/Application Support/spacelabel/labels.json.lock",
        "~/Library/Caches/spacelabel",
        "~/Library/LaunchAgents/dev.mcsim.spacelabel.plist",
        "~/Library/Logs/spacelabel",
      ],
      rmdir:     "~/Library/Application Support/spacelabel"

  caveats <<~CAVEATS
    spacelabel ships ad-hoc-signed (no Apple Developer account yet). After install:
      * run `spacelabel install` to start the menu-bar agent at login;
      * to enable click-to-switch, grant Accessibility to "spacelabel" under
        System Settings -> Privacy & Security -> Accessibility.
    An ad-hoc signature's cdhash changes each release, so the Accessibility grant must
    be re-approved after an upgrade. If first launch is blocked by Gatekeeper:
    right-click -> Open, or `xattr -dr com.apple.quarantine "#{appdir}/spacelabel.app"`.
  CAVEATS
end
