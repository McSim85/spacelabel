"""macOS platform-integration layer — the only place private APIs are touched.

Groups the CGS read path (:mod:`spacelabel.platform.cgs`), display topology
(:mod:`spacelabel.platform.displays`), the plist fallback parser
(:mod:`spacelabel.platform.spaces_plist`), space-change / display-change
observation (:mod:`spacelabel.platform.notifications`), and an optional
``os_log`` handler (:mod:`spacelabel.platform.oslog_handler`).

Note: this subpackage is named ``platform``; under Python's absolute imports it
does not shadow the stdlib ``platform`` module (``import platform`` still resolves
to the standard library).
"""
