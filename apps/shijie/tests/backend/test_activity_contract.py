from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from test_backend import SOURCE, fixtures, Store


class ActivityContractTests(unittest.TestCase):
    def test_single_hint_pack_exposes_count_without_leaking_hint_text(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), fixtures)
            project = store.upload([{"path": "Orders.java", "text": SOURCE}], "contract")
            pid = project["project"]["id"]
            original = store.backup()["data"]["activities"][0]["data"]
            task = deepcopy(original)
            task.update(id="one-hint-contract", hints=["Only one hint is available."])
            preview = store.preview_pack(pid, {"schema_version": 1, "project_version": 1, "activities": [task]})
            store.commit_pack(pid, preview["draft_id"], True)
            detail = store.activity_detail(pid, task["id"])
            self.assertEqual(detail["activity"]["hint_count"], 1)
            self.assertNotIn("hints", detail["activity"])
            self.assertEqual(detail["hints_requested"], [])
            store.hint(pid, task["id"], 1)
            self.assertEqual(len(store.activity_detail(pid, task["id"])["hints_requested"]), 1)


if __name__ == "__main__":
    unittest.main()
