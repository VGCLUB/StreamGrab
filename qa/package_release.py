import hashlib
import json
import shutil
import zipfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streamgrab import __version__


def source_files(root: Path) -> list[Path]:
    """Publish an explicit source allowlist, never a whole working directory."""
    top_level = {
        ".gitignore", ".gitattributes", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
        "使用说明.txt", "启动拾流.cmd", "build.ps1", "StreamGrab.spec", "run.py",
        "requirements.txt", "requirements-dev.txt", "requirements-lock.txt",
    }
    qa_results = {
        "验证报告.md", "live-platform-results.json", "live-download-results.json",
        "live-audio-results.json", "bundled-tool-results.json",
        "browser-login-results.json",
        "douyin-browser-results.json",
    }
    included = []
    for file in root.rglob("*"):
        if not file.is_file():
            continue
        rel = file.relative_to(root)
        if any(p in {".venv", ".git", ".pytest_cache", "__pycache__", "dist", "build", "release", ".publish"} for p in rel.parts):
            continue
        if len(rel.parts) == 1:
            allow = file.name in top_level
        elif rel.parts[0] in {"streamgrab", "tests"}:
            allow = file.suffix == ".py"
        elif rel.parts[0] == "qa":
            allow = len(rel.parts) == 2 and (file.suffix == ".py" or file.name in qa_results)
        elif rel.parts[0] == "assets":
            allow = rel.as_posix() in {"assets/app.svg", "assets/app.ico"}
        elif rel.parts[0] == "licenses":
            allow = file.suffix.lower() not in {".exe", ".dll", ".pyd", ".zip"}
        elif rel.parts[0] == ".github":
            allow = file.suffix.lower() in {".md", ".yml", ".yaml"}
        else:
            allow = False
        if allow:
            included.append(file)
    return sorted(included)


def sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def main():
    root = Path(__file__).resolve().parents[1]
    release = root / "release"
    release.mkdir(exist_ok=True)
    app_root = root / f"dist/StreamGrab-{__version__}"
    assert (app_root / "拾流下载器.exe").exists()
    source = release / f"StreamGrab-{__version__}-source.zip"
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file in source_files(root):
            zf.write(file, (Path("StreamGrab-source") / file.relative_to(root)).as_posix())
    shutil.copy2(source, app_root / source.name)
    archive = release / f"StreamGrab-{__version__}-Windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file in sorted(app_root.rglob("*")):
            if file.is_file():
                zf.write(file, (Path("拾流下载器") / file.relative_to(app_root)).as_posix())
    hashes = {f.name: {"size_bytes": f.stat().st_size, "sha256": sha256(f)} for f in (source, archive)}
    (release / "SHA256.json").write_text(json.dumps(hashes, ensure_ascii=False, indent=2), encoding="utf-8")
    (release / "SHA256SUMS.txt").write_text("".join(f"{data['sha256']}  {name}\n" for name, data in hashes.items()), encoding="utf-8")
    print(json.dumps(hashes, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
