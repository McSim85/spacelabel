cask "spacelabel" do
  version "0.6.1"
  # PLACEHOLDER sha256 -> this cask is NOT installable until the first signed-.app
  # release. The release pipeline (publish.yml `update-cask`) PR-bumps `version` + this
  # `sha256` (of the zipped, ad-hoc-signed spacelabel.app asset) when that release is cut.
  # Until then, build + install the bundle locally with `tools/build_app.sh --sign`.
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

  # `brew uninstall --zap` deep-clean. Keep in sync with `spacelabel uninstall --purge`
  # (install.purge_user_data). Per-shell completion files live outside ~/Library and are
  # removed by `spacelabel uninstall --purge`.
  zap trash: [
    "~/Library/Application Support/spacelabel",
    "~/Library/Caches/spacelabel",
    "~/Library/LaunchAgents/dev.mcsim.spacelabel.plist",
    "~/Library/Logs/spacelabel",
  ]

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
