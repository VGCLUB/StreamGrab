import sys
import json
from pathlib import Path

from streamgrab.ui import launch

if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--verify-video":
        from streamgrab.verification import verify_video
        from streamgrab.engine import public_error
        try:
            result = verify_video(sys.argv[2], Path(sys.argv[3]))
        except Exception as exc:
            result = {"status": "failed", "error_type": type(exc).__name__, "message": public_error(exc)}
        Path(sys.argv[3]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(0 if result["status"] == "passed" else 1)
    if len(sys.argv) == 3 and sys.argv[1] == "--selfcheck-login":
        from qa.browser_login_smoke import run_check
        try:
            result = {"status": "passed", **run_check()}
        except Exception as exc:
            result = {"status": "failed", "error_type": type(exc).__name__}
        Path(sys.argv[2]).write_text(json.dumps(result, indent=2), encoding="utf-8")
        sys.exit(0 if result["status"] == "passed" else 1)
    sys.exit(launch())
