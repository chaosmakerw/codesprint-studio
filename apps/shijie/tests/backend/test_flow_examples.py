"""Verify bundled teaching against fixed original sources and real temporary Store.

The queue is the public original example, executed only for its own value checks.
Java and learner projects are never compiled or executed. Store tests use real
temporary SQLite, no Spring, HTTP, model, network or private source directories.
Frames remain explanatory predictions; passing import does not prove mastery.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest


APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / "src"))
from projectstudy.analysis import InputError, analyze  # noqa: E402
from projectstudy.store import Store  # noqa: E402


def bundle(name):
    return json.loads((APP / "examples" / name / "learning-bundle.json").read_text(encoding="utf-8"))


class FlowExampleTests(unittest.TestCase):
    def test_bundles_are_reproducible_and_all_step_evidence_is_current(self):
        for name in ("java-demo", "python-notes"):
            with self.subTest(name=name):
                root = APP / "examples" / name
                actual = bundle(name)
                generated = runpy.run_path(str(root / "generate_bundle.py"))["build_bundle"]()
                self.assertEqual(actual, generated)
                sources = {f["path"]: f["text"] for f in actual["files"]}
                concepts = {c["id"] for c in actual["concepts"]}
                self.assertTrue(actual["flows"], "Every downloadable example must contain a real teaching chain.")
                for flow in actual["flows"]:
                    for step in flow["steps"]:
                        self.assertTrue(step["concept_ids"])
                        self.assertLessEqual(set(step["concept_ids"]), concepts)
                    items = actual["concepts"] + actual["activities"] + flow["steps"]
                    items += [method for step in flow["steps"] for method in step.get("methods", [])]
                    for item in items:
                        self.assertTrue(item["evidence"])
                        for evidence in item["evidence"]:
                            text = sources[evidence["path"]]
                            self.assertEqual(text, (root / evidence["path"]).read_text(encoding="utf-8"))
                            self.assertEqual(evidence["quote"], text.splitlines()[evidence["line"] - 1])
                            self.assertEqual(evidence["sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())
                    for case in flow["trace_cases"]:
                        self.assertIn("静态预测", case["result"])
                        for frame in case["frames"]:
                            self.assertTrue(frame["values"])
                            for value in frame["values"]:
                                self.assertEqual(set(value), {"name", "type", "value", "source"})
                                self.assertTrue(all(isinstance(v, str) and v for v in value.values()))
                            method_ids = {m["id"] for s in flow["steps"] if s["id"] == frame["step_id"] for m in s["methods"]}
                            self.assertIn(frame["method_id"], method_ids)
                        self.assertFalse(set(case.get("skipped_step_ids", [])) & {f["step_id"] for f in case["frames"]})

    def test_import_preview_confirmation_and_backup_keep_teaching_chains(self):
        for name in ("java-demo", "python-notes"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                store = Store(Path(directory))
                teaching = bundle(name)
                preview = store.preview_bundle(teaching)
                self.assertEqual(preview["flow_count"], len(teaching["flows"]))
                self.assertEqual(store.list_projects(), [])
                with self.assertRaises(InputError):
                    store.commit_bundle(preview["draft_id"], False)
                detail = store.commit_bundle(preview["draft_id"], True)
                pid = detail["project"]["id"]
                self.assertEqual(detail["flows"], teaching["flows"])
                self.assertEqual(detail["summary"]["flow_count"], len(teaching["flows"]))
                backup = store.backup()
                with tempfile.TemporaryDirectory() as restore_directory:
                    restored = Store(Path(restore_directory))
                    restored.restore(backup, True)
                    self.assertEqual(restored.project_detail(pid)["flows"], teaching["flows"])

    def test_existing_project_can_receive_flows_without_duplicate_concepts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            teaching = bundle("python-notes")
            original = copy.deepcopy(teaching)
            original.pop("flows")
            preview = store.preview_bundle(original)
            detail = store.commit_bundle(preview["draft_id"], True)
            pid, project = detail["project"]["id"], detail["project"]
            pack = {"schema_version": 2, "project_version": project["version"],
                    "source_hash": project["source_hash"], "title": "追加真实功能链",
                    "concepts": [], "activities": [], "flows": teaching["flows"]}
            draft = store.preview_pack(pid, pack)
            result = store.commit_pack(pid, draft["draft_id"], True)
            self.assertEqual((result["added_concept_count"], result["added_count"], result["added_flow_count"]), (0, 0, 2))
            final = store.project_detail(pid)
            self.assertEqual((len(final["concepts"]), len(final["activities"]), len(final["flows"])), (4, 19, 2))

    def test_python_example_predictions_match_original_success_and_stop_points(self):
        path = APP / "examples" / "python-notes" / "project" / "job_queue.py"
        spec = importlib.util.spec_from_file_location("original_flow_queue", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        NoteQueue = module.NoteQueue
        queue = NoteQueue(2)
        self.assertTrue(queue.enqueue(" A ", " First "))
        self.assertEqual(queue.snapshot(), [{"key": "a", "text": "First"}])
        for key, text in ((42, "Note"), ("a", " ")):
            with self.assertRaises(ValueError):
                queue.enqueue(key, text)
            self.assertEqual(queue.snapshot(), [{"key": "a", "text": "First"}])
        full = NoteQueue(1)
        full.enqueue("a", "First")
        self.assertFalse(full.enqueue(" A ", "Replacement"))
        with self.assertRaises(OverflowError):
            full.enqueue("b", "New")
        self.assertEqual(full.snapshot(), [{"key": "a", "text": "First"}])
        lifecycle = NoteQueue(2)
        lifecycle.enqueue("b", "First")
        lifecycle.enqueue("a", "Second")
        self.assertEqual(lifecycle.take(), {"key": "b", "text": "First"})
        self.assertTrue(lifecycle.enqueue(" B ", "Again"))
        self.assertEqual(lifecycle.snapshot(), [{"key": "a", "text": "Second"}, {"key": "b", "text": "Again"}])
        self.assertIsNone(NoteQueue(2).take())
        flows = {f["id"]: f for f in bundle("python-notes")["flows"]}
        success = next(c for c in flows["note-take-requeue"]["trace_cases"] if c["id"] == "release-requeue")
        observed = {v["name"]: v["value"] for v in success["frames"][-1]["values"]}
        self.assertEqual(json.loads(observed["return"]), lifecycle.snapshot())

    def test_java_explanations_bind_known_source_without_invented_converter_or_database(self):
        teaching = bundle("java-demo")
        files = [{**f, "sha256": hashlib.sha256(f["text"].encode("utf-8")).hexdigest()} for f in teaching["files"]]
        self.assertEqual({c["id"] for c in teaching["concepts"]}, {c["id"] for c in analyze(files)})
        flow = teaching["flows"][0]
        self.assertEqual([step["id"] for step in flow["steps"]],
                         ["wiring", "entry", "validate", "route", "identity", "service", "filter", "wrap", "return"])
        self.assertFalse(any("Converter" in step["actor"] or "Connection" in r["name"] for step in flow["steps"] for r in flow["resources"]))
        cases = {case["id"]: case for case in flow["trace_cases"]}
        self.assertEqual(list(dict.fromkeys(f["step_id"] for f in cases["forged-buyer"]["frames"])), ["wiring", "entry", "validate", "route"])
        self.assertEqual(list(dict.fromkeys(f["step_id"] for f in cases["logged-out"]["frames"])), ["wiring", "entry", "validate", "route", "identity", "return"])
        self.assertEqual(cases["forged-buyer"]["skipped_step_ids"], ["identity", "service", "filter", "wrap", "return"])
        self.assertEqual(cases["logged-out"]["skipped_step_ids"], ["service", "filter", "wrap"])
        success = cases["user-1001"]["frames"]
        method_ids = [f["method_id"] for f in success]
        self.assertEqual(method_ids.count("buyer-getter"), 2)
        self.assertEqual(method_ids.count("identity-equals"), 2)
        self.assertEqual(method_ids.count("result-add"), 1)
        self.assertLess(method_ids.index("validation-success-record"), method_ids.index("validation-success-getter"))
        self.assertLess(method_ids.index("fake-return"), method_ids.index("orders-success-factory"))
        self.assertEqual(method_ids[-2:], ["tools-return", "dispatch-return"])
        self.assertNotIn("filter", [f["step_id"] for f in cases["dependency-fails"]["frames"]])
        self.assertIn("不是现有Fake行为", cases["dependency-fails"]["input"])
        results = {case["id"]: case["result"] for case in flow["trace_cases"]}
        self.assertIn('OrderSummary(1L,1001L,"接口练习册","PAID")', results["user-1001"])
        self.assertNotIn("集合练习册", results["user-1001"])
        self.assertIn("data=[]", results["user-9000"])

    def test_tampered_call_evidence_and_cross_project_concept_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            teaching = bundle("java-demo")
            broken = copy.deepcopy(teaching)
            broken["flows"][0]["steps"][0]["evidence"][0]["sha256"] = "0" * 64
            with self.assertRaises(InputError):
                store.preview_bundle(broken)
            broken = copy.deepcopy(teaching)
            broken["flows"][0]["steps"][0]["concept_ids"] = ["note-inputs"]
            with self.assertRaises(InputError):
                store.preview_bundle(broken)

    def test_examples_explain_the_project_before_code_and_keep_exact_multi_answers(self):
        for name, aid in (("java-demo", "order-q13"), ("python-notes", "note-q15")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                teaching = bundle(name)
                summary = teaching["project_summary"]
                self.assertTrue(all(summary[k] for k in ("purpose", "audience", "scenario", "capabilities", "learning_value", "limitations", "evidence")))
                objective = [a for a in teaching["activities"] if a["options"]]
                self.assertTrue({2, 3, 4} <= {len(a["answer_ids"]) for a in objective})
                self.assertTrue(all(len(a["answer_ids"]) < len(a["options"]) for a in objective))
                store = Store(Path(directory))
                preview = store.preview_bundle(teaching)
                detail = store.commit_bundle(preview["draft_id"], True)
                pid = detail["project"]["id"]
                question = next(a for a in objective if a["id"] == aid)
                answers = question["answer_ids"]
                self.assertEqual(store.submit(pid, aid, answers[:-1])["attempt"]["score"], 0)
                self.assertEqual(store.submit(pid, aid, answers)["attempt"]["score"], 100)
                first = store.activity_detail(pid, aid)["activity"]["first_attempt"]
                self.assertEqual(first["answer"], answers[:-1])


if __name__ == "__main__":
    unittest.main()
