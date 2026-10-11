"""Four-stage contracts exercise real temporary SQLite; uploaded code is never run."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_generic_backend import InputError, Store, bundle_fixture
from test_flows import scene_bundle
from projectstudy.server import Application


def summary_fixture(raw):
    return {"purpose": "统计序列的合计。", "audience": "需要简单汇总的使用者。", "scenario": "输入 [2,3] 得到 5。",
            "capabilities": ["序列求和"], "learning_value": "理解参数绑定、函数调用与返回。",
            "limitations": "源码教学推演，未证明真实部署或独立掌握。", "evidence": raw["concepts"][0]["evidence"]}


def enhanced_bundle():
    raw = scene_bundle()
    raw["project_summary"] = summary_fixture(raw)
    step = raw["flows"][0]["steps"][1]
    step["methods"] = [{"id": "sum-call", "signature": "sum(values: iterable[int]) -> int", "input": "values=[2,3]",
                        "output": "5", "relation": "call", "why": "对可迭代序列求和。", "evidence": step["evidence"]},
                       {"id": "total-return", "signature": "total(values) -> int", "input": "sum结果5",
                        "output": "返回5给调用方", "relation": "return", "why": "将函数结果返回调用方。", "evidence": step["evidence"]}]
    raw["flows"][0]["trace_cases"][0]["frames"][1]["method_id"] = "total-return"
    return raw


class FourStageLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def imported(self, raw=None):
        draft = self.store.preview_bundle(raw or enhanced_bundle())
        return self.store.commit_bundle(draft["draft_id"], True)

    @staticmethod
    def review_answers():
        return {"purpose": "用于汇总序列。", "chain": "total 接收 values，再调用 sum，return 返回结果。",
                "boundary": "整数不可迭代，sum 抛 TypeError，不返回合计。", "next_step": "脱离提示复现正常和非法输入。"}

    def test_summary_methods_trace_reopen_and_backup_round_trip(self):
        raw = enhanced_bundle()
        detail = self.imported(raw)
        pid = detail["project"]["id"]
        self.assertEqual(detail["project_summary"]["purpose"], raw["project_summary"]["purpose"])
        self.assertEqual(detail["project_summary"]["origin"], "agent_proposed")
        self.assertEqual(detail["flows"], raw["flows"])
        self.store.restore(self.store.backup(), True)
        self.assertEqual(Store(Path(self.temp.name)).project_detail(pid)["flows"], raw["flows"])

    def test_summary_forged_evidence_rejects_atomic_import(self):
        raw = enhanced_bundle()
        raw["project_summary"] = deepcopy(raw["project_summary"])
        raw["project_summary"]["evidence"][0]["quote"] = "not in file"
        with self.assertRaises(InputError):
            self.store.preview_bundle(raw)
        self.assertEqual(self.store.list_projects(), [])

    def test_activity_summary_exposes_objective_availability_without_answers(self):
        raw = enhanced_bundle()
        detail = self.imported(raw)
        original = {activity["id"]: activity for activity in raw["activities"]}
        self.assertTrue(any(activity["objective"] for activity in detail["activities"]))
        for activity in detail["activities"]:
            source = original[activity["id"]]
            self.assertEqual(activity["objective"], bool(source.get("options") and source.get("answer_ids")))
            for private_field in ("options", "answer_ids", "explanation", "hints"):
                self.assertNotIn(private_field, activity)

    def test_missing_summary_uses_real_readme_or_explicit_unknown(self):
        detail = self.imported(bundle_fixture())
        self.assertEqual(detail["project_summary"]["origin"], "missing")
        self.assertIn("尚无", detail["project_summary"]["purpose"])
        raw = bundle_fixture()
        raw["files"].append({"path": "README.md", "text": "# 汇总程序\n用于汇总序列。\n"})
        detail = self.imported(raw)
        self.assertEqual(detail["project_summary"]["origin"], "readme_fallback")
        self.assertIn("用于汇总序列", detail["project_summary"]["purpose"])
        self.assertNotIn("真实数据库", detail["project_summary"]["purpose"])

    def test_unknown_method_frame_and_forged_method_evidence_rejected(self):
        for variant in ("unknown", "quote", "relation"):
            raw = enhanced_bundle()
            flow = raw["flows"][0]
            if variant == "unknown":
                flow["trace_cases"][0]["frames"][1]["method_id"] = "missing"
            elif variant == "quote":
                flow["steps"][1]["methods"][0]["evidence"] = deepcopy(flow["steps"][1]["evidence"])
                flow["steps"][1]["methods"][0]["evidence"][0]["quote"] = "made-up call"
            else:
                flow["steps"][1]["methods"][0]["relation"] = "execute"
            with self.subTest(variant=variant), self.assertRaises(InputError):
                self.store.preview_bundle(raw)

    def test_explicit_skipped_steps_need_branch_and_cannot_be_traced(self):
        raw = enhanced_bundle()
        case = raw["flows"][0]["trace_cases"][0]
        raw["flows"][0]["branches"][0]["skipped_step_ids"] = ["aggregate"]
        case["frames"] = case["frames"][:1]
        case["skipped_step_ids"] = ["aggregate"]
        detail = self.imported(raw)
        self.assertEqual(detail["flows"][0]["trace_cases"][0]["skipped_step_ids"], ["aggregate"])
        bad = deepcopy(raw)
        bad["flows"][0]["branches"][0]["skipped_step_ids"] = []
        with self.assertRaises(InputError):
            self.store.preview_bundle(bad)
        bad = enhanced_bundle()
        bad["flows"][0]["trace_cases"][0]["skipped_step_ids"] = ["aggregate"]
        with self.assertRaises(InputError):
            self.store.preview_bundle(bad)

    def test_complete_long_trace_keeps_more_than_forty_frames_in_backup(self):
        raw = enhanced_bundle()
        case = raw["flows"][0]["trace_cases"][0]
        original = deepcopy(case["frames"])
        case["frames"] = [deepcopy(original[n % 2]) for n in range(80)]
        detail = self.imported(raw)
        pid = detail["project"]["id"]
        self.assertEqual(len(detail["flows"][0]["trace_cases"][0]["frames"]), 80)
        self.store.restore(self.store.backup(), True)
        self.assertEqual(self.store.project_detail(pid)["flows"][0]["trace_cases"][0]["frames"], case["frames"])

    def test_flow_methods_and_summary_can_supplement_without_losing_answers(self):
        detail = self.imported(scene_bundle())
        pid = detail["project"]["id"]
        self.store.submit(pid, "sum-0", ["five"])
        raw = enhanced_bundle()
        pack = {"schema_version": 2, "project_version": 1, "source_hash": detail["project"]["source_hash"],
                "project_summary": raw["project_summary"], "flow_updates": raw["flows"]}
        draft = self.store.preview_pack(pid, pack)
        saved = self.store.commit_pack(pid, draft["draft_id"], True)
        self.assertEqual(saved["updated_flow_count"], 1)
        self.assertEqual(self.store.project_detail(pid)["summary"]["attempt_count"], 1)
        self.assertEqual(self.store.project_detail(pid)["flows"], raw["flows"])
        pack["flow_updates"][0]["steps"].reverse()
        with self.assertRaises(InputError):
            self.store.preview_pack(pid, pack)

    def test_late_flow_change_blocks_supplement_without_overwriting_other_agent_work(self):
        detail = self.imported(scene_bundle())
        pid = detail["project"]["id"]
        pack = {"schema_version": 2, "project_version": 1, "source_hash": detail["project"]["source_hash"],
                "flow_updates": enhanced_bundle()["flows"]}
        draft = self.store.preview_pack(pid, pack)
        with self.store._connection(True) as db:
            flow = self.store._rows(db, "flows", pid)[0]
            flow["summary"] = "另一位 Agent 在预览后补充的有效说明。"
            self.store._insert(db, "flows", flow, pid)
        before = self.store.backup()["data"]
        with self.assertRaises(InputError):
            self.store.commit_pack(pid, draft["draft_id"], True)
        self.assertEqual(self.store.backup()["data"], before)

    def test_summary_and_reviews_remain_historical_on_source_change(self):
        pid = self.imported()["project"]["id"]
        self.store.submit_review(pid, self.review_answers())
        self.store.add_materials(pid, [{"path": "new.md", "text": "补充资料。"}])
        detail = self.store.project_detail(pid)
        self.assertEqual(detail["project_summary"]["origin"], "missing")
        self.assertTrue(detail["review_state"]["due"])
        self.assertTrue(detail["review_state"]["history"][0]["stale"])
        backup = self.store.backup()
        self.assertEqual(len(backup["data"]["project_summaries"]), 1)
        self.store.restore(backup, True)
        self.assertEqual(self.store.backup()["data"], backup["data"])

    def test_periodic_review_due_interval_help_and_raw_text(self):
        pid = self.imported()["project"]["id"]
        initial = self.store.reviews(pid)
        self.assertTrue(initial["due"])
        self.assertEqual(initial["interval_days"], 3)
        answers = self.review_answers()
        answers["purpose"] += " <script>alert(1)</script>"
        saved = self.store.submit_review(pid, answers, "agent")
        self.assertFalse(saved["due"])
        self.assertEqual(saved["review"]["answers"], answers)
        self.assertNotIn("score", saved["review"])
        self.assertEqual(saved["review"]["assistance"], "agent")
        self.store.set_review_interval(pid, 7)
        date = datetime.fromisoformat(saved["review"]["created_at"]) + timedelta(days=6)
        with patch("projectstudy.store.now", return_value=date.isoformat()):
            self.assertFalse(self.store.reviews(pid)["due"])
        with patch("projectstudy.store.now", return_value=(date + timedelta(days=1)).isoformat()):
            self.assertTrue(self.store.reviews(pid)["due"])
        self.store.restore(self.store.backup(), True)
        self.assertEqual(self.store.reviews(pid)["interval_days"], 7)

    def test_review_invalid_interval_or_partial_answer_has_no_write(self):
        pid = self.imported()["project"]["id"]
        before = self.store.backup()["data"]
        for invalid in (0, 31, True, "3"):
            with self.subTest(interval=invalid), self.assertRaises(InputError):
                self.store.set_review_interval(pid, invalid)
        with self.assertRaises(InputError):
            self.store.submit_review(pid, {"purpose": "不完整"})
        self.assertEqual(self.store.backup()["data"], before)

    def test_tampered_review_mastery_or_current_hash_cannot_restore(self):
        pid = self.imported()["project"]["id"]
        self.store.submit_review(pid, self.review_answers())
        before = self.store.backup()
        for variant in ("claim", "hash", "extra-score"):
            bad = deepcopy(before)
            record = bad["data"]["reviews"][0]["data"]
            if variant == "claim":
                record["mastery_claim"] = "已独立掌握"
            elif variant == "hash":
                record["source_hash"] = "0" * 64
            else:
                record["score"] = 100
            with self.subTest(variant=variant), self.assertRaises(InputError):
                self.store.restore(bad, True)
            self.assertEqual(self.store.backup()["data"], before["data"])

    def test_practice_first_answer_is_idempotent_and_partial_backup_restores(self):
        pid = self.imported()["project"]["id"]
        started = self.store.start_check(pid, 5, mode="practice")
        cid = started["check"]["id"]
        question = started["questions"][0]
        self.assertNotIn("answer_ids", question)
        self.assertNotIn("explanation", question)
        first = self.store.answer_check(pid, cid, question["id"], ["six"])
        self.assertFalse(first["result"]["correct"])
        self.assertIn("sum", first["result"]["explanation"])
        second = self.store.answer_check(pid, cid, question["id"], ["five"])
        self.assertTrue(second["already_answered"])
        self.assertEqual(second["result"], first["result"])
        self.assertEqual(self.store.project_detail(pid)["summary"]["attempt_count"], 1)
        backup = self.store.backup()
        self.store.restore(backup, True)
        self.assertEqual(self.store.checks(pid)["checks"][0]["answered_count"], 1)
        for q in started["questions"][1:]:
            result = self.store.answer_check(pid, cid, q["id"], ["five"])
        self.assertEqual(result["check"]["score"], 80)
        self.assertTrue(result["check"]["passed"])
        self.assertEqual(result["check"]["question_count"], 5)
        self.store.restore(self.store.backup(), True)

    def test_reopened_check_keeps_question_order_and_only_answered_feedback(self):
        pid = self.imported()["project"]["id"]
        started = self.store.start_check(pid, 5, mode="practice")
        cid = started["check"]["id"]
        self.store.answer_check(pid, cid, started["questions"][0]["id"], ["five"])
        reopened = Store(Path(self.temp.name)).check_detail(pid, cid)
        self.assertEqual(reopened["questions"], started["questions"])
        self.assertEqual(reopened["check"]["current_index"], 1)
        self.assertEqual(len(reopened["check"]["results"]), 1)
        self.assertFalse(reopened["check"]["stale"])
        for question in reopened["questions"]:
            self.assertNotIn("answer_ids", question)
            self.assertNotIn("explanation", question)
            self.assertNotIn("hints", question)
        other = self.imported()["project"]["id"]
        with self.assertRaises(InputError):
            self.store.check_detail(other, cid)

    def test_stale_check_is_readable_but_cannot_resume_submission(self):
        pid = self.imported()["project"]["id"]
        started = self.store.start_check(pid, 5, mode="practice")
        cid = started["check"]["id"]
        self.store.answer_check(pid, cid, started["questions"][0]["id"], ["six"])
        self.store.add_materials(pid, [{"path": "new.md", "text": "新版本资料。"}])
        restored = self.store.check_detail(pid, cid)
        self.assertTrue(restored["check"]["stale"])
        self.assertEqual(restored["questions"], started["questions"])
        self.assertFalse(restored["check"]["results"][0]["correct"])
        with self.assertRaises(InputError):
            self.store.answer_check(pid, cid, started["questions"][1]["id"], ["five"])

    def test_practice_skip_invalid_answer_and_exam_endpoint_rejected_atomically(self):
        pid = self.imported()["project"]["id"]
        started = self.store.start_check(pid, 5, mode="practice")
        cid = started["check"]["id"]
        with self.assertRaises(InputError):
            self.store.answer_check(pid, cid, started["questions"][1]["id"], ["five"])
        with self.assertRaises(InputError):
            self.store.answer_check(pid, cid, started["questions"][0]["id"], ["no-option"])
        with self.assertRaises(InputError):
            self.store.submit_check(pid, cid, [])
        self.assertEqual(self.store.project_detail(pid)["summary"]["attempt_count"], 0)
        exam = self.store.start_check(pid, 1)
        with self.assertRaises(InputError):
            self.store.answer_check(pid, exam["check"]["id"], exam["questions"][0]["id"], ["five"])

    def test_multiple_answers_require_exact_set_without_partial_credit(self):
        raw = bundle_fixture(1)
        raw["activities"][0].update(options=[{"id": "a", "text": "2"}, {"id": "b", "text": "3"}, {"id": "c", "text": "5"}], answer_ids=["a", "b"])
        pid = self.imported(raw)["project"]["id"]
        for answer, expected in ((["a"], False), (["a", "b", "c"], False), (["b", "a"], True)):
            check = self.store.start_check(pid, 1, mode="practice")
            result = self.store.answer_check(pid, check["check"]["id"], "sum-0", answer)
            self.assertEqual(result["result"]["correct"], expected)

    def test_guessed_correct_is_not_independent_and_becomes_due(self):
        pid = self.imported(bundle_fixture(1))["project"]["id"]
        started = self.store.start_check(pid, 1, mode="practice")
        result = self.store.answer_check(pid, started["check"]["id"], "sum-0", ["five"], True)
        self.assertTrue(result["result"]["correct"])
        self.assertTrue(result["result"]["needs_review"])
        self.assertFalse(result["check"]["independent"])
        with self.assertRaises(InputError):
            self.store.start_check(pid, 5, scope="due", mode="practice")
        first_date = datetime.fromisoformat(self.store.backup()["data"]["attempts"][0]["data"]["created_at"])
        with patch("projectstudy.store.now", return_value=(first_date + timedelta(minutes=11)).isoformat()):
            due = self.store.start_check(pid, 5, scope="due", mode="practice")
            self.assertEqual(due["check"]["question_count"], 1)
            self.assertEqual(self.store.quiz_inventory(pid)["due"], 1)
        self.store.restore(self.store.backup(), True)

    def test_practice_answer_after_source_update_is_blocked_but_snapshot_retained(self):
        pid = self.imported()["project"]["id"]
        started = self.store.start_check(pid, 5, mode="practice")
        self.store.answer_check(pid, started["check"]["id"], started["questions"][0]["id"], ["five"])
        self.store.add_materials(pid, [{"path": "input.md", "text": "新的资料。"}])
        with self.assertRaises(InputError):
            self.store.answer_check(pid, started["check"]["id"], started["questions"][1]["id"], ["five"])
        self.store.restore(self.store.backup(), True)
        self.assertTrue(self.store.checks(pid)["checks"][0]["stale"])

    def test_forged_partial_practice_result_cannot_restore_or_replace_records(self):
        pid = self.imported()["project"]["id"]
        started = self.store.start_check(pid, 5, mode="practice")
        self.store.answer_check(pid, started["check"]["id"], started["questions"][0]["id"], ["six"])
        before = self.store.backup()
        bad = deepcopy(before)
        bad["data"]["checks"][0]["data"]["results"][0]["correct"] = True
        with self.assertRaises(InputError):
            self.store.restore(bad, True)
        self.assertEqual(self.store.backup()["data"], before["data"])

    def test_topic_rotation_covers_small_concept_instead_of_large_duplicate_pool(self):
        raw = bundle_fixture(20)
        raw["concepts"].append({**raw["concepts"][0], "id": "small-topic", "title": "小知识点"})
        raw["activities"][19]["concept_id"] = "small-topic"
        pid = self.imported(raw)["project"]["id"]
        for _ in range(10):
            started = self.store.start_check(pid, 2, mode="practice")
            self.assertEqual({q["concept_id"] for q in started["questions"]}, {"python-aggregation", "small-topic"})

    def test_legacy_backups_missing_new_tables_restore_and_delete_cleans_only_project(self):
        pid = self.imported(bundle_fixture())["project"]["id"]
        current = self.store.backup()
        for table in ("project_summaries", "reviews", "review_settings"):
            current["data"].pop(table)
        self.store.restore(current, True)
        self.assertTrue(self.store.reviews(pid)["due"])
        self.store.submit_review(pid, self.review_answers())
        self.store.set_review_interval(pid, 5)
        other = self.imported()["project"]["id"]
        self.store.delete_project(pid, True)
        backup = self.store.backup()["data"]
        self.assertEqual(backup["reviews"], [])
        self.assertEqual(backup["review_settings"], [])
        self.assertEqual(backup["projects"][0]["id"], other)

    def test_agent_manifest_and_new_routes_use_same_declared_contract(self):
        app = Application(Path(self.temp.name), Path(self.temp.name) / "http")
        manifest = app.route("GET", "/api/agent-manifest", {}, {})
        self.assertEqual(manifest["write_token"]["header"], "X-Study-Token")
        self.assertIn("project_summary", manifest["workflow"])
        preview = app.route("POST", "/api/bundles/preview", {}, {"bundle": enhanced_bundle()})
        detail = app.route("POST", f"/api/bundles/{preview['draft_id']}/commit", {}, {"confirmed": True})
        pid = detail["project"]["id"]
        route = f"/api/projects/{pid}"
        self.assertTrue(app.route("GET", route + "/reviews", {}, {})["due"])
        self.assertEqual(app.route("POST", route + "/reviews/settings", {}, {"interval_days": 7})["interval_days"], 7)
        self.assertFalse(app.route("POST", route + "/reviews", {}, {"answers": self.review_answers()})["due"])
        started = app.route("POST", route + "/checks/start", {}, {"mode": "practice", "count": 1})
        result = app.route("POST", route + f"/checks/{started['check']['id']}/answer", {}, {"activity_id": started["questions"][0]["id"], "answer": ["five"], "guessed": False})
        self.assertTrue(result["check"]["passed"])
        self.assertEqual(app.route("GET", route + "/checks/inventory", {}, {})["all"], 5)

    def test_manifest_reference_prompt_is_readable_without_entering_learning_screen(self):
        root = Path(self.temp.name) / "app"
        (root / "prompts").mkdir(parents=True)
        (root / "prompts" / "reference.md").write_text("数据契约与学习包校验说明。", encoding="utf-8")
        app = Application(root, Path(self.temp.name) / "reference-data")
        manifest = app.route("GET", "/api/agent-manifest", {}, {})
        self.assertEqual(manifest["prompts"]["reference"], "/api/coach-prompt?mode=reference")
        self.assertEqual(app.route("GET", "/api/coach-prompt", {"mode": ["reference"]}, {})["prompt"], "数据契约与学习包校验说明。")
        with self.assertRaises(InputError):
            app.route("GET", "/api/coach-prompt", {"mode": ["wrong"]}, {})


if __name__ == "__main__":
    unittest.main()
