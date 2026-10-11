"""Isolated SQLite contracts, controlled sampler histories and owned local HTTP servers."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import random
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_generic_backend import Store, InputError, bundle_fixture
from projectstudy.checks import CheckMixin
from projectstudy.server import make_server, main


class ReliabilityUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / "learner data"
        self.store = Store(self.data)
        draft = self.store.preview_bundle(bundle_fixture())
        self.detail = self.store.commit_bundle(draft["draft_id"], True)
        self.pid = self.detail["project"]["id"]

    def tearDown(self):
        self.temp.cleanup()

    def pack(self, concepts=None):
        return {"schema_version": 2, "project_version": 1,
                "source_hash": self.detail["project"]["source_hash"],
                "concept_updates": concepts or [self.enhanced_concept()]}

    def enhanced_concept(self):
        concept = deepcopy(self.detail["concepts"][0])
        concept["lesson"] += "\n补充：输入、执行机制、返回结果和非法输入。"
        concept["mechanism"] += "\n调用者持有参数，函数计算后返回一个新整数。"
        concept["failures"].append("对整数调用 sum 会出现 TypeError。")
        concept["references"].append({"title": "函数定义", "url": "https://docs.python.org/3/tutorial/controlflow.html#defining-functions", "note": "函数边界。"})
        return concept

    def test_concept_supplement_preserves_answers_questions_identity_source_and_backup(self):
        self.store.submit(self.pid, "sum-0", ["six"])
        self.store.hint(self.pid, "sum-0", 1)
        before = self.store.backup()["data"]
        draft = self.store.preview_pack(self.pid, self.pack())
        self.assertEqual(draft["updated_concept_count"], 1)
        result = self.store.commit_pack(self.pid, draft["draft_id"], True)
        self.assertEqual(result["updated_concept_count"], 1)
        after = self.store.backup()["data"]
        for table in ("projects", "files", "activities", "attempts", "hints"):
            self.assertEqual(after[table], before[table], table)
        self.assertEqual(self.store.project_detail(self.pid)["concepts"][0], self.enhanced_concept())
        backup = self.store.backup()
        self.store.restore(backup, True)
        self.assertEqual(self.store.backup()["data"], backup["data"])

    def test_concept_supplement_rejects_removal_identity_edits_unknown_id_or_extra_fields(self):
        original = self.detail["concepts"][0]
        for field, value in (("id", "not-present"), ("title", "另一个概念"), ("group", "新分类"),
                             ("prerequisites", ["another"]), ("evidence", []), ("why", "删除旧设计理由"),
                             ("lesson", "完全覆盖原讲解"), ("mechanism", "删去运行机制"),
                             ("failures", []), ("references", []), ("executable", "run me")):
            bad = self.enhanced_concept()
            bad[field] = value
            before = self.store.backup()["data"]
            with self.subTest(field=field), self.assertRaises(InputError):
                self.store.preview_pack(self.pid, self.pack([bad]))
            self.assertEqual(self.store.backup()["data"], before)
        missing = deepcopy(original)
        missing.pop("lesson")
        with self.assertRaises(InputError):
            self.store.preview_pack(self.pid, self.pack([missing]))

    def test_preview_binds_existing_concept_and_rejects_atomic_late_conflict(self):
        pack = self.pack()
        new_question = deepcopy(bundle_fixture()["activities"][0])
        new_question["id"] = "supplement-question"
        pack["activities"] = [new_question]
        draft = self.store.preview_pack(self.pid, pack)
        with self.store._connection(True) as db:
            other = deepcopy(self.detail["concepts"][0])
            other["lesson"] += "\n另一位 Agent 已经补充。"
            self.store._insert(db, "concepts", other, self.pid)
        before = self.store.backup()["data"]
        with self.assertRaises(InputError):
            self.store.commit_pack(self.pid, draft["draft_id"], True)
        self.assertEqual(self.store.backup()["data"], before)

    def test_duplicate_concept_update_and_source_hash_mismatch_are_rejected(self):
        pack = self.pack([self.enhanced_concept(), self.enhanced_concept()])
        with self.assertRaises(InputError):
            self.store.preview_pack(self.pid, pack)
        pack = self.pack()
        pack["source_hash"] = "0" * 64
        with self.assertRaises(InputError):
            self.store.preview_pack(self.pid, pack)

    def test_activity_summary_exposes_kind_option_mode_without_answer_key(self):
        for activity in self.store.project_detail(self.pid)["activities"]:
            self.assertEqual(activity["kind"], "predict")
            self.assertEqual(activity["option_mode"], "single")
            self.assertTrue(activity["objective"])
            for key in ("options", "answer_ids", "explanation", "hints"):
                self.assertNotIn(key, activity)

    def test_new_variants_do_not_starve_wrong_and_due_inside_same_topic(self):
        activities = [{"id": f"new-{n}", "concept_id": "large"} for n in range(50)]
        activities += [{"id": "wrong", "concept_id": "large"}, {"id": "due", "concept_id": "large"}]
        latest = {"wrong": {"status": "incorrect", "created_at": "2026-10-11T09:00:00+00:00"},
                  "due": {"status": "correct", "created_at": "2026-10-09T00:00:00+00:00"}}
        with patch("projectstudy.store.now", return_value="2026-10-11T09:01:00+00:00"):
            for seed in range(5):
                selected = CheckMixin._balanced_selection(activities, 4, latest, random.Random(seed))
                self.assertEqual({a["id"] for a in selected if a["id"] in latest}, {"wrong", "due"})
                self.assertEqual(sum(a["id"] not in latest for a in selected), 2)

    def test_single_question_rounds_alternate_review_and_unseen(self):
        self.store.submit(self.pid, "sum-0", ["six"])
        first = self.store.start_check(self.pid, 1, mode="practice")
        second = self.store.start_check(self.pid, 1, mode="practice")
        self.assertEqual(first["questions"][0]["id"], "sum-0")
        self.assertNotEqual(second["questions"][0]["id"], "sum-0")

    def test_topic_coverage_keeps_small_topic_even_with_many_new_and_weak_variants(self):
        activities = [{"id": f"a-{n}", "concept_id": "large"} for n in range(50)] + [{"id": "b", "concept_id": "small"}]
        latest = {"a-0": {"status": "incorrect", "created_at": "2026-10-11T00:00:00+00:00"}}
        for seed in range(10):
            selected = CheckMixin._balanced_selection(activities, 2, latest, random.Random(seed))
            self.assertEqual({a["concept_id"] for a in selected}, {"large", "small"})

    def test_question_angles_rotate_within_same_concept(self):
        activities = [{"id": f"predict-{n}", "concept_id": "same", "kind": "predict"} for n in range(40)]
        activities += [{"id": "trace", "concept_id": "same", "kind": "trace"}, {"id": "debug", "concept_id": "same", "kind": "debug"}]
        for seed in range(10):
            selected = CheckMixin._balanced_selection(activities, 3, {}, random.Random(seed))
            self.assertEqual({a["kind"] for a in selected}, {"predict", "trace", "debug"})

    def test_preview_warns_about_real_quality_issues_without_rejecting_legacy_two_option_tasks(self):
        raw = bundle_fixture(3)
        raw["activities"][0]["explanation"] = ""
        raw["activities"][1]["answer_ids"] = ["five", "six"]
        raw["activities"][2]["options"][1]["text"] = " 5 "
        preview = self.store.preview_bundle(raw)
        warnings = "\n".join(preview["warnings"])
        self.assertIn("缺少解析", warnings)
        self.assertIn("所有选项均为正确答案", warnings)
        self.assertIn("重复选项文本", warnings)
        normal = self.store.preview_bundle(bundle_fixture())
        self.assertFalse(any("题目质量提示" in warning for warning in normal["warnings"]))


class StartupReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "owned data"
        self.server = make_server(port=0, root=self.root, data_dir=self.data)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.temp.cleanup()

    def invoke(self, data=None):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            result = main(["--port", str(self.port), "--data-dir", str(data or self.data), "--open"])
        return result, output.getvalue()

    def test_health_instance_is_opaque_and_stable_for_same_data_folder(self):
        first = self.server.app.route("GET", "/api/health", {}, {})
        from projectstudy.server import Application
        second = Application(self.root, self.data).route("GET", "/api/health", {}, {})
        other = Application(self.root, self.root / "other data").route("GET", "/api/health", {}, {})
        self.assertEqual(first["app"], "project-study")
        self.assertEqual(first["instance_id"], second["instance_id"])
        self.assertNotEqual(first["instance_id"], other["instance_id"])
        self.assertNotIn(str(self.data), json.dumps(first))
        self.assertRegex(first["instance_id"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(first["instance_id"], (self.data / ".instance-id").read_text(encoding="ascii"))

    def test_copying_instance_marker_to_another_directory_does_not_reuse_original(self):
        other = self.root / "copied data"
        other.mkdir()
        (other / ".instance-id").write_bytes((self.data / ".instance-id").read_bytes())
        with patch("projectstudy.server.open_application") as browser:
            result, output = self.invoke(other)
            self.assertEqual(result, 1)
            browser.assert_not_called()
            self.assertIn("--port", output)

    def test_second_launch_reuses_owned_matching_instance_without_new_server(self):
        with patch("projectstudy.server.open_application") as browser, patch("projectstudy.server.make_server") as create:
            result, output = self.invoke()
            self.assertEqual(result, 0)
            create.assert_not_called()
            browser.assert_called_once()
            self.assertIn(f"http://127.0.0.1:{self.port}", output)

    def test_port_taken_by_different_data_folder_is_never_reused(self):
        with patch("projectstudy.server.open_application") as browser:
            result, output = self.invoke(self.root / "different data")
            self.assertEqual(result, 1)
            browser.assert_not_called()
            self.assertIn("--port", output)
            self.assertIn(f"http://127.0.0.1:{self.port}", output)

    def test_second_launch_with_project_does_not_silently_skip_import(self):
        output = io.StringIO()
        with patch("projectstudy.server.open_application") as browser, redirect_stdout(output), redirect_stderr(output):
            result = main(["--port", str(self.port), "--data-dir", str(self.data), "--project", str(self.root / "new project"), "--open"])
        self.assertEqual(result, 1)
        browser.assert_not_called()
        self.assertIn("尚未导入", output.getvalue())

    def test_foreign_app_or_different_version_is_never_reused(self):
        health = self.server.app.route("GET", "/api/health", {}, {})
        for field, value in (("app", "another-service"), ("version", "0.0.0"), ("instance_id", "0" * 64)):
            with self.subTest(field=field), patch("projectstudy.server._running_instance", return_value={**health, field: value}), patch("projectstudy.server.open_application") as browser:
                result, output = self.invoke()
                self.assertEqual(result, 1)
                browser.assert_not_called()
                self.assertIn("--port", output)

    def test_live_owned_port_cannot_be_bound_by_second_server(self):
        with self.assertRaises(OSError):
            make_server(self.port, self.root / "other-data", root=self.root)

    def test_failed_browser_during_reuse_still_reports_running_service(self):
        with patch("projectstudy.server.open_application", side_effect=OSError("unavailable")):
            result, output = self.invoke()
            self.assertEqual(result, 0)
            self.assertIn(f"http://127.0.0.1:{self.port}", output)
            self.assertIn("浏览器", output)

    def test_browser_failure_on_cold_start_does_not_abort_server_and_closes_owned_socket(self):
        owned = unittest.mock.Mock()
        with patch("projectstudy.server.make_server", return_value=owned), patch("projectstudy.server.open_application", side_effect=OSError("unavailable")):
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                result = main(["--port", "65533", "--data-dir", str(self.root / "cold"), "--open"])
            self.assertEqual(result, 0)
            owned.serve_forever.assert_called_once()
            owned.server_close.assert_called_once()
            self.assertIn("浏览器", output.getvalue())


if __name__ == "__main__":
    unittest.main()
