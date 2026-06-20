# Homebrew formula template for McSim85/homebrew-spacelabel
#
# One-time setup (after the tap repo exists):
#   1. Copy this file to Formula/spacelabel.rb in homebrew-spacelabel
#   2. Set a real url + sha256 for the initial version
#   3. Fill in resource blocks by running:
#        brew update-python-resources Formula/spacelabel.rb
#      (This fetches sha256s for click and all pyobjc-* wheels from PyPI.)
#   4. Commit and push; install via:
#        brew tap McSim85/spacelabel
#        brew install spacelabel
#
# After the initial setup, publish.yml keeps url + sha256 current automatically
# (the HOMEBREW_TAP_ENABLED=true repo variable enables that job).

class Spacelabel < Formula
  include Language::Python::Virtualenv

  desc "Label macOS Spaces (virtual desktops) by their stable UUID — reorder-proof"
  homepage "https://github.com/McSim85/spacelabel"
  url "https://github.com/McSim85/spacelabel/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "PLACEHOLDER_SHA256_RUN_brew_update-python-resources"
  license "MIT"

  # macOS only — PyObjC requires Cocoa/AppKit; spacelabel uses private CGS APIs.
  depends_on :macos
  depends_on "python@3.13"

  # ── Python dependencies ────────────────────────────────────────────────────
  # Populate with:  brew update-python-resources Formula/spacelabel.rb
  # The pyobjc-framework-* wheels are macOS universal2 and install without a
  # compiler.  SkyLight/CGS is never a PyPI dep — it is dlopened at runtime.

  resource "click" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER"
    sha256 "PLACEHOLDER"
  end

  resource "pyobjc-core" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER"
    sha256 "PLACEHOLDER"
  end

  resource "pyobjc-framework-Cocoa" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER"
    sha256 "PLACEHOLDER"
  end

  resource "pyobjc-framework-Quartz" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER"
    sha256 "PLACEHOLDER"
  end

  resource "pyobjc-framework-CoreText" do
    url "https://files.pythonhosted.org/packages/PLACEHOLDER"
    sha256 "PLACEHOLDER"
  end

  # ── Install ────────────────────────────────────────────────────────────────

  def install
    virtualenv_install_with_resources
  end

  # ── Test ───────────────────────────────────────────────────────────────────

  test do
    assert_match version.to_s, shell_output("#{bin}/spacelabel --version")
  end
end
