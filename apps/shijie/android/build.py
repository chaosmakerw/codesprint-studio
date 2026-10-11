"""Build the offline Android app with official SDK tools and an app-private signer.

No personal database is read by this command. A personal seed must be explicitly
provided with --seed; the default is the redistributable example seed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
VERSION = "0.4.1"
CODE = "1"
PACKAGE = "io.shijie.learning"
WEB_FILES = ("index.html", "app.js", "style.css", "journey.css", "icon.svg", "offline-store.js")


def safe_clean(path: Path, parent: Path) -> None:
    resolved, boundary = path.resolve(), parent.resolve()
    if not resolved.is_relative_to(boundary) or resolved == boundary:
        raise SystemExit("Unsafe generated-file cleanup path")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--debug", action="store_true", help="Private test build with WebView debugging")
    parser.add_argument("--flavor", choices=("sample", "personal"), default="sample")
    parser.add_argument("--seed", type=Path, help="Explicit local JSON seed; never auto-reads desktop data")
    parser.add_argument("--sdk", type=Path, help="Official Android SDK directory")
    args = parser.parse_args()
    seed = args.seed or PROJECT / "examples" / "mobile-seed.json"
    if args.flavor == "personal" and args.seed is None:
        raise SystemExit("Personal builds require an explicit --seed file")
    if not seed.is_file():
        raise SystemExit("Seed file is missing; prepare an offline example or pass --seed")
    seed_value = json.loads(seed.read_text(encoding="utf-8"))
    if not isinstance(seed_value, dict):
        raise SystemExit("Seed must be a JSON object")
    sdk_value = args.sdk or os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    sdk = Path(sdk_value) if sdk_value else PROJECT.parent / "mobile" / "tools" / "android-sdk"
    tools = sdk / "build-tools" / "36.0.0"
    platform = sdk / "platforms" / "android-36" / "android.jar"
    if not platform.is_file() or not (tools / "aapt2.exe").is_file():
        raise SystemExit("Set --sdk or ANDROID_SDK_ROOT to an SDK with android-36/build-tools 36.0.0")
    mode = "debug" if args.debug else "release"
    build = ROOT / "build" / args.flavor / mode
    build.mkdir(parents=True, exist_ok=True)
    assets = build / "site"
    classes = build / "classes"
    safe_clean(assets, ROOT / "build")
    safe_clean(classes, ROOT / "build")
    for name in WEB_FILES:
        source = PROJECT / "web" / name
        if not source.is_file():
            raise SystemExit(f"Offline web file is missing: {name}")
        shutil.copyfile(source, assets / name)
    (assets / "offline").mkdir()
    shutil.copyfile(seed, assets / "offline" / "seed.json")
    (assets / "prompts").mkdir()
    for source in sorted((PROJECT / "prompts").glob("*.md")):
        shutil.copyfile(source, assets / "prompts" / source.name)
    (assets / "legal").mkdir()
    for name in ("LICENSE", "NOTICE"):
        source = PROJECT / name
        if not source.is_file():
            raise SystemExit(f"Redistribution notice is missing: {name}")
        shutil.copyfile(source, assets / "legal" / name)
    for file in assets.rglob("*"):
        if not file.is_file():
            continue
        content = file.read_bytes()
        home_path_pattern = rb"(?:[A-Z]:[\\/]+Users[\\/]|" + b"/" + b"Users/|" + b"/" + b"home/)"
        if re.search(home_path_pattern, content, re.I):
            raise SystemExit("Personal home path in app assets; sanitize the seed before building")
        if b"-----BEGIN PRIVATE KEY-----" in content or b"-----BEGIN OPENSSH PRIVATE KEY-----" in content:
            raise SystemExit("Private key found in app assets")

    def run(arguments, *, environment=None):
        # AAPT2 on Windows needs relative resource paths when the checkout path is Unicode.
        command = [str(arguments[0])]
        command.extend(os.path.relpath(value, ROOT) if isinstance(value, Path) else str(value) for value in arguments[1:])
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", env=environment)
        if result.returncode:
            print(result.stdout)
            print(result.stderr)
            raise SystemExit(f"Build tool failed: {Path(str(arguments[0])).name}")
        return result.stdout + result.stderr

    java_home = Path(os.environ.get("JAVA_HOME", ""))
    javac = java_home / "bin" / "javac.exe"
    keytool = java_home / "bin" / "keytool.exe"
    if not javac.is_file():
        javac = Path(shutil.which("javac") or "javac")
    if not keytool.is_file():
        keytool = Path(shutil.which("keytool") or "keytool")
    manifest = (ROOT / "AndroidManifest.xml").read_text(encoding="utf-8")
    if args.debug:
        manifest = manifest.replace('android:debuggable="false"', 'android:debuggable="true"')
    manifest_file = build / "AndroidManifest.xml"
    manifest_file.write_text(manifest, encoding="utf-8")
    resource_zip = build / "resources.zip"
    run([tools / "aapt2.exe", "compile", "--dir", ROOT / "res", "-o", resource_zip])
    resource_apk = build / "resources.apk"
    run([tools / "aapt2.exe", "link", "-I", platform, "--manifest", manifest_file,
         "--min-sdk-version", "26", "--target-sdk-version", "36", "--version-code", CODE,
         "--version-name", VERSION, "-o", resource_apk, resource_zip])
    java_sources = sorted((ROOT / "src").rglob("*.java"))
    run([javac, "-encoding", "UTF-8", "-source", "8", "-target", "8", "-bootclasspath",
         str(tools / "core-lambda-stubs.jar") + os.pathsep + str(platform), "-g:source,lines", "-d", classes,
         *java_sources])
    run([tools / "d8.bat", "--min-api", "26", "--lib", platform, "--output", build,
         *sorted(classes.rglob("*.class"))])
    unsigned = build / "unsigned.apk"
    shutil.copyfile(resource_apk, unsigned)
    with zipfile.ZipFile(unsigned, "a") as archive:
        archive.write(build / "classes.dex", "classes.dex", compress_type=zipfile.ZIP_DEFLATED)
        for file in sorted(assets.rglob("*")):
            if file.is_file():
                archive.write(file, "assets/site/" + file.relative_to(assets).as_posix(), compress_type=zipfile.ZIP_DEFLATED)
    aligned = build / "aligned.apk"
    run([tools / "zipalign.exe", "-f", "-p", "4", unsigned, aligned])

    private = ROOT / "private-signing"
    private.mkdir(exist_ok=True)
    keystore, password_file = private / "shijie.p12", private / "keystore-password.txt"
    identity_file = private / "signer-sha256.txt"
    if keystore.exists() != password_file.exists():
        raise SystemExit("Incomplete signing identity; restore the missing original file instead of generating a replacement")
    if identity_file.exists() and not keystore.exists():
        raise SystemExit("Original keystore missing; restore it to preserve upgrade identity")
    if not keystore.exists():
        password_file.write_text(secrets.token_urlsafe(40), encoding="utf-8")
        sign_environment = os.environ.copy()
        sign_environment["SHIJIE_SIGNING_PASSWORD"] = password_file.read_text(encoding="utf-8").strip()
        run([keytool, "-genkeypair", "-keystore", keystore, "-storetype", "PKCS12", "-alias", "shijie",
             "-storepass:env", "SHIJIE_SIGNING_PASSWORD", "-keypass:env", "SHIJIE_SIGNING_PASSWORD",
             "-keyalg", "RSA", "-keysize", "3072", "-validity", "10000", "-dname", "CN=Shijie Offline",
             "-noprompt"], environment=sign_environment)
    sign_environment = os.environ.copy()
    sign_environment["SHIJIE_SIGNING_PASSWORD"] = password_file.read_text(encoding="utf-8").strip()
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    apk = (build if args.debug else dist) / f"shijie-{VERSION}-{args.flavor}{'-debug' if args.debug else ''}.apk"
    run([tools / "apksigner.bat", "sign", "--ks", keystore, "--ks-key-alias", "shijie",
         "--ks-pass", "env:SHIJIE_SIGNING_PASSWORD", "--key-pass", "env:SHIJIE_SIGNING_PASSWORD",
         "--v1-signing-enabled", "false", "--v2-signing-enabled", "true", "--v3-signing-enabled", "true",
         "--v4-signing-enabled", "false", "--out", apk, aligned], environment=sign_environment)
    verification = run([tools / "apksigner.bat", "verify", "--verbose", "--print-certs", apk])
    digest_line = "Signer #1 certificate SHA-256 digest: "
    signer = next((line[len(digest_line):].strip() for line in verification.splitlines() if line.startswith(digest_line)), None)
    if not signer:
        raise SystemExit("Signing certificate verification failed")
    if identity_file.exists() and identity_file.read_text(encoding="utf-8").strip() != signer:
        raise SystemExit("Signing identity changed unexpectedly; do not publish")
    identity_file.write_text(signer + "\n", encoding="utf-8")
    run([tools / "zipalign.exe", "-c", "-p", "4", apk])
    permissions = run([tools / "aapt2.exe", "dump", "permissions", apk])
    if "uses-permission" in permissions:
        raise SystemExit("Offline app unexpectedly requests Android permissions")
    badging = run([tools / "aapt2.exe", "dump", "badging", apk])
    with zipfile.ZipFile(apk) as archive:
        if any(re.search(r"(?:private-signing|password|\.p12$|\.jks$|\.keystore$)", entry) for entry in archive.namelist()):
            raise SystemExit("Private signing files leaked into APK")
        asset_hashes = {name: hashlib.sha256(archive.read(name)).hexdigest()
                        for name in archive.namelist() if name.startswith("assets/site/")}
        dex_hash = hashlib.sha256(archive.read("classes.dex")).hexdigest()
    sha = hashlib.sha256(apk.read_bytes()).hexdigest()
    report = {"apk": apk.name, "sha256": sha, "bytes": apk.stat().st_size, "version": VERSION,
              "versionCode": int(CODE), "package": PACKAGE, "debug": args.debug, "flavor": args.flavor,
              "minAndroid": "8.0 (API 26)", "targetApi": 36, "signerCertificateSHA256": signer,
              "signatureVerified": True, "zipalignPassed": True, "permissions": [],
              "privateSigningExcluded": True, "assetSHA256": asset_hashes, "dexSHA256": dex_hash,
              "seedSHA256": hashlib.sha256(seed.read_bytes()).hexdigest(),
              "badging": badging}
    (build / "apk-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.debug:
        (dist / f"{apk.stem}-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (dist / f"{apk.stem}-SHA256.txt").write_text(f"{sha}  {apk.name}\n", encoding="utf-8")
    print(f"APK verified: {apk.name} ({apk.stat().st_size / 1024 / 1024:.2f} MiB); Android 8+; no permissions")


if __name__ == "__main__":
    main()
