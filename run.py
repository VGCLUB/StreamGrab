import sys
import json
from pathlib import Path

from streamgrab.ui import launch

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--selfcheck-login":
        from qa.browser_login_smoke import run_check
        try:
            result = {"status": "passed", **run_check()}
        except Exception as exc:
            result = {"status": "failed", "error_type": type(exc).__name__}
        Path(sys.argv[2]).write_text(json.dumps(result, indent=2), encoding="utf-8")
        sys.exit(0 if result["status"] == "passed" else 1)
    sys.exit(launch())
