"""Build a source-only portable archive; never include local projects or answers."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = ("run.py", "START.cmd", "README.md", "CONTEXT.md", "LICENSE", "NOTICE", ".gitignore")
SOURCE_DIRS = ("src", "web", "prompts", "examples", "tests", "scripts", "docs")


def build():
    files = [ROOT / name for name in TOP_FILES]
    for name in SOURCE_DIRS:
        for candidate in sorted((ROOT / name).rglob("*")):
            if candidate.is_symlink():
                raise ValueError("Portable sources must not contain symbolic links.")
            if candidate.is_file() and "__pycache__" not in candidate.parts and candidate.suffix not in {".pyc", ".class"}:
                files.append(candidate)
    if not all(candidate.is_file() for candidate in files):
        raise ValueError("A required portable source is missing.")
    private_home = str(Path.home()).replace("\\", "/").casefold()
    payloads = {}
    for candidate in files:
        relative = candidate.relative_to(ROOT).as_posix()
        content = candidate.read_bytes()
        if candidate.suffix.lower() == ".cmd":
            content.decode("ascii")
            if b"\n" in content.replace(b"\r\n", b""):
                raise ValueError("Windows launchers must use CRLF line endings.")
        if private_home in content.decode("utf-8", errors="replace").replace("\\", "/").casefold():
            raise ValueError("A private machine path was found in a source file.")
        payloads[relative] = content
    version_match = re.search(r'__version__\s*=\s*"([0-9.]+)"', (ROOT / "src/projectstudy/__init__.py").read_text(encoding="utf-8"))
    if not version_match:
        raise ValueError("The app version is missing.")
    version = version_match.group(1)
    manifest = {"version": version, "created_at": datetime.now(timezone.utc).isoformat(),
                "private_data_included": False,
                "sha256": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(payloads.items())}}
    destination = ROOT / "dist" / f"shijie-{version}-windows.zip"
    destination.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(payloads.items()):
            archive.writestr("shijie/" + name, content)
        archive.writestr("shijie/MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("Portable archive failed its CRC check.")
        for name, expected in manifest["sha256"].items():
            if hashlib.sha256(archive.read("shijie/" + name)).hexdigest() != expected:
                raise ValueError("Portable archive content failed its SHA check.")
        if len(archive.namelist()) != len(payloads) + 1:
            raise ValueError("Portable archive contains unexpected files.")
    print(f"Portable ZIP: {destination.name}; files: {len(payloads) + 1}; private data: excluded; integrity: OK")


if __name__ == "__main__":
    build()
