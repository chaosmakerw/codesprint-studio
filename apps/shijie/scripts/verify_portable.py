"""Cold-start the actual portable ZIP via Windows CMD and an isolated temporary DB.

Only this script's child process tree and checked temporary directory are removed.
No user browser, existing server, original project or application data is touched.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from projectstudy import __version__
VERSION = __version__
TEMP_PREFIX = "shijie-v04-portable-"
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(base, route, token="", payload=None, *, json_response=True):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    req = urllib.request.Request(base + route, data=body,
                                 headers={"Origin": base, "X-Study-Token": token, "Content-Type": "application/json"})
    with LOCAL_HTTP.open(req, timeout=5) as response:
        content = response.read()
        if json_response:
            return json.loads(content.decode("utf-8"))
        return content.decode("utf-8"), response.headers.get("Content-Type", "")


def checked_temporary(path):
    resolved = path.resolve()
    assert resolved.parent == Path(tempfile.gettempdir()).resolve(), "Temporary root is not the system test directory"
    assert resolved.name.startswith(TEMP_PREFIX), "Temporary root is not owned by this verification"
    return resolved


def checked_archive(path, destination):
    allowed_dirs = {"src", "web", "prompts", "examples", "tests", "scripts", "docs"}
    allowed_top = {"run.py", "START.cmd", "README.md", "CONTEXT.md", "LICENSE", "NOTICE", ".gitignore", "MANIFEST.json"}
    private_components = {"data", "output", ".qa", ".git", "__pycache__", "private-signing", "browser-profile"}
    personal_home = re.sub(r"[/\\]+", "/", str(Path.home())).casefold()
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None, "ZIP CRC verification failed"
        names = archive.namelist()
        assert len({name.casefold() for name in names}) == len(names), "ZIP contains duplicate Windows paths"
        for item in archive.infolist():
            name = item.filename
            parts = PurePosixPath(name).parts
            assert name.startswith("shijie/") and "\\" not in name and ":" not in name, "ZIP entry is not portable"
            assert all(part not in {"..", ".", ""} for part in parts), "ZIP entry escapes its directory"
            assert stat.S_IFMT(item.external_attr >> 16) != stat.S_IFLNK, "ZIP symbolic links are forbidden"
            assert not private_components.intersection(parts), "ZIP includes a private or generated directory"
            assert not name.lower().endswith((".sqlite3", ".db", ".pyc", ".class", ".apk")), "ZIP includes private/generated binary content"
            assert len(parts) == 2 and parts[1] in allowed_top or len(parts) > 2 and parts[1] in allowed_dirs, "ZIP includes an undeclared top-level path"
            target = (destination / name).resolve()
            assert target.is_relative_to(destination.resolve()), "ZIP extraction target is outside the test root"
            content = archive.read(name)
            normalized = re.sub(r"[/\\]+", "/", content.decode("utf-8", errors="replace")).casefold()
            assert personal_home not in normalized, "ZIP contains a personal machine path"
        manifest = json.loads(archive.read("shijie/MANIFEST.json"))
        assert manifest["version"] == VERSION and manifest["private_data_included"] is False, "Manifest version/privacy mismatch"
        expected_names = {"shijie/" + name for name in manifest["sha256"]} | {"shijie/MANIFEST.json"}
        assert set(names) == expected_names, "Manifest does not cover every ZIP entry"
        for name, digest in manifest["sha256"].items():
            assert re.fullmatch(r"[0-9a-f]{64}", digest), "Manifest contains invalid SHA256"
            assert hashlib.sha256(archive.read("shijie/" + name)).hexdigest() == digest, "ZIP SHA256 verification failed"
        archive.extractall(destination)
        return len(names)


def verify(archive_path, report_path):
    assert os.name == "nt", "This verification requires the real Windows CMD parser"
    temporary = checked_temporary(Path(tempfile.mkdtemp(prefix=TEMP_PREFIX)))
    process = None
    base = ""
    checks = {}
    metadata = {"version": VERSION, "archive": archive_path.name,
                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    error = None
    started_at = time.monotonic()
    try:
        assert archive_path.is_file(), "Portable ZIP does not exist"
        metadata["archive_sha256"] = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        extracted = temporary / "portable app"
        metadata["archive_file_count"] = checked_archive(archive_path, extracted)
        app = extracted / "shijie"
        assert not (app / "data").exists(), "Portable ZIP already contains application data"
        checks["zip_crc_all_hashes_allowlist_and_private_exclusion"] = True
        data_dir = temporary / "isolated data"
        assert data_dir.resolve().is_relative_to(temporary)
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        assert port != 8769, "Verification must not use the user's service port"
        base = "http://127.0.0.1:" + str(port)
        command = f'cmd.exe /d /s /c ""{app / "START.cmd"}" --no-open --port {port} --data-dir "{data_dir}""'
        process = subprocess.Popen(command, cwd=app, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, "STUDY_NO_PAUSE": "1", "PYTHONIOENCODING": "utf-8"},
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 20
        while True:
            assert process.poll() is None, "Actual CMD launcher exited before HTTP readiness"
            try:
                bootstrap = request(base, "/api/bootstrap")
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise AssertionError("Actual CMD launcher did not become HTTP-ready")
                time.sleep(.05)
        token = bootstrap["token"]
        assert bootstrap["version"] == VERSION and bootstrap["projects"] == []
        health = request(base, "/api/health")
        assert health["ok"] is True and health["version"] == VERSION
        assert (data_dir / "study.sqlite3").is_file() and not (app / "data").exists()
        assert not (data_dir / "browser-profile").exists()
        checks["actual_start_cmd_no_browser_empty_isolated_database"] = True
        manifest = request(base, "/api/agent-manifest")
        assert manifest["version"] == VERSION and manifest["write_token"]["header"] == "X-Study-Token"
        assert set(manifest["prompts"]) == {"onboarding", "learning", "reference"}
        for mode, route in manifest["prompts"].items():
            assert route.startswith("/api/coach-prompt?") and "mode=" + mode in route
            assert len(request(base, route)["prompt"]) > 200, "Bundled Agent prompt is empty"
        css, content_type = request(base, "/journey.css", json_response=False)
        assert "text/css" in content_type and len(css) > 200
        checks["agent_discovery_three_prompts_and_journey_css_served"] = True
        try:
            request(base, "/api/bundles/preview", payload={"bundle": {}})
            raise AssertionError("Write without the app token was allowed")
        except urllib.error.HTTPError as failure:
            assert failure.code == 403
        checks["agent_write_still_requires_local_token"] = True
        bundle = json.loads((app / "examples/python-notes/learning-bundle.json").read_text(encoding="utf-8"))
        draft = request(base, "/api/bundles/preview", token, {"bundle": bundle})
        assert request(base, "/api/bootstrap")["projects"] == [], "Uncommitted preview imported a project"
        detail = request(base, "/api/bundles/" + draft["draft_id"] + "/commit", token, {"confirmed": True})
        pid = detail["project"]["id"]
        route = "/api/projects/" + pid
        assert detail["project_summary"]["purpose"] == bundle["project_summary"]["purpose"]
        assert detail["project_summary"]["origin"] == "agent_proposed"
        assert detail["flows"] == bundle["flows"]
        assert all(step.get("methods") for flow in detail["flows"] for step in flow["steps"])
        explicit_skips = [case["skipped_step_ids"] for flow in detail["flows"] for case in flow["trace_cases"]
                          if case.get("skipped_step_ids")]
        assert explicit_skips, "Bundle does not teach explicitly skipped calls"
        assert all("method_id" in frame for flow in detail["flows"] for case in flow["trace_cases"] for frame in case["frames"])
        checks["agent_api_import_summary_methods_and_explicit_stops"] = True
        tasks = {task["id"]: task for task in bundle["activities"]}
        multiple = next(task for task in tasks.values() if 1 < len(task["answer_ids"]) < len(task["options"]))
        metadata["multi_answer_count"] = len(multiple["answer_ids"])
        for variant in ("partial", "exact", "extra"):
            round_data = request(base, route + "/checks/start", token,
                                 {"mode": "practice", "scope": "all", "count": 30, "concept_id": multiple["concept_id"]})
            cid = round_data["check"]["id"]
            questions = round_data["questions"]
            assert any(question["id"] == multiple["id"] for question in questions)
            assert round_data["check"]["question_count"] == len(questions)
            for question in questions:
                assert not {"answer_ids", "explanation", "hints"}.intersection(question)
                task = tasks[question["id"]]
                answer = list(reversed(task["answer_ids"]))
                if task["id"] == multiple["id"]:
                    if variant == "partial":
                        answer = task["answer_ids"][:1]
                    elif variant == "extra":
                        answer += [next(option["id"] for option in task["options"] if option["id"] not in task["answer_ids"])]
                payload = {"activity_id": task["id"], "answer": answer, "guessed": False}
                graded = request(base, route + "/checks/" + cid + "/answer", token, payload)
                assert graded["result"]["correct"] == (variant == "exact" or task["id"] != multiple["id"])
                if task["id"] == multiple["id"]:
                    assert graded["result"]["score"] == (100 if variant == "exact" else 0)
                    assert graded["result"]["explanation"] == task["explanation"]
                    repeated = request(base, route + "/checks/" + cid + "/answer", token,
                                       {**payload, "answer": task["answer_ids"]})
                    assert repeated["already_answered"] and repeated["result"] == graded["result"]
                saved = request(base, route + "/checks/" + cid)
                assert saved["questions"] == questions and saved["check"]["current_index"] == graded["check"]["answered_count"]
            score = round((len(questions) - (variant != "exact")) * 100 / len(questions), 2)
            assert graded["check"]["status"] == "finished" and graded["check"]["score"] == score
            assert graded["check"]["passed"] == (score >= 80)
            checks["practice_multi_" + variant + "_first_answer_and_fixed_denominator"] = True
        assert request(base, route + "/checks/inventory")["wrong"] == 1
        checks["wrong_inventory_uses_latest_answer"] = True
        original_review = {"purpose": "我理解这个程序整理待复习笔记，而不是生产消息队列。",
                           "chain": "enqueue 接收输入，_key 规范化，校验正文，判断重复和容量，最后写入。",
                           "boundary": "空键会在规范化阶段失败，下游去重、容量判断和写入没有执行。",
                           "next_step": "需要脱离提示复现非法输入不改变队列状态。<script>不得执行</script>"}
        settings = request(base, route + "/reviews/settings", token, {"interval_days": 7})
        assert settings["interval_days"] == 7 and settings["due"]
        reflected = request(base, route + "/reviews", token, {"answers": original_review, "assistance": "reference"})
        assert reflected["review"]["answers"] == original_review and reflected["review"]["assistance"] == "reference"
        assert reflected["review"]["status"] == "self_reported" and "score" not in reflected["review"]
        assert reflected["interval_days"] == 7 and reflected["due"] is False
        assert not reflected["history"][0]["stale"]
        checks["seven_day_reflection_keeps_original_words_without_fake_score"] = True
        backup = request(base, "/api/backup")
        assert backup["schema_version"] == 2 and len(backup["data"]["reviews"]) == 1
        restored = request(base, "/api/restore", token, {"backup": backup, "confirmed": True})
        assert restored["restored"]
        assert request(base, "/api/backup")["data"] == backup["data"]
        assert request(base, route + "/reviews")["history"][0]["answers"] == original_review
        assert request(base, route)["flows"] == bundle["flows"]
        checks["backup_restore_keeps_every_record_summary_trace_and_reflection"] = True
    except Exception as failure:
        error = {"kind": type(failure).__name__, "message": str(failure).replace(str(temporary), "<temporary-test-directory>")}
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                                   timeout=10, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
                output, errors = process.communicate(timeout=5)
                assert b"not recognized" not in (output + errors).lower(), "Actual CMD reported command parsing errors"
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=.2):
                            time.sleep(.05)
                    except OSError:
                        break
                else:
                    raise AssertionError("Verification-owned HTTP process is still listening")
                checks["owned_process_tree_closed_and_port_released"] = True
            except Exception as failure:
                error = error or {"kind": type(failure).__name__, "message": "Owned-process cleanup failed"}
        try:
            shutil.rmtree(checked_temporary(temporary))
            checks["resolved_owned_temporary_directory_removed"] = True
        except Exception as failure:
            error = error or {"kind": type(failure).__name__, "message": "Checked temporary-directory cleanup failed"}
        report = {**metadata, "status": "OK" if error is None else "FAILED", "checks": checks,
                  "tests_run": len(checks) + (error is not None), "passed": len(checks),
                  "failures": int(error is not None and error["kind"] == "AssertionError"),
                  "errors": int(error is not None and error["kind"] != "AssertionError"), "skipped": 0,
                  "elapsed_seconds": round(time.monotonic() - started_at, 3),
                  "scope": "Actual release ZIP, Windows CMD launcher and local HTTP with real temporary SQLite; no original project code, model or user data executed."}
        if error:
            report["error"] = error
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: report[key] for key in ("status", "tests_run", "passed", "failures", "errors", "skipped", "elapsed_seconds")}))
        return 0 if error is None else 1


def main():
    parser = argparse.ArgumentParser(description="Verify the current portable ZIP without using personal data")
    parser.add_argument("--archive", type=Path, default=ROOT / f"dist/shijie-{VERSION}-windows.zip")
    parser.add_argument("--report", type=Path, default=ROOT / f"output/portable-{VERSION}-results.json")
    args = parser.parse_args()
    return verify(args.archive.resolve(), args.report.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
