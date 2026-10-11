"""Use the complete original Python bundle with real temporary SQLite storage.

These checks import learning data, never the example project as Python code.
They use no browser, HTTP, framework, model, network or learner-private files.
The example project's own seven unit tests are separate development evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


APP = Path(__file__).resolve().parents[2]
EXAMPLE = APP / "examples" / "python-notes"
sys.path.insert(0, str(APP / "src"))

from projectstudy.analysis import InputError  # noqa: E402
from projectstudy.store import Store  # noqa: E402


def load_bundle():
    return json.loads((EXAMPLE / "learning-bundle.json").read_text(encoding="utf-8"))


class PythonLearningExampleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.bundle = load_bundle()
        self.tasks = {task["id"]: task for task in self.bundle["activities"]}

    def tearDown(self):
        self.temp.cleanup()

    def import_bundle(self):
        preview = self.store.preview_bundle(self.bundle)
        detail = self.store.commit_bundle(preview["draft_id"], True)
        return detail["project"]["id"]

    def answer_check(self, pid, check, wrong=0):
        answers = []
        for index, question in enumerate(check["questions"]):
            correct = self.tasks[question["id"]]["answer_ids"]
            if index < wrong:
                response = [next(option["id"] for option in question["options"] if option["id"] not in correct)]
            else:
                response = correct
            answers.append({"activity_id": question["id"], "answer": response})
        return self.store.submit_check(pid, check["check"]["id"], answers)

    def test_bundle_matches_original_files_and_every_anchor(self):
        self.assertEqual(self.bundle["schema_version"], 2)
        self.assertEqual(len(self.bundle["files"]), 6)
        self.assertEqual(len(self.bundle["concepts"]), 4)
        self.assertEqual(len({concept["group"] for concept in self.bundle["concepts"]}), 3)
        self.assertEqual(sum(bool(task["options"]) for task in self.tasks.values()), 15)
        self.assertEqual({task["kind"] for task in self.tasks.values()},
                         {"predict", "trace", "debug", "reproduce", "transfer"})
        contents = {file["path"]: file["text"] for file in self.bundle["files"]}
        before = {path: (EXAMPLE / path).read_bytes() for path in contents}
        for path, text in contents.items():
            self.assertEqual(text, (EXAMPLE / path).read_text(encoding="utf-8"))
            self.assertTrue(path.startswith(("project/", "materials/")))
        for item in self.bundle["concepts"] + self.bundle["activities"]:
            self.assertTrue(item["evidence"])
            for anchor in item["evidence"]:
                text = contents[anchor["path"]]
                self.assertEqual(anchor["quote"], text.splitlines()[anchor["line"] - 1])
                self.assertEqual(anchor["sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())
        for concept in self.bundle["concepts"]:
            self.assertTrue(concept["lesson"])
            self.assertTrue(concept["mechanism"])
            self.assertTrue(concept["failures"])
            self.assertEqual(concept["references"], [])
        self.import_bundle()
        self.assertEqual(before, {path: (EXAMPLE / path).read_bytes() for path in contents})

    def test_one_confirmation_imports_non_java_sources_materials_and_custom_graph(self):
        preview = self.store.preview_bundle(self.bundle)
        self.assertEqual(self.store.list_projects(), [])
        self.assertEqual((preview["file_count"], preview["concept_count"], preview["activity_count"]), (6, 4, 19))
        with self.assertRaises(InputError):
            self.store.commit_bundle(preview["draft_id"], False)
        self.assertEqual(self.store.list_projects(), [])
        detail = self.store.commit_bundle(preview["draft_id"], True)
        pid = detail["project"]["id"]
        self.assertEqual(len(self.store.list_projects()), 1)
        self.assertEqual((detail["summary"]["concept_count"], detail["summary"]["activity_count"]), (4, 19))
        self.assertEqual(len(detail["materials"]), 4)
        self.assertEqual({concept["id"] for concept in detail["concepts"]},
                         {concept["id"] for concept in self.bundle["concepts"]})
        chain = next(concept for concept in detail["concepts"] if concept["id"] == "note-lifecycle")
        self.assertEqual(chain["prerequisites"], ["note-dedup"])
        for file in self.bundle["files"]:
            self.assertEqual(self.store.source(pid, file["path"])["text"], file["text"])
        self.assertIn("尚未验证", detail["summary"]["mastery_claim"])

    def test_learning_preserves_first_answer_sequential_hints_and_open_review(self):
        pid = self.import_bundle()
        hidden = self.store.activity_detail(pid, "note-q01")["activity"]
        for field in ("answer_ids", "explanation", "hints"):
            self.assertNotIn(field, hidden)
        self.assertEqual(self.store.submit(pid, "note-q01", ["a"])["attempt"]["status"], "incorrect")
        corrected = self.store.submit(pid, "note-q01", ["b"])["attempt"]
        self.assertEqual(corrected["score"], 100)
        detail = self.store.activity_detail(pid, "note-q01")
        self.assertEqual(detail["activity"]["first_attempt"]["answer"], ["a"])
        self.assertEqual(len(detail["history"]), 2)
        with self.assertRaises(InputError):
            self.store.hint(pid, "note-trace", 2)
        self.store.hint(pid, "note-trace", 1)
        self.store.hint(pid, "note-trace", 2)
        trace = self.store.submit(pid, "note-trace", "我将变量、返回和状态写成表，当前仅提供静态预测。")["attempt"]
        self.assertEqual((trace["status"], trace["score"], trace["hint_level"]), ("review_required", None, 2))
        reproduction = self.store.submit(pid, "note-reproduce", "目前是实现草案，没有实际运行记录。")["attempt"]
        self.assertEqual((reproduction["status"], reproduction["score"]), ("review_required", None))
        self.assertTrue(self.store.activity_detail(pid, "note-transfer")["activity"]["delay_pending"])

    def test_random_check_four_of_five_passes_without_mastery_and_backup_restores(self):
        pid = self.import_bundle()
        check = self.store.start_check(pid, count=5)
        self.assertEqual(check["check"]["question_count"], 5)
        self.assertEqual(len({question["id"] for question in check["questions"]}), 5)
        for question in check["questions"]:
            self.assertTrue(self.tasks[question["id"]]["options"])
            self.assertNotIn("answer_ids", question)
            self.assertNotIn("explanation", question)
        result = self.answer_check(pid, check, wrong=1)
        self.assertEqual((result["check"]["correct_count"], result["check"]["question_count"], result["check"]["score"]), (4, 5, 80.0))
        self.assertTrue(result["check"]["passed"])
        self.assertTrue(result["check"]["independent"])
        self.assertIn("不证明独立掌握", result["check"]["mastery_claim"])
        with self.assertRaises(InputError):
            self.answer_check(pid, check)
        backup = self.store.backup()
        with tempfile.TemporaryDirectory() as restored_dir:
            restored = Store(Path(restored_dir))
            restored.restore(backup, True)
            restored_check = restored.checks(pid)["checks"][0]
            self.assertEqual((restored_check["correct_count"], restored_check["score"]), (4, 80.0))
            self.assertEqual(restored.project_detail(pid)["summary"]["attempt_count"], 5)

    def test_random_check_three_of_five_fails_and_concept_filter_uses_real_pool(self):
        pid = self.import_bundle()
        check = self.store.start_check(pid, count=5)
        result = self.answer_check(pid, check, wrong=2)
        self.assertEqual((result["check"]["correct_count"], result["check"]["score"]), (3, 60.0))
        self.assertFalse(result["check"]["passed"])
        selected = self.store.start_check(pid, count=8, concept_id="note-inputs")
        self.assertEqual(selected["check"]["question_count"], 4)
        self.assertEqual({question["concept_id"] for question in selected["questions"]}, {"note-inputs"})
        self.assertEqual(self.answer_check(pid, selected)["check"]["score"], 100.0)


if __name__ == "__main__":
    unittest.main()
