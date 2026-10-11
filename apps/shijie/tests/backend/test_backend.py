from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from projectstudy.analysis import InputError, MAX_FILE_BYTES, analyze, collect_upload, relative_path, scan_directory
from projectstudy.server import make_server
from projectstudy.store import Store

SOURCE = "import java.util.Map;\nclass Orders {\n  Long userId;\n  boolean allowed() { return userId != null; }\n}\n"


def fixtures(files, concepts):
    if not concepts:
        return []
    anchor = concepts[0]["evidence"][0]
    base = {"concept_id": concepts[0]["id"], "title": "源码边界练习", "prompt": "根据源码，预测行为并解释证据。",
            "rubric": ["说明变量类型和执行顺序", "区分静态实现与运行证据"],
            "explanation": "这里的标准解释只在提交之后展示。", "hints": ["先看声明类型。", "跟踪方法返回。", "用不同输入核对。"],
            "evidence": [anchor]}
    choice = {**base, "id": "predict-a", "kind": "predict", "options": [{"id": "a", "text": "符合边界"}, {"id": "b", "text": "不符合边界"}], "answer_ids": ["a"]}
    subjective = {**base, "id": "trace-a", "kind": "trace", "options": [], "answer_ids": []}
    transfer = {**choice, "id": "transfer-a", "kind": "transfer", "due_after_hours": 48}
    return [choice, subjective, transfer]


