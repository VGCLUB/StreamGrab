"""Verify user-owned browser profile sync with synthetic localhost cookies only."""
import http.server
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streamgrab.browser_login import BrowserLogin, CredentialVault, LoginSite, find_browser


class Fixture(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Set-Cookie", "streamgrab_fixture=synthetic-only; HttpOnly; SameSite=Lax; Max-Age=3600; Path=/")
        self.end_headers()
        self.wfile.write(b"<html><title>StreamGrab local login fixture</title>Fixture only</html>")

    def log_message(self, *_):
        pass


def run_check():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    browser = find_browser()
    with tempfile.TemporaryDirectory(prefix="streamgrab-login-fixture-") as temporary:
        root = Path(temporary)
        site = LoginSite("fixture", f"http://127.0.0.1:{server.server_port}/", ("127.0.0.1",))
        sites = {"fixture": site}
        vault = CredentialVault(root / "vault", sites=sites)
        service = BrowserLogin(root / "browser", sites=sites, vault=vault, executable=browser)
        try:
            service.start("fixture", extra_args=("--headless=new",))
            deadline = time.monotonic() + 12
            while True:
                try:
                    snapshot = service.capture("fixture")
                    break
                except Exception:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(.2)
            assert any(c["name"] == "streamgrab_fixture" and c["httpOnly"] for c in snapshot.cookies)
            vault.save(snapshot, True)
            sealed = (root / "vault/fixture.dat").read_bytes()
            assert b"synthetic-only" not in sealed
            restored = CredentialVault(root / "vault", sites=sites).load("fixture")
            assert restored.cookies == snapshot.cookies
            report = {"browser": browser.name, "profile": "isolated fixture profile", "cookie_scope": "localhost only",
                      "http_only_cookie": "passed", "dpapi_encryption": "passed", "restart_reuse": "passed",
                      "personal_browser_profile": "not accessed", "real_platform_login": "not attempted"}
        finally:
            service.close_all()
            # Chromium flushes its profile asynchronously after Browser.close.
            time.sleep(1)
    server.shutdown()
    server.server_close()
    return report


if __name__ == "__main__":
    report = run_check()
    Path(__file__).with_name("browser-login-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
