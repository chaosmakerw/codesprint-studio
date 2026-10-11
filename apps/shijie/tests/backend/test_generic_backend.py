"""Generic learning intake and independent checks against real temporary SQLite."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from projectstudy.analysis import InputError, collect_upload, scan_directory
from projectstudy.server import Application
from projectstudy.store import Store


def bundle_fixture(question_count=5):
    source = "def total(values):\n    return sum(values)\n"
    anchor = {"path": "src/main.py", "line": 2, "quote": "return sum(values)",
              "sha256": hashlib.sha256(source.encode()).hexdigest()}
    concept = {"id": "python-aggregation", "title": "聚合值", "group": "项目基础", "explanation": "把序列求和。",
               "mechanism": "函数接收序列并返回求和结果。", "why": "封装统计规则。", "failures": ["输入不可迭代。"],
               "lesson": "输入 [2,3] 返回 5。<script>alert(1)</script>", "prerequisites": [], "evidence": [anchor],
               "detection": "agent_proposed", "limitations": "尚未运行项目。",
               "references": [{"title": "Python内建函数", "url": "https://docs.python.org/3/library/functions.html#sum", "note": "sum协议出处"}]}
    activities = [{"id": f"sum-{n}", "concept_id": concept["id"], "kind": "predict", "title": f"预测{n}",
                   "prompt": "输入 [2,3] 的返回值是什么？", "options": [{"id": "five", "text": "5"}, {"id": "six", "text": "6"}],
                   "answer_ids": ["five"], "rubric": ["依据函数返回语句。"], "hints": ["从输入看起。", "执行sum。", "2+3。"],
                   "explanation": "sum([2,3])返回5。", "evidence": [anchor]} for n in range(question_count)]
    return {"schema_version": 2, "title": "Python统计练习", "project": {"name": "通用统计项目"},
            "files": [{"path": "src/main.py", "text": source}, {"path": "notes/basics.md", "text": "序列可迭代。\n"}],
            "concepts": [concept], "activities": activities}


class GenericBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def imported(self, count=5):
        draft = self.store.preview_bundle(bundle_fixture(count))
        return self.store.commit_bundle(draft["draft_id"], True)

    def start(self, pid, count=5, **kwargs):
        return self.store.start_check(pid, count, **kwargs)

    def answers(self, started, incorrect=0):
        return [{"activity_id": q["id"], "answer": ["six" if n < incorrect else "five"]} for n, q in enumerate(started["questions"])]

    def test_non_java_upload_waits_for_agent_and_uses_generic_name(self):
        result = self.store.upload(bundle_fixture()["files"])
        self.assertEqual(result["project"]["name"], "我的项目")
        self.assertEqual(result["concepts"], [])
        self.assertEqual(result["activities"], [])

    def test_common_language_sources_and_materials_are_collected(self):
        suffixes = ["java", "py", "js", "ts", "go", "rs", "c", "cpp", "h", "cs", "sql", "html", "css", "md", "txt"]
        files, report = collect_upload([{"path": "src/a." + ext, "text": "a valid text"} for ext in suffixes]
                                      + [{"path": "target/a.py", "text": "build"}, {"path": ".env", "text": "hidden"},
                                         {"path": "src/c.bin", "text": "binary"}, {"path": "src/passwords.py", "text": 'api_key="real-long-credential"'}])
        self.assertEqual(len(files), len(suffixes))
        self.assertEqual(report["skipped"], 4)

    def test_scan_read_only_material_and_python_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("print(2)\n", encoding="utf-8")
            (root / "learning.md").write_text("变量\n", encoding="utf-8")
            files, _, _ = scan_directory(directory)
            self.assertEqual({f["path"] for f in files}, {"app.py", "learning.md"})
            self.assertEqual((root / "app.py").read_text(), "print(2)\n")

    def test_bundle_preview_confirmation_and_plain_text_lesson(self):
        raw = bundle_fixture()
        preview = self.store.preview_bundle(raw)
        self.assertEqual((preview["file_count"], preview["concept_count"], preview["activity_count"]), (2, 1, 5))
        self.assertEqual(self.store.list_projects(), [])
        self.assertNotIn("drafts", self.store.backup()["data"])
        with self.assertRaises(InputError):
            self.store.commit_bundle(preview["draft_id"], False)
        result = self.store.commit_bundle(preview["draft_id"], True)
        self.assertEqual(result["concepts"][0]["lesson"], raw["concepts"][0]["lesson"])
        self.assertEqual(result["summary"]["activity_count"], 5)
        with self.assertRaises(InputError):
            self.store.commit_bundle(preview["draft_id"], True)

    def test_bad_hash_quote_and_cycle_leave_no_partial_import(self):
        for case in ("hash", "quote", "cycle", "missing-prerequisite"):
            raw = bundle_fixture()
            if case == "hash":
                raw["concepts"][0]["evidence"][0]["sha256"] = "0" * 64
            elif case == "quote":
                raw["activities"][0]["evidence"][0]["quote"] = "not-present"
            elif case == "cycle":
                raw["concepts"][0]["prerequisites"] = [raw["concepts"][0]["id"]]
            else:
                raw["concepts"][0]["prerequisites"] = ["not-existing"]
            with self.subTest(case=case), self.assertRaises(InputError):
                self.store.preview_bundle(raw)
            self.assertEqual(self.store.list_projects(), [])

    def test_filtered_sensitive_file_cannot_leave_dangling_evidence(self):
        raw = bundle_fixture()
        raw["files"][0]["path"] = "secrets/main.py"
        raw["concepts"][0]["evidence"][0]["path"] = "secrets/main.py"
        with self.assertRaises(InputError):
            self.store.preview_bundle(raw)
        self.assertEqual(self.store.list_projects(), [])

    def test_reference_scheme_and_concept_count_are_bounded(self):
        raw = bundle_fixture()
        raw["concepts"][0]["references"][0]["url"] = "javascript:alert(1)"
        with self.assertRaises(InputError):
            self.store.preview_bundle(raw)
        raw = bundle_fixture()
        raw["concepts"] = [{**raw["concepts"][0], "id": f"group-{n}", "group": f"分类{n}"} for n in range(9)]
        with self.assertRaises(InputError):
            self.store.preview_bundle(raw)

    def test_full_bundle_accepts_150_activities_but_rejects_151(self):
        full = self.imported(150)
        self.assertEqual(full["summary"]["activity_count"], 150)
        with self.assertRaises(InputError):
            self.store.preview_bundle(bundle_fixture(151))
        raw = bundle_fixture()
        raw["concepts"] = [{**raw["concepts"][0], "id": f"concept-{n}"} for n in range(81)]
        with self.assertRaises(InputError):
            self.store.preview_bundle(raw)

    def test_bundle_commit_capacity_rollback_keeps_database_unchanged(self):
        preview = self.store.preview_bundle(bundle_fixture())
        with patch("projectstudy.store.MAX_BACKUP_BYTES", 200), self.assertRaises(InputError):
            self.store.commit_bundle(preview["draft_id"], True)
        self.assertEqual(self.store.list_projects(), [])
        self.assertEqual(self.store.commit_bundle(preview["draft_id"], True)["summary"]["activity_count"], 5)

    def test_schema2_pack_adds_arbitrary_concept_and_rejects_overwrite(self):
        detail = self.imported()
        pid, project = detail["project"]["id"], detail["project"]
        raw = bundle_fixture()
        concept = {**raw["concepts"][0], "id": "fold-state", "prerequisites": ["python-aggregation"]}
        activity = {**raw["activities"][0], "id": "fold-state-new", "concept_id": "fold-state"}
        pack = {"schema_version": 2, "project_version": 1, "source_hash": project["source_hash"], "concepts": [concept], "activities": [activity]}
        preview = self.store.preview_pack(pid, pack)
        self.assertEqual(preview["concept_count"], 1)
        self.store.commit_pack(pid, preview["draft_id"], True)
        self.assertEqual(self.store.project_detail(pid)["summary"]["concept_count"], 2)
        with self.assertRaises(InputError):
            self.store.preview_pack(pid, pack)

    def test_pack_late_conflict_does_not_overwrite_or_write_other_concepts(self):
        detail = self.imported()
        pid, project = detail["project"]["id"], detail["project"]
        raw = bundle_fixture()
        concept = {**raw["concepts"][0], "id": "new-topic"}
        activity = {**raw["activities"][0], "id": "new-question", "concept_id": "new-topic"}
        pack = {"schema_version": 2, "project_version": 1, "source_hash": project["source_hash"], "concepts": [concept], "activities": [activity]}
        preview = self.store.preview_pack(pid, pack)
        conflicting = {**raw["activities"][0], "id": "new-question", "title": "先保存的题目"}
        with self.store._connection(True) as db:
            self.store._insert(db, "activities", self.store._validate_activities([conflicting], detail["concepts"], self.store.handoff(pid)["source_files"])[0], pid)
        before = self.store.backup()["data"]
        with self.assertRaises(InputError):
            self.store.commit_pack(pid, preview["draft_id"], True)
        self.assertEqual(self.store.backup()["data"], before)
        pack["source_hash"] = "0" * 64
        with self.assertRaises(InputError):
            self.store.preview_pack(pid, pack)

    def test_materials_version_retains_old_answers_and_evidence(self):
        detail = self.imported()
        pid = detail["project"]["id"]
        attempt = self.store.submit(pid, "sum-0", ["five"])["attempt"]
        self.store.submit_evidence(pid, {"attempt_id": attempt["id"], "summary": "手算已完成", "independent": True})
        check = self.start(pid)
        updated = self.store.add_materials(pid, [{"path": "input.md", "text": "输入序列需可迭代。\n"}])
        self.assertEqual(updated["project"]["version"], 2)
        self.assertEqual(updated["summary"]["stale_attempt_count"], 1)
        self.assertEqual(updated["concepts"], [])
        self.assertEqual(updated["activities"], [])
        self.assertIn("materials/input.md", [m["path"] for m in updated["materials"]])
        self.assertTrue(self.store.checks(pid)["checks"][0]["stale"])
        with self.assertRaises(InputError):
            self.store.submit_check(pid, check["check"]["id"], self.answers(check))
        backed = self.store.backup()
        self.assertEqual(len(backed["data"]["evidence"]), 1)
        self.store.restore(backed, True)
        self.assertEqual(self.store.project_detail(pid)["summary"]["stale_attempt_count"], 1)

    def test_materials_repeated_snapshot_has_no_version_change_and_only_text(self):
        pid = self.imported()["project"]["id"]
        items = [{"path": "input.md", "text": "复习资料。"}]
        self.store.add_materials(pid, items)
        repeated = self.store.add_materials(pid, items)
        self.assertEqual(repeated["project"]["version"], 2)
        self.assertFalse(repeated["source_changed"])
        with self.assertRaises(InputError):
            self.store.add_materials(pid, [{"path": "app.py", "text": "print(1)"}])

    def test_finished_check_survives_material_change_as_stale_history(self):
        pid = self.imported()["project"]["id"]
        started = self.start(pid)
        self.store.submit_check(pid, started["check"]["id"], self.answers(started))
        self.store.add_materials(pid, [{"path": "new.md", "text": "新的学习资料。"}])
        self.store.restore(self.store.backup(), True)
        check = self.store.checks(pid)["checks"][0]
        self.assertTrue(check["stale"])
        self.assertEqual(check["status"], "finished")
        self.assertEqual(check["score"], 100)

    def test_check_80_score_is_pass_but_never_mastery(self):
        pid = self.imported()["project"]["id"]
        started = self.start(pid)
        self.assertEqual(len({q["id"] for q in started["questions"]}), 5)
        for q in started["questions"]:
            self.assertNotIn("answer_ids", q)
            self.assertNotIn("explanation", q)
            self.assertEqual({o["id"] for o in q["options"]}, {"five", "six"})
        result = self.store.submit_check(pid, started["check"]["id"], self.answers(started, incorrect=1))
        self.assertEqual(result["check"]["score"], 80)
        self.assertTrue(result["check"]["passed"])
        self.assertTrue(result["check"]["independent"])
        self.assertIn("不证明独立掌握", result["check"]["mastery_claim"])
        self.assertEqual(self.store.project_detail(pid)["summary"]["attempt_count"], 5)
        self.store.restore(self.store.backup(), True)
        self.assertEqual(self.store.checks(pid)["checks"][0]["score"], 80)

    def test_check_60_score_fails_and_later_hints_do_not_rewrite_first_check(self):
        pid = self.imported()["project"]["id"]
        started = self.start(pid)
        result = self.store.submit_check(pid, started["check"]["id"], self.answers(started, incorrect=2))
        self.assertEqual(result["check"]["score"], 60)
        self.assertFalse(result["check"]["passed"])
        self.assertTrue(result["check"]["independent"])
        self.store.hint(pid, "sum-0", 1)
        self.store.restore(self.store.backup(), True)
        self.assertTrue(self.store.checks(pid)["checks"][0]["independent"])

    def test_subjective_questions_remain_review_required_and_outside_checks(self):
        raw = bundle_fixture()
        for a in raw["activities"]:
            a.update(options=[], answer_ids=[])
        draft = self.store.preview_bundle(raw)
        pid = self.store.commit_bundle(draft["draft_id"], True)["project"]["id"]
        with self.assertRaises(InputError):
            self.start(pid)
        attempted = self.store.submit(pid, "sum-0", "我会继续手算并口述。")
        self.assertIsNone(attempted["attempt"]["score"])
        self.assertEqual(attempted["attempt"]["status"], "review_required")

    def test_check_invalid_partial_duplicate_and_resubmit_are_atomic(self):
        pid = self.imported()["project"]["id"]
        started = self.start(pid)
        cid, answers = started["check"]["id"], self.answers(started)
        invalid = deepcopy(answers)
        invalid[-1]["answer"] = ["missing"]
        duplicate = deepcopy(answers)
        duplicate[-1] = duplicate[0]
        for items in (answers[:-1], invalid, duplicate):
            with self.assertRaises(InputError):
                self.store.submit_check(pid, cid, items)
            self.assertEqual(self.store.project_detail(pid)["summary"]["attempt_count"], 0)
        self.store.submit_check(pid, cid, answers)
        with self.assertRaises(InputError):
            self.store.submit_check(pid, cid, answers)
        self.assertEqual(self.store.project_detail(pid)["summary"]["attempt_count"], 5)

    def test_hints_and_prior_answers_make_check_not_first_independent(self):
        pid = self.imported()["project"]["id"]
        self.store.hint(pid, "sum-0", 1)
        first = self.start(pid)
        result = self.store.submit_check(pid, first["check"]["id"], self.answers(first))
        self.assertFalse(result["check"]["independent"])
        second = self.start(pid)
        result2 = self.store.submit_check(pid, second["check"]["id"], self.answers(second))
        self.assertFalse(result2["check"]["independent"])
        self.store.restore(self.store.backup(), True)

    def test_wrong_scope_uses_latest_current_answer_and_small_pool(self):
        pid = self.imported()["project"]["id"]
        self.store.submit(pid, "sum-0", ["six"])
        self.store.submit(pid, "sum-1", ["six"])
        self.store.submit(pid, "sum-1", ["five"])
        started = self.start(pid, count=30, scope="wrong", concept_id="python-aggregation")
        self.assertEqual(started["check"]["question_count"], 1)
        self.assertEqual(started["questions"][0]["id"], "sum-0")

    def test_active_finished_check_backup_and_legacy_restore(self):
        pid = self.imported()["project"]["id"]
        started = self.start(pid)
        backup = self.store.backup()
        self.store.restore(backup, True)
        self.store.submit_check(pid, started["check"]["id"], self.answers(started))
        current = self.store.backup()
        tampered = deepcopy(current)
        tampered["data"]["checks"][0]["data"]["score"] = 20
        with self.assertRaises(InputError):
            self.store.restore(tampered, True)
        self.assertEqual(self.store.backup()["data"], current["data"])
        legacy = deepcopy(current)
        legacy["schema_version"] = 1
        legacy["data"].pop("checks")
        self.store.restore(legacy, True)
        self.assertEqual(self.store.checks(pid), {"checks": []})
        self.assertEqual(self.store.project_detail(pid)["summary"]["attempt_count"], 5)

    def test_handoff_exposes_material_and_generic_schema2_contract(self):
        pid = self.imported()["project"]["id"]
        context = self.store.handoff(pid)
        self.assertEqual(context["schema_version"], 2)
        self.assertEqual(context["project_version"], 1)
        self.assertEqual(context["source_hash"], context["project"]["source_hash"])
        self.assertEqual(context["materials"][0]["path"], "notes/basics.md")
        self.assertIn("lesson", context["concept_contract"]["fields"])

    def test_new_http_route_contracts_use_store_data_only(self):
        app = Application(Path(self.temp.name), Path(self.temp.name) / "route-data")
        preview = app.route("POST", "/api/bundles/preview", {}, {"bundle": bundle_fixture()})
        detail = app.route("POST", f"/api/bundles/{preview['draft_id']}/commit", {}, {"confirmed": True})
        pid = detail["project"]["id"]
        started = app.route("POST", f"/api/projects/{pid}/checks/start", {}, {"count": 5})
        result = app.route("POST", f"/api/projects/{pid}/checks/{started['check']['id']}/submit", {}, {"answers": self.answers(started)})
        self.assertTrue(result["check"]["passed"])
        self.assertEqual(app.route("GET", f"/api/projects/{pid}/checks", {}, {})["checks"][0]["status"], "finished")


if __name__ == "__main__":
    unittest.main()