class AnalysisTests(unittest.TestCase):
    def test_relative_path_traversal_rejected(self):
        for value in ("../outside.java", "/outside.java", "C:/secret.java", "a\\b.java", "a//b.java", "a/./b.java"):
            with self.subTest(value=value), self.assertRaises(InputError):
                relative_path(value)

    def test_config_build_and_credentials_excluded(self):
        files, report = collect_upload([{"path": p, "text": SOURCE} for p in ("src/Orders.java", "target/Orders.java", ".git/A.java", ".env", "application.yml", "secrets/Secret.java")])
        self.assertEqual([f["path"] for f in files], ["src/Orders.java"])
        self.assertEqual(report["skipped"], 5)

    def test_secret_content_is_excluded(self):
        secret = "sk-" + "supersecret123456789012345"
        unsafe = 'class Unsafe { String apiKey = "' + secret + '"; }'
        files, _ = collect_upload([{"path": "Good.java", "text": SOURCE}, {"path": "Unsafe.java", "text": unsafe}])
        self.assertEqual([f["path"] for f in files], ["Good.java"])

    def test_java_password_assignment_is_excluded(self):
        files, _ = collect_upload([{"path": "Good.java", "text": SOURCE}, {"path": "Unsafe.java", "text": 'class Unsafe { String password = "long-password-value"; }'}])
        self.assertEqual(len(files), 1)

    def test_oversized_file_skipped_not_truncated(self):
        files, report = collect_upload([{"path": "Good.java", "text": SOURCE}, {"path": "Huge.java", "text": "x" * (MAX_FILE_BYTES + 1)}])
        self.assertEqual(len(files), 1)
        self.assertEqual(report["skipped"], 1)

    def test_duplicate_upload_paths_rejected(self):
        with self.assertRaises(InputError):
            collect_upload([{"path": "A.java", "text": SOURCE}] * 2)

    def test_real_scan_reads_java_and_skips_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "target").mkdir()
            (root / "src" / "A.java").write_text(SOURCE)
            (root / "target" / "A.java").write_text(SOURCE)
            files, report, selected = scan_directory(str(root))
            self.assertEqual([f["path"] for f in files], ["src/A.java"])
            self.assertEqual(report["included"], 1)
            self.assertEqual(selected, str(root.resolve()))

    def test_scan_does_not_modify_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "A.java"
            path.write_text(SOURCE)
            before = path.read_bytes()
            scan_directory(directory)
            self.assertEqual(path.read_bytes(), before)

    def test_detectors_return_real_hash_line_and_static_limit(self):
        files, _ = collect_upload([{"path": "A.java", "text": SOURCE}])
        concepts = analyze(files)
        self.assertIn("java-types", [c["id"] for c in concepts])
        self.assertIn("identity-permission", [c["id"] for c in concepts])
        for c in concepts:
            self.assertEqual(c["detection"], "static_candidate")
            for evidence in c["evidence"]:
                self.assertEqual(evidence["sha256"], hashlib.sha256(SOURCE.encode()).hexdigest())
                self.assertIn(evidence["quote"], SOURCE.splitlines()[evidence["line"] - 1])

    def test_main_operations_rank_above_imports_and_test_mentions(self):
        candidates = [
            {"path": "src/main/java/AAImports.java", "text": "import java.util.Map;\nimport example.security.CurrentUserContext;\nclass AAImports {}"},
            {"path": "src/test/java/AAIdentityTest.java", "text": "class AAIdentityTest { void check(Long userId) { if (userId == null) { return; } } }"},
            {"path": "src/main/java/security/ProviderCurrentUserContext.java", "text": "class ProviderCurrentUserContext {\n  Long getUserId() {\n    return provider.current()\n      .map(AuthenticatedPrincipal::userId)\n      .orElse(null);\n  }\n}"},
            {"path": "src/main/java/order/OrderTools.java", "text": "class OrderTools {\n  void listMyOrders() {\n    Long currentUserId = currentUserContext.getUserId();\n    if (currentUserId == null) { return; }\n  }\n}"},
            {"path": "src/main/java/order/FakeOrderService.java", "text": "class FakeOrderService {\n  boolean belongs(Long currentUserId) {\n    if (currentUserId.equals(order.buyerId())) { return true; }\n    return false;\n  }\n}"},
            {"path": "src/main/java/order/JdbcOrderService.java", "text": "class JdbcOrderService {\n String sql = \"\"\"\n WHERE buyer_id = ?\n \"\"\";\n}"},
        ]
        files, _ = collect_upload(candidates)
        concepts = {c["id"]: c for c in analyze(files)}
        evidence = concepts["identity-permission"]["evidence"]
        first_four = {Path(e["path"]).name for e in evidence[:4]}
        self.assertEqual(first_four, {"ProviderCurrentUserContext.java", "OrderTools.java", "FakeOrderService.java", "JdbcOrderService.java"})
        self.assertIn("provider.current()", next(e["quote"] for e in evidence if e["path"].endswith("ProviderCurrentUserContext.java")))
        self.assertIn("WHERE buyer_id = ?", next(e["quote"] for e in evidence if e["path"].endswith("JdbcOrderService.java")))
        self.assertFalse(concepts["java-types"]["evidence"][0]["quote"].strip().startswith("import "))
        for c in concepts.values():
            self.assertEqual(len({e["path"] for e in c["evidence"]}), len(c["evidence"]))
            self.assertLessEqual(len(c["evidence"]), 8)

    def test_import_only_clue_remains_static_candidate(self):
        files, _ = collect_upload([{"path": "OnlyImport.java", "text": "import java.util.Map;\nclass OnlyImport {}"}])
        concept = next(c for c in analyze(files) if c["id"] == "java-types")
        self.assertEqual(concept["detection"], "static_candidate")
        self.assertTrue(concept["evidence"][0]["quote"].startswith("import "))
        self.assertIn("不证明", concept["limitations"])

    def test_invalid_selected_folder_error_does_not_echo_path(self):
        private = "C:/Users/YOUR_USER/not-a-project"
        with self.assertRaises(InputError) as failure:
            scan_directory(private)
        self.assertNotIn(private, str(failure.exception))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name), fixtures)
        result = self.store.upload([{"path": "Orders.java", "text": SOURCE}], "练习项目")
        self.pid = result["project"]["id"]

    def tearDown(self):
        self.temp.cleanup()

    def pack(self):
        activity = deepcopy(fixtures(*self.source_and_concepts())[0])
        activity["id"] = "agent-predict-new"
        return {"schema_version": 1, "project_version": 1, "title": "新的任务包", "activities": [activity]}

    def source_and_concepts(self):
        backup = self.store.backup()["data"]
        return [r["data"] for r in backup["files"]], [r["data"] for r in backup["concepts"]]

    def evidence_payload(self, aid):
        return {"attempt_id": aid, "summary": "自己跟踪变量并记录了结果。", "test_type": "unit", "tests_run": 2,
                "passed": 2, "failures": 0, "errors": 0, "skipped": 0, "independent": True, "scope": "真实Java对象；未启动Spring或数据库。"}

    def test_answers_not_exposed_before_submission(self):
        detail = self.store.activity_detail(self.pid, "predict-a")
        self.assertNotIn("answer_ids", detail["activity"])
        self.assertNotIn("explanation", detail["activity"])
        self.assertNotIn("hints", detail["activity"])

    def test_choice_is_exactly_graded(self):
        bad = self.store.submit(self.pid, "predict-a", ["b"])["attempt"]
        good = self.store.submit(self.pid, "predict-a", ["a"])["attempt"]
        self.assertEqual((bad["score"], bad["status"]), (0, "incorrect"))
        self.assertEqual((good["score"], good["status"]), (100, "correct"))
        self.assertTrue(good["explanation"])

    def test_first_answer_is_immutable_after_retry(self):
        first = self.store.submit(self.pid, "predict-a", ["b"])["attempt"]
        self.store.submit(self.pid, "predict-a", ["a"])
        detail = self.store.activity_detail(self.pid, "predict-a")
        self.assertEqual(detail["history"][0]["id"], first["id"])
        self.assertEqual(detail["activity"]["first_attempt"]["answer"], ["b"])
        self.assertEqual(len(detail["history"]), 2)

    def test_unknown_option_rejected_no_record(self):
        with self.assertRaises(InputError):
            self.store.submit(self.pid, "predict-a", ["missing"])
        self.assertEqual(self.store.project_detail(self.pid)["summary"]["attempt_count"], 0)

    def test_subjective_answer_never_gets_fake_score(self):
        attempt = self.store.submit(self.pid, "trace-a", "我需要进一步跟踪返回语句。")["attempt"]
        self.assertIsNone(attempt["score"])
        self.assertEqual(attempt["status"], "review_required")

    def test_hint_levels_are_sequential_and_persistent(self):
        with self.assertRaises(InputError):
            self.store.hint(self.pid, "trace-a", 3)
        first = self.store.hint(self.pid, "trace-a", 1)
        self.assertEqual(self.store.hint(self.pid, "trace-a", 1)["id"], first["id"])
        self.store.hint(self.pid, "trace-a", 2)
        attempt = self.store.submit(self.pid, "trace-a", "跟踪结果")["attempt"]
        self.assertEqual(attempt["hint_level"], 2)
        self.assertEqual(len(self.store.activity_detail(self.pid, "trace-a")["hints_requested"]), 2)

    def test_claim_independent_rejected_when_hints_used(self):
        self.store.hint(self.pid, "trace-a", 1)
        aid = self.store.submit(self.pid, "trace-a", "跟踪结果")["attempt"]["id"]
        with self.assertRaises(InputError):
            self.store.submit_evidence(self.pid, self.evidence_payload(aid))

    def test_user_evidence_pending_review_not_mastery(self):
        aid = self.store.submit(self.pid, "trace-a", "我独立跟踪了变量")["attempt"]["id"]
        result = self.store.submit_evidence(self.pid, self.evidence_payload(aid))
        self.assertEqual(result["evidence"]["status"], "review_required")
        self.assertIn("未执行", result["evidence"]["verification"])

    def test_evidence_counts_must_add_up(self):
        aid = self.store.submit(self.pid, "trace-a", "跟踪结果")["attempt"]["id"]
        payload = self.evidence_payload(aid)
        payload["passed"] = 1
        with self.assertRaises(InputError):
            self.store.submit_evidence(self.pid, payload)

    def test_unchanged_rescan_preserves_version_and_history(self):
        self.store.submit(self.pid, "predict-a", ["a"])
        result = self.store.upload([{"path": "Orders.java", "text": SOURCE}], project_id=self.pid)
        self.assertEqual(result["project"]["version"], 1)
        self.assertFalse(result["source_changed"])
        self.assertEqual(result["summary"]["attempt_count"], 1)

    def test_changed_source_marks_old_attempt_stale(self):
        self.store.submit(self.pid, "predict-a", ["a"])
        result = self.store.upload([{"path": "Orders.java", "text": SOURCE + "// changed\n"}], project_id=self.pid)
        self.assertEqual(result["project"]["version"], 2)
        self.assertEqual(result["summary"]["stale_attempt_count"], 1)
        self.assertEqual(self.store.activity_detail(self.pid, "predict-a")["activity"]["status"], "stale")

    def test_delay_starts_after_concept_attempt(self):
        self.assertIsNone(self.store.activity_detail(self.pid, "transfer-a")["activity"]["due_at"])
        self.store.submit(self.pid, "predict-a", ["a"])
        detail = self.store.activity_detail(self.pid, "transfer-a")["activity"]
        self.assertTrue(detail["due_at"])
        self.assertTrue(detail["delay_pending"])
        self.assertFalse(self.store.submit(self.pid, "transfer-a", ["a"])["attempt"]["delayed_retest"])

    def test_draft_needs_explicit_confirmation(self):
        draft = self.store.preview_pack(self.pid, self.pack())
        before = self.store.project_detail(self.pid)["summary"]["activity_count"]
        with self.assertRaises(InputError):
            self.store.commit_pack(self.pid, draft["draft_id"], False)
        self.assertEqual(self.store.project_detail(self.pid)["summary"]["activity_count"], before)
        self.assertEqual(self.store.commit_pack(self.pid, draft["draft_id"], True)["added_count"], 1)

    def test_pack_forged_source_rejected(self):
        pack = self.pack()
        pack["activities"][0]["evidence"][0]["quote"] = "not in actual source"
        with self.assertRaises(InputError):
            self.store.preview_pack(self.pid, pack)

    def test_pack_code_field_rejected(self):
        pack = self.pack()
        pack["activities"][0]["command"] = "erase everything"
        with self.assertRaises(InputError):
            self.store.preview_pack(self.pid, pack)

    def test_pack_wrong_version_rejected(self):
        pack = self.pack()
        pack["project_version"] = 2
        with self.assertRaises(InputError):
            self.store.preview_pack(self.pid, pack)

    def test_backup_restore_keeps_first_answer_hints_and_evidence(self):
        self.store.hint(self.pid, "trace-a", 1)
        aid = self.store.submit(self.pid, "trace-a", "带提示完成")["attempt"]["id"]
        payload = self.evidence_payload(aid)
        payload["independent"] = False
        self.store.submit_evidence(self.pid, payload)
        backup = self.store.backup()
        self.assertTrue(self.store.restore(backup, True)["restored"])
        detail = self.store.activity_detail(self.pid, "trace-a")
        self.assertEqual(detail["history"][0]["answer"], "带提示完成")
        self.assertEqual(len(detail["hints_requested"]), 1)
        self.assertEqual(len(detail["evidence"]), 1)

    def test_backup_restore_rejects_tampered_score_atomically(self):
        self.store.submit(self.pid, "predict-a", ["b"])
        before = self.store.backup()
        broken = deepcopy(before)
        broken["data"]["attempts"][0]["data"]["score"] = 100
        broken["data"]["attempts"][0]["data"]["status"] = "correct"
        with self.assertRaises(InputError):
            self.store.restore(broken, True)
        self.assertEqual(self.store.backup()["data"], before["data"])

    def test_backup_restore_rejects_bad_late_record_atomically(self):
        self.store.submit(self.pid, "predict-a", ["a"])
        before = self.store.backup()
        broken = deepcopy(before)
        broken["data"]["hints"].append({"project_id": self.pid, "data": {"id": "bad", "activity_id": "predict-a", "version": 1, "level": 7}})
        with self.assertRaises(InputError):
            self.store.restore(broken, True)
        self.assertEqual(self.store.backup()["data"], before["data"])

    def test_backup_source_hash_validated(self):
        broken = deepcopy(self.store.backup())
        broken["data"]["files"][0]["data"]["text"] += "changed"
        with self.assertRaises(InputError):
            self.store.restore(broken, True)

    def test_backup_capacity_rejection_rolls_back_write(self):
        before = self.store.backup()["data"]
        with patch("projectstudy.store.MAX_BACKUP_BYTES", 100), self.assertRaises(InputError):
            self.store.submit(self.pid, "trace-a", "会超过模拟安全容量的记录")
        self.assertEqual(self.store.backup()["data"], before)

    def test_backup_and_handoff_do_not_include_selected_root(self):
        with self.store._connection(True) as db:
            p = self.store._project(db, self.pid)
            p["root"] = "C:/Users/YOUR_USER/JavaProject"
            self.store._insert(db, "projects", p)
        self.assertNotIn("YOUR_USER", json.dumps(self.store.backup()))
        self.assertNotIn("YOUR_USER", json.dumps(self.store.handoff(self.pid)))
        self.assertEqual(self.store.handoff(self.pid)["source_files"][0]["text"], SOURCE)

    def test_concurrent_answers_append_without_loss(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            records = list(executor.map(lambda _: self.store.submit(self.pid, "predict-a", ["a"]), range(8)))
        self.assertEqual(len({r["attempt"]["id"] for r in records}), 8)
        self.assertEqual(self.store.project_detail(self.pid)["summary"]["attempt_count"], 8)

    def test_delete_only_removes_app_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "A.java"
            p.write_text(SOURCE)
            result = self.store.scan(directory)
            self.store.delete_project(result["project"]["id"], True)
            self.assertEqual(p.read_text(), SOURCE)

    def test_launch_import_does_not_silently_rescan(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "A.java"
            p.write_text(SOURCE)
            result = self.store.import_selected_once(directory)
            p.write_text(SOURCE + "// changed")
            again = self.store.import_selected_once(directory)
            self.assertEqual(again["project"]["version"], 1)
            self.assertEqual(again["project"]["id"], result["project"]["id"])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "web").mkdir()
        (self.root / "web" / "index.html").write_text("<!doctype html><title>test</title>")
        example = self.root / "examples" / "java-demo"
        example.mkdir(parents=True)
        (example / "A.java").write_text(SOURCE)
        self.server = make_server(port=0, data_dir=self.root / "data", root=self.root, curriculum_builder=fixtures)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.token = self.request("GET", "/api/bootstrap")[1]["token"]

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Content-Type": "application/json"}
        if method != "GET":
            h["X-Study-Token"] = self.server.app.token
        h.update(headers or {})
        conn.request(method, path, json.dumps(body or {}) if body is not None else None, h)
        response = conn.getresponse()
        payload = response.read()
        content = json.loads(payload) if response.getheader("Content-Type", "").startswith("application/json") else payload.decode()
        result = response.status, content, dict(response.getheaders())
        conn.close()
        return result

    def test_loopback_bootstrap_is_real_http(self):
        status, result, _ = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(result["projects"], [])
        self.assertGreater(result["limits"]["max_backup_bytes"], 0)

    def test_write_without_valid_token_rejected(self):
        status, _, _ = self.request("POST", "/api/projects/demo", {}, {"X-Study-Token": "wrong"})
        self.assertEqual(status, 403)
        self.assertEqual(self.server.app.store.list_projects(), [])

    def test_cross_origin_write_rejected(self):
        self.assertEqual(self.request("POST", "/api/projects/demo", {}, {"Origin": "https://malicious.example"})[0], 403)

    def test_cross_site_bootstrap_rejected(self):
        self.assertEqual(self.request("GET", "/api/bootstrap", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_host_rebinding_rejected(self):
        self.assertEqual(self.request("GET", "/api/bootstrap", headers={"Host": f"attacker.example:{self.port}"})[0], 403)

    def test_static_security_headers(self):
        status, html, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("<!doctype", html)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_static_traversal_denied(self):
        self.assertEqual(self.request("GET", "/../run.py")[0], 404)

    def test_demo_import_answer_and_hint_real_http(self):
        status, result, _ = self.request("POST", "/api/projects/demo", {})
        self.assertEqual(status, 200)
        pid = result["project"]["id"]
        self.assertEqual(self.request("POST", f"/api/projects/{pid}/hints", {"activity_id": "trace-a", "level": 1})[0], 200)
        status, answer, _ = self.request("POST", f"/api/projects/{pid}/attempts", {"activity_id": "trace-a", "answer": "自己的解释"})
        self.assertEqual(status, 200)
        self.assertEqual(answer["attempt"]["status"], "review_required")
        self.assertEqual(answer["attempt"]["hint_level"], 1)

    def test_upload_bad_path_rejected(self):
        status, _, _ = self.request("POST", "/api/projects/upload", {"name": "不安全项目", "files": [{"path": "../../secret.java", "text": SOURCE}]})
        self.assertEqual(status, 400)

    def test_restore_confirmation_is_required(self):
        backup = self.request("GET", "/api/backup")[1]
        self.assertEqual(self.request("POST", "/api/restore", {"backup": backup, "confirmed": False})[0], 400)


if __name__ == "__main__":
    unittest.main()
