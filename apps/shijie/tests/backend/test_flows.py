"""Scene narratives use actual SQLite and source hashes, never execute project code."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_generic_backend import InputError, Store, bundle_fixture
from projectstudy.server import Application


def flow_fixture(fid="sum-flow"):
    bundle = bundle_fixture()
    anchor = bundle["concepts"][0]["evidence"][0]
    return {"id": fid, "title": "统计两个数", "summary": "从调用函数到返回合计，逐步解释输入和返回值。",
            "scenario": "调用方要统计两个数 2 和 3，将 [2, 3] 交给 total。",
            "input": "total([2, 3])", "expected": "返回整数 5。",
            "steps": [
                {"id": "receive", "title": "接收序列", "actor": "total 函数",
                 "action": "把调用方传入的列表绑定到 values。", "input": "列表 [2, 3]",
                 "output": "values 指向 [2, 3]。", "why": "函数通过参数接收调用方数据。",
                 "concept_ids": ["python-aggregation"], "evidence": [anchor]},
                {"id": "aggregate", "title": "计算并返回", "actor": "sum 与 total",
                 "action": "sum 遍历 values 求和，total 把合计返回调用方。",
                 "input": "values=[2, 3]", "output": "整数 5",
                 "why": "return 将计算结果交回调用方，调用方不需要复制求和实现。",
                 "concept_ids": ["python-aggregation"], "evidence": [anchor]}],
            "branches": [{"from_step_id": "receive", "condition": "values 是不可迭代的整数 2。",
                          "outcome": "sum 抛出 TypeError，不能得到正常合计。", "skipped_step_ids": []}],
            "trace_cases": [{"id": "two-numbers", "title": "两个数的正常推演", "input": "total([2, 3])",
                             "result": "返回 5；这是按源码推演，尚未运行。",
                             "frames": [
                                 {"step_id": "receive", "explanation": "函数形参接收调用方的列表。",
                                  "values": [{"name": "values", "type": "list[int]", "value": "[2, 3]", "source": "调用方实参"}]},
                                 {"step_id": "aggregate", "explanation": "求和结果经 return 返回。",
                                  "values": [{"name": "result", "type": "int", "value": "5", "source": "sum(values)"}]}]}],
            "resources": []}


def scene_bundle():
    bundle = bundle_fixture()
    bundle["flows"] = [flow_fixture()]
    return bundle


class FlowBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def imported(self, raw=None):
        draft = self.store.preview_bundle(raw or scene_bundle())
        return self.store.commit_bundle(draft["draft_id"], True)

    def pack(self, detail, flows=None, concepts=None, activities=None):
        project = detail["project"]
        return {"schema_version": 2, "project_version": project["version"], "source_hash": project["source_hash"],
                "concepts": concepts or [], "activities": activities or [], "flows": flows or []}

    def test_full_bundle_flows_persist_reopen_backup_and_restore(self):
        raw = scene_bundle()
        preview = self.store.preview_bundle(raw)
        self.assertEqual(preview["flow_count"], 1)
        self.assertEqual(preview["flows"], raw["flows"])
        self.assertEqual(self.store.list_projects(), [])
        detail = self.store.commit_bundle(preview["draft_id"], True)
        pid = detail["project"]["id"]
        self.assertEqual(detail["flows"], raw["flows"])
        self.assertEqual(detail["summary"]["flow_count"], 1)
        reopened = Store(Path(self.temp.name))
        self.assertEqual(reopened.project_detail(pid)["flows"], raw["flows"])
        backup = reopened.backup()
        self.assertEqual(backup["schema_version"], 2)
        self.assertEqual(backup["data"]["flows"], [{"project_id": pid, "data": raw["flows"][0]}])
        with tempfile.TemporaryDirectory() as directory:
            restored = Store(Path(directory))
            restored.restore(backup, True)
            self.assertEqual(restored.backup()["data"], backup["data"])
            self.assertEqual(restored.project_detail(pid)["flows"], raw["flows"])
        handoff = reopened.handoff(pid)
        self.assertEqual(handoff["flows"], raw["flows"])
        self.assertIn("trace_cases", handoff["flow_contract"]["fields"])
        self.assertIn("静态教学推演", handoff["instructions"])
        self.assertNotIn("root", handoff["project"])

    def test_invalid_concept_step_and_source_references_reject_before_import(self):
        mutations = {
            "unknown-concept": lambda f: f["steps"][1].update(concept_ids=["absent"]),
            "branch-start": lambda f: f["branches"][0].update(from_step_id="absent"),
            "branch-skip": lambda f: f["branches"][0].update(skipped_step_ids=["absent"]),
            "trace-step": lambda f: f["trace_cases"][0]["frames"][1].update(step_id="absent"),
            "hash": lambda f: f["steps"][1]["evidence"][0].update(sha256="0" * 64),
            "line": lambda f: f["steps"][1]["evidence"][0].update(line=99),
            "quote": lambda f: f["steps"][1]["evidence"][0].update(quote="fabricated source"),
        }
        for label, mutate in mutations.items():
            raw = scene_bundle()
            raw["flows"].append(flow_fixture("late-flow"))
            mutate(raw["flows"][-1])
            with self.subTest(label=label), self.assertRaises(InputError):
                self.store.preview_bundle(raw)
            self.assertEqual(self.store.list_projects(), [])

    def test_tampered_last_flow_commit_keeps_no_partial_project(self):
        raw = scene_bundle()
        raw["flows"].append(flow_fixture("late-flow"))
        preview = self.store.preview_bundle(raw)
        with self.store._connection(True) as db:
            draft = self.store._rows(db, "drafts")[0]
            draft["flows"][-1]["steps"][-1]["concept_ids"] = ["absent"]
            db.execute("DELETE FROM drafts WHERE id=?", (draft["id"],))
            self.store._insert(db, "drafts", draft, "__intake__")
        before = self.store.backup()["data"]
        with self.assertRaises(InputError):
            self.store.commit_bundle(preview["draft_id"], True)
        self.assertEqual(self.store.backup()["data"], before)
        self.assertEqual(self.store.list_projects(), [])

    def test_flow_only_pack_references_existing_concept_and_rejects_duplicate(self):
        detail = self.imported(bundle_fixture())
        pid = detail["project"]["id"]
        pack = self.pack(detail, [flow_fixture()])
        preview = self.store.preview_pack(pid, pack)
        self.assertEqual((preview["concept_count"], preview["activity_count"], preview["flow_count"]), (0, 0, 1))
        committed = self.store.commit_pack(pid, preview["draft_id"], True)
        self.assertEqual((committed["added_count"], committed["added_concept_count"], committed["added_flow_count"]), (0, 0, 1))
        self.assertEqual(self.store.project_detail(pid)["flows"], pack["flows"])
        before = self.store.backup()["data"]
        with self.assertRaises(InputError):
            self.store.preview_pack(pid, pack)
        self.assertEqual(self.store.backup()["data"], before)
        with self.assertRaises(InputError):
            self.store.preview_pack(pid, self.pack(detail))

    def test_pack_can_reference_same_batch_concepts_without_activities(self):
        detail = self.imported(bundle_fixture())
        concept = deepcopy(bundle_fixture()["concepts"][0])
        concept["id"] = "new-concept"
        flow = flow_fixture()
        flow["steps"][1]["concept_ids"] = [concept["id"]]
        pid = detail["project"]["id"]
        preview = self.store.preview_pack(pid, self.pack(detail, [flow], [concept]))
        self.store.commit_pack(pid, preview["draft_id"], True)
        self.assertEqual(self.store.project_detail(pid)["summary"]["concept_count"], 2)

    def test_late_duplicate_flow_blocks_entire_pack_commit(self):
        detail = self.imported(bundle_fixture())
        pid = detail["project"]["id"]
        concept = deepcopy(bundle_fixture()["concepts"][0])
        concept["id"] = "new-concept"
        preview = self.store.preview_pack(pid, self.pack(detail, [flow_fixture()], [concept]))
        with self.store._connection(True) as db:
            self.store._insert(db, "flows", flow_fixture(), pid)
        before = self.store.backup()["data"]
        with self.assertRaises(InputError):
            self.store.commit_pack(pid, preview["draft_id"], True)
        self.assertEqual(self.store.backup()["data"], before)
        self.assertEqual(self.store.project_detail(pid)["summary"]["concept_count"], 1)

    def test_legacy_schema2_bundle_pack_and_backups_without_flows(self):
        detail = self.imported(bundle_fixture())
        pid = detail["project"]["id"]
        self.assertEqual(detail["flows"], [])
        activity = deepcopy(bundle_fixture()["activities"][0])
        activity["id"] = "legacy-question"
        pack = self.pack(detail, activities=[activity])
        pack.pop("flows")
        preview = self.store.preview_pack(pid, pack)
        self.assertEqual(preview["flow_count"], 0)
        self.store.commit_pack(pid, preview["draft_id"], True)
        backup = self.store.backup()
        backup["data"].pop("flows")
        self.store.restore(backup, True)
        self.assertEqual(self.store.project_detail(pid)["flows"], [])
        backup["schema_version"] = 1
        backup["data"].pop("checks")
        self.store.restore(backup, True)
        self.assertEqual(self.store.project_detail(pid)["summary"]["activity_count"], 6)
        legacy_pack = {"schema_version": 1, "project_version": 1, "activities": [activity], "flows": []}
        with self.assertRaises(InputError):
            self.store.preview_pack(pid, legacy_pack)

    def test_source_version_change_clears_flows_and_keeps_answer_history(self):
        detail = self.imported()
        pid = detail["project"]["id"]
        self.store.submit(pid, "sum-0", ["five"])
        raw_files = bundle_fixture()["files"]
        raw_files[0]["text"] += "# new source version\n"
        updated = self.store.upload(raw_files, project_id=pid)
        self.assertEqual(updated["project"]["version"], 2)
        self.assertEqual(updated["flows"], [])
        self.assertEqual(updated["summary"]["stale_attempt_count"], 1)
        self.store.restore(self.store.backup(), True)
        self.assertEqual(self.store.project_detail(pid)["summary"]["attempt_count"], 1)

    def test_material_change_clears_flows_and_preserves_hints_evidence_checks(self):
        detail = self.imported()
        pid = detail["project"]["id"]
        attempted = self.store.submit(pid, "sum-0", ["five"])["attempt"]
        self.store.submit_evidence(pid, {"attempt_id": attempted["id"], "summary": "已手算核对", "independent": True})
        self.store.hint(pid, "sum-1", 1)
        started = self.store.start_check(pid, 5)
        unchanged = self.store.upload(bundle_fixture()["files"], project_id=pid)
        self.assertFalse(unchanged["source_changed"])
        self.assertEqual(len(unchanged["flows"]), 1)
        updated = self.store.add_materials(pid, [{"path": "new.md", "text": "追加学习资料。"}])
        self.assertEqual(updated["flows"], [])
        self.assertEqual(updated["project"]["version"], 2)
        backup = self.store.backup()
        self.assertEqual((len(backup["data"]["attempts"]), len(backup["data"]["hints"]), len(backup["data"]["evidence"]), len(backup["data"]["checks"])), (1, 1, 1, 1))
        self.store.restore(backup, True)
        self.assertTrue(self.store.checks(pid)["checks"][0]["stale"])
        with self.assertRaises(InputError):
            self.store.submit_check(pid, started["check"]["id"], [])

    def test_flow_fields_and_all_nested_objects_are_strict_and_bounded(self):
        mutations = {
            "extra-flow": lambda f: f.update(command="print(1)"),
            "missing-flow": lambda f: f.pop("scenario"),
            "summary-length": lambda f: f.update(summary="x" * 4001),
            "scenario-length": lambda f: f.update(scenario="x" * 8001),
            "one-step": lambda f: f.update(steps=f["steps"][:1]),
            "21-steps": lambda f: f.update(steps=[{**f["steps"][0], "id": f"s-{n}"} for n in range(21)]),
            "duplicate-step": lambda f: f["steps"][1].update(id="receive"),
            "empty-action": lambda f: f["steps"][0].update(action=" "),
            "empty-input": lambda f: f["steps"][0].update(input=""),
            "empty-output": lambda f: f["steps"][0].update(output=""),
            "empty-why": lambda f: f["steps"][0].update(why=""),
            "step-text-length": lambda f: f["steps"][0].update(why="x" * 5001),
            "no-concept": lambda f: f["steps"][0].update(concept_ids=[]),
            "duplicate-concept": lambda f: f["steps"][0].update(concept_ids=["python-aggregation"] * 2),
            "extra-step": lambda f: f["steps"][0].update(command="print(1)"),
            "extra-branch": lambda f: f["branches"][0].update(command="print(1)"),
            "branch-length": lambda f: f["branches"][0].update(condition="x" * 5001),
            "too-many-branches": lambda f: f.update(branches=f["branches"] * 21),
            "too-many-skip-steps": lambda f: f["branches"][0].update(skipped_step_ids=["receive"] * 21),
            "duplicate-case": lambda f: f.update(trace_cases=f["trace_cases"] * 2),
            "13-cases": lambda f: f.update(trace_cases=[{**f["trace_cases"][0], "id": f"case-{n}"} for n in range(13)]),
            "extra-case": lambda f: f["trace_cases"][0].update(command="print(1)"),
            "no-frame": lambda f: f["trace_cases"][0].update(frames=[]),
            "121-frames": lambda f: f["trace_cases"][0].update(frames=[f["trace_cases"][0]["frames"][0]] * 121),
            "extra-frame": lambda f: f["trace_cases"][0]["frames"][0].update(command="print(1)"),
            "no-value": lambda f: f["trace_cases"][0]["frames"][0].update(values=[]),
            "25-values": lambda f: f["trace_cases"][0]["frames"][0].update(values=f["trace_cases"][0]["frames"][0]["values"] * 25),
            "extra-value": lambda f: f["trace_cases"][0]["frames"][0]["values"][0].update(command="print(1)"),
            "nonstring-value": lambda f: f["trace_cases"][0]["frames"][0]["values"][0].update(value=[2, 3]),
            "source-length": lambda f: f["trace_cases"][0]["frames"][0]["values"][0].update(source="x" * 1001),
            "extra-resource": lambda f: f.update(resources=[{"name": "data", "ownership": "caller", "lifecycle": "call", "failure": "return", "command": "print(1)"}]),
            "21-resources": lambda f: f.update(resources=[{"name": "data", "ownership": "caller", "lifecycle": "call", "failure": "return"}] * 21),
            "resource-text-length": lambda f: f.update(resources=[{"name": "data", "ownership": "x" * 3001, "lifecycle": "call", "failure": "return"}]),
        }
        for label, mutate in mutations.items():
            raw = scene_bundle()
            mutate(raw["flows"][0])
            with self.subTest(label=label), self.assertRaises(InputError):
                self.store.preview_bundle(raw)
        self.assertEqual(self.store.list_projects(), [])

    def test_project_flow_limit_applies_to_full_and_addition_packs(self):
        raw = scene_bundle()
        raw["flows"] = [flow_fixture(f"f-{n}") for n in range(12)]
        detail = self.imported(raw)
        self.assertEqual(detail["summary"]["flow_count"], 12)
        with self.assertRaises(InputError):
            self.store.preview_pack(detail["project"]["id"], self.pack(detail, [flow_fixture("new-flow")]))
        raw["flows"].append(flow_fixture("f-12"))
        with self.assertRaises(InputError):
            self.store.preview_bundle(raw)

    def test_empty_trace_and_resources_are_legal_but_wholly_empty_bundle_is_not(self):
        raw = scene_bundle()
        raw["flows"][0]["trace_cases"] = []
        raw["activities"] = []
        detail = self.imported(raw)
        self.assertEqual(detail["summary"]["activity_count"], 0)
        self.assertEqual(detail["flows"][0]["resources"], [])
        self.store.restore(self.store.backup(), True)
        raw.update(concepts=[], flows=[], activities=[])
        with self.assertRaises(InputError):
            self.store.preview_bundle(raw)

    def test_restore_bad_late_flow_is_atomic_and_validates_hashes_and_links(self):
        self.imported()
        before = self.store.backup()
        mutations = {
            "concept": lambda f: f["steps"][-1].update(concept_ids=["absent"]),
            "step": lambda f: f["trace_cases"][0]["frames"][-1].update(step_id="absent"),
            "hash": lambda f: f["steps"][-1]["evidence"][0].update(sha256="0" * 64),
            "branch": lambda f: f["branches"][0].update(skipped_step_ids=["absent"]),
        }
        for label, mutate in mutations.items():
            broken = deepcopy(before)
            added = deepcopy(broken["data"]["flows"][0])
            added["data"]["id"] = "bad-late-flow"
            mutate(added["data"])
            broken["data"]["flows"].append(added)
            with self.subTest(label=label), self.assertRaises(InputError):
                self.store.restore(broken, True)
            self.assertEqual(self.store.backup()["data"], before["data"])

    def test_capacity_failure_rolls_back_all_content_and_preserves_draft(self):
        preview = self.store.preview_bundle(scene_bundle())
        with patch("projectstudy.store.MAX_BACKUP_BYTES", 200), self.assertRaises(InputError):
            self.store.commit_bundle(preview["draft_id"], True)
        self.assertEqual(self.store.list_projects(), [])
        self.assertEqual(self.store.backup()["data"]["flows"], [])
        detail = self.store.commit_bundle(preview["draft_id"], True)
        self.assertEqual(detail["summary"]["flow_count"], 1)

    def test_delete_project_removes_only_its_flows(self):
        first, second = self.imported(), self.imported()
        self.store.delete_project(first["project"]["id"], True)
        self.assertEqual(self.store.project_detail(second["project"]["id"])["flows"], scene_bundle()["flows"])
        self.assertEqual(len(self.store.backup()["data"]["flows"]), 1)

    def test_demo_route_uses_complete_bundle_when_present(self):
        root = Path(self.temp.name) / "app"
        demo = root / "examples" / "java-demo"
        demo.mkdir(parents=True)
        (demo / "learning-bundle.json").write_text(json.dumps(scene_bundle(), ensure_ascii=False), encoding="utf-8")
        app = Application(root, Path(self.temp.name) / "route-data")
        detail = app.route("POST", "/api/projects/demo", {}, {})
        self.assertEqual(detail["summary"]["flow_count"], 1)
        self.assertEqual(detail["flows"], scene_bundle()["flows"])
        self.assertNotIn("root", detail["project"])
        repeated = app.route("POST", "/api/projects/demo", {}, {})
        self.assertEqual(repeated["project"]["id"], detail["project"]["id"])
        self.assertEqual(len(app.store.list_projects()), 1)

    def test_demo_repeated_clicks_and_reopened_store_reuse_project_with_history(self):
        first = self.store.import_builtin_bundle(scene_bundle(), "java-demo")
        pid = first["project"]["id"]
        self.store.submit(pid, "sum-0", ["five"])
        self.store.hint(pid, "sum-1", 1)
        second = self.store.import_builtin_bundle(scene_bundle(), "java-demo")
        self.assertEqual(second["project"]["id"], pid)
        self.assertEqual(second["summary"]["attempt_count"], 1)
        self.assertEqual(len(self.store.list_projects()), 1)
        reopened = Store(Path(self.temp.name))
        third = reopened.import_builtin_bundle(scene_bundle(), "java-demo")
        self.assertEqual(third["project"]["id"], pid)
        self.assertEqual(len(reopened.activity_detail(pid, "sum-1")["hints_requested"]), 1)
        self.assertNotIn("builtin_imports", reopened.backup()["data"])

    def test_builtin_origin_does_not_match_user_same_name_or_ordinary_import(self):
        user = self.imported()
        user_pid = user["project"]["id"]
        self.store.submit(user_pid, "sum-0", ["five"])
        demo = self.store.import_builtin_bundle(scene_bundle(), "java-demo")
        self.assertNotEqual(demo["project"]["id"], user_pid)
        self.assertEqual(demo["project"]["name"], user["project"]["name"])
        self.assertEqual(demo["project"]["source_hash"], user["project"]["source_hash"])
        ordinary = self.imported()
        self.assertNotEqual(ordinary["project"]["id"], demo["project"]["id"])
        self.assertEqual(len(self.store.list_projects()), 3)
        self.assertEqual(self.store.project_detail(user_pid)["summary"]["attempt_count"], 1)

    def test_builtin_content_revision_gets_new_snapshot_without_overwriting_answers(self):
        first = self.store.import_builtin_bundle(scene_bundle(), "java-demo")
        pid = first["project"]["id"]
        self.store.submit(pid, "sum-0", ["five"])
        revised = scene_bundle()
        revised["flows"][0]["summary"] += "这次补充了参数绑定说明。"
        second = self.store.import_builtin_bundle(revised, "java-demo")
        self.assertNotEqual(second["project"]["id"], pid)
        self.assertEqual(second["project"]["source_hash"], first["project"]["source_hash"])
        third = self.store.import_builtin_bundle(revised, "java-demo")
        self.assertEqual(second["project"]["id"], third["project"]["id"])
        self.assertEqual(self.store.project_detail(pid)["summary"]["attempt_count"], 1)
        self.assertEqual(self.store.project_detail(pid)["flows"], scene_bundle()["flows"])

    def test_builtin_modified_snapshot_is_preserved_and_fresh_original_created(self):
        first = self.store.import_builtin_bundle(scene_bundle(), "java-demo")
        pid = first["project"]["id"]
        self.store.submit(pid, "sum-0", ["five"])
        self.store.add_materials(pid, [{"path": "my-notes.md", "text": "我的学习笔记。"}])
        fresh = self.store.import_builtin_bundle(scene_bundle(), "java-demo")
        self.assertNotEqual(fresh["project"]["id"], pid)
        self.assertEqual(fresh["project"]["version"], 1)
        self.assertEqual(fresh["summary"]["flow_count"], 1)
        preserved = self.store.project_detail(pid)
        self.assertEqual(preserved["project"]["version"], 2)
        self.assertEqual(preserved["summary"]["stale_attempt_count"], 1)

    def test_builtin_does_not_consume_ordinary_pending_preview(self):
        preview = self.store.preview_bundle(scene_bundle())
        demo = self.store.import_builtin_bundle(scene_bundle(), "java-demo")
        ordinary = self.store.commit_bundle(preview["draft_id"], True)
        self.assertNotEqual(demo["project"]["id"], ordinary["project"]["id"])

    def test_builtin_concurrent_imports_are_atomic_and_reuse_at_capacity(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            details = list(executor.map(lambda _: self.store.import_builtin_bundle(scene_bundle(), "java-demo"), range(4)))
        ids = {d["project"]["id"] for d in details}
        self.assertEqual(len(ids), 1)
        for _ in range(11):
            self.imported()
        reused = self.store.import_builtin_bundle(scene_bundle(), "java-demo")
        self.assertIn(reused["project"]["id"], ids)
        self.assertEqual(len(self.store.list_projects()), 12)

    def test_builtin_markers_are_removed_on_delete_and_replace_restore(self):
        first = self.store.import_builtin_bundle(scene_bundle(), "java-demo")
        pid = first["project"]["id"]
        self.store.delete_project(pid, True)
        with self.store._connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM builtin_imports").fetchone()[0], 0)
        second = self.store.import_builtin_bundle(scene_bundle(), "java-demo")
        self.assertNotEqual(second["project"]["id"], pid)
        self.store.restore(self.store.backup(), True)
        with self.store._connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM builtin_imports").fetchone()[0], 0)

    def test_demo_route_rejects_bad_bundle_without_silently_scanning(self):
        root = Path(self.temp.name) / "app"
        demo = root / "examples" / "java-demo"
        demo.mkdir(parents=True)
        (demo / "main.py").write_text("print(2)\n", encoding="utf-8")
        (demo / "learning-bundle.json").write_text("{broken JSON", encoding="utf-8")
        app = Application(root, Path(self.temp.name) / "route-data")
        with self.assertRaises(InputError):
            app.route("POST", "/api/projects/demo", {}, {})
        self.assertEqual(app.store.list_projects(), [])
        (demo / "learning-bundle.json").unlink()
        self.assertEqual(app.route("POST", "/api/projects/demo", {}, {})["flows"], [])


if __name__ == "__main__":
    unittest.main()
