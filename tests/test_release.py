from qa.package_release import source_files


def test_source_archive_excludes_local_history_and_unknown_files(tmp_path):
    files = [
        "run.py", "README.md", "streamgrab/ui.py", "tests/test_core.py", "qa/package_release.py",
        "qa/live-platform-results.json", "assets/app.svg", "licenses/ffmpeg/LICENSE", ".github/workflows/ci.yml",
        "qa/ui-session-results.json", "qa/test-results.xml", "qa/app-stderr.log", "qa/live-downloads/user.mp4",
        "cookies.txt", ".env", "private.json", ".publish/token.txt", ".venv/runtime.py",
        "assets/ffmpeg/ffmpeg.exe", "streamgrab/__pycache__/ui.pyc", "release/old.zip",
    ]
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test", encoding="utf-8")
    selected = {p.relative_to(tmp_path).as_posix() for p in source_files(tmp_path)}
    assert selected == set(files[:9])
