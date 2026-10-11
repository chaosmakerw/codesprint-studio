"""Conservative curriculum regression tests using only the original demo.

The braced fixture is an in-memory syntax variant of the public demo; it never
reads or edits a learner's private project. Real Java is not executed here.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest


APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / "src"))

from projectstudy.analysis import analyze  # noqa: E402
from projectstudy.curriculum import build_curriculum  # noqa: E402


def _files():
    root = APP / "examples" / "java-demo"
    files = []
    for path in sorted(root.rglob("*.java")):
        content = path.read_text(encoding="utf-8")
        # The exact recogniser intentionally requires explicit branch blocks.
        # Adapt the test fixture in memory, retaining the demo's actual behaviour.
        replacements = {
            "if (!validation.success()) return validation;":
                "if (!validation.success()) { return validation; }",
            'if ("list_my_orders".equals(toolName)) return orderTools.listMyOrders();':
                'if ("list_my_orders".equals(toolName)) { return orderTools.listMyOrders(); }',
            "if (currentUserId.equals(order.buyerId())) result.add(order);":
                "if (currentUserId.equals(order.buyerId())) { result.add(order); }",
        }
        for old, new in replacements.items():
            content = content.replace(old, new)
        files.append({"path": path.relative_to(root).as_posix(), "text": content,
                      "sha256": hashlib.sha256(content.encode()).hexdigest()})
    return files


def _changed(files, filename, old, new):
    changed = copy.deepcopy(files)
    matched = False
    for f in changed:
        if f["path"].endswith("/" + filename):
            if old not in f["text"]:
                raise AssertionError("The regression fixture no longer contains its expected mutation anchor.")
            f["text"] = f["text"].replace(old, new)
            f["sha256"] = hashlib.sha256(f["text"].encode()).hexdigest()
            matched = True
    if not matched:
        raise AssertionError("The regression fixture file is missing.")
    return changed


def _order_count(files):
    return sum("-order-" in a["id"] for a in build_curriculum(files, analyze(files)))


class CurriculumChecks(unittest.TestCase):
    def setUp(self):
        self.files = _files()

    def test_order_chain_has_five_versioned_source_backed_tasks(self):
        self.assertEqual(5, _order_count(self.files))
        index = {f["path"]: f for f in self.files}
        activities = build_curriculum(self.files, analyze(self.files))
        self.assertEqual(len(activities), len({a["id"] for a in activities}))
        self.assertLessEqual(len(activities), 40)
        for activity in activities:
            self.assertEqual(3, len(activity["hints"]))
            self.assertTrue(activity["rubric"])
            self.assertTrue(activity["explanation"])
            self.assertTrue(set(activity["answer_ids"]) <= {o["id"] for o in activity["options"]})
            for evidence in activity["evidence"]:
                source = index[evidence["path"]]
                self.assertIn(evidence["quote"], source["text"].splitlines()[evidence["line"] - 1])
                self.assertEqual(evidence["sha256"], source["sha256"])

    def test_actual_buyer_ids_drive_scenarios(self):
        files = _changed(self.files, "FakeOrderService.java", "1001L", "4101L")
        files = _changed(files, "FakeOrderService.java", "1002L", "4202L")
        question = next(a for a in build_curriculum(files, analyze(files)) if "-order-predict-" in a["id"])
        self.assertIn("4101", question["prompt"])
        self.assertNotIn("1001", question["prompt"])

    def test_missing_or_weakened_filter_has_no_assumed_order_answers(self):
        changed = _changed(self.files, "FakeOrderService.java", "currentUserId.equals(order.buyerId())",
                           "currentUserId == order.buyerId()")
        self.assertEqual(0, _order_count(changed))
        self.assertEqual(0, _order_count([f for f in self.files if not f["path"].endswith("ToolDispatcher.java")]))
        self.assertTrue(any(a["concept_id"] == "identity-permission" and not a["answer_ids"]
                            for a in build_curriculum(changed, analyze(changed))))

    def test_extra_early_success_rejects_exact_order_chain(self):
        changed = _changed(self.files, "ToolArgumentValidator.java", "if (arguments == null)",
                           'if (arguments.containsKey("bypass")) { return ToolResult.success(null); }\n'
                           '            if (arguments == null)')
        self.assertEqual(0, _order_count(changed))

    def test_commented_comparison_is_not_executable_evidence(self):
        changed = _changed(self.files, "FakeOrderService.java", "if (currentUserId.equals(order.buyerId()))",
                           "// if (currentUserId.equals(order.buyerId()))\n            if (true)")
        self.assertEqual(0, _order_count(changed))

    def test_stale_concept_evidence_is_rejected(self):
        concepts = analyze(self.files)
        for concept in concepts:
            for evidence in concept["evidence"]:
                evidence["sha256"] = "0" * 64
        incomplete = [f for f in self.files if not f["path"].endswith("FakeOrderService.java")]
        self.assertEqual([], build_curriculum(incomplete, concepts))

    def test_extra_data_addition_rejects_exact_order_chain(self):
        changed = _changed(self.files, "FakeOrderService.java", "return result;",
                           "result.add(orders.get(0));\n        return result;")
        self.assertEqual(0, _order_count(changed))


if __name__ == "__main__":
    unittest.main()
