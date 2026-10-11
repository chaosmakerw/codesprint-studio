"""Create a deterministic, original-example mobile seed without opening user data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from projectstudy.store import Store

RELEASE_TIME = "2026-10-11T00:00:00+00:00"
EXAMPLES = (("java-demo", "sample-java-orders"), ("python-notes", "sample-python-notes"))
PERSONAL_TABLES = ("attempts", "hints", "evidence", "checks", "reviews", "review_settings")


def build(destination: Path):
    with tempfile.TemporaryDirectory(prefix="shijie-original-seed-") as temporary:
        store = Store(Path(temporary) / "source")
        mapping = {}
        for folder, sample_id in EXAMPLES:
            bundle = json.loads((ROOT / "examples" / folder / "learning-bundle.json").read_text(encoding="utf-8"))
            preview = store.preview_bundle(bundle)
            detail = store.commit_bundle(preview["draft_id"], True)
            mapping[detail["project"]["id"]] = sample_id
        backup = store.backup()
        backup["exported_at"] = RELEASE_TIME
        backup["privacy"] = "仅包含本仓库原创教学示例；不包含个人学习记录。"
        for project in backup["data"]["projects"]:
            project["id"] = mapping[project["id"]]
            project["created_at"] = project["updated_at"] = RELEASE_TIME
        for table, records in backup["data"].items():
            if table == "projects":
                continue
            if table in PERSONAL_TABLES and records:
                raise ValueError("Example generation must not contain learner records")
            for index, row in enumerate(records):
                row["project_id"] = mapping[row["project_id"]]
                if "created_at" in row["data"]:
                    row["data"]["created_at"] = RELEASE_TIME
                if table == "project_summaries":
                    row["data"]["id"] = f"{row['project_id']}-summary-{index}"
        Store(Path(temporary) / "verify").restore(backup, True)
        objective = sum(bool(row["data"].get("options") and row["data"].get("answer_ids"))
                        for row in backup["data"]["activities"])
        if objective != 31 or len(backup["data"]["projects"]) != 2:
            raise ValueError("Example content count changed; review before redistributing")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(backup, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(f"Original seed: 2 projects, {objective} objective questions; personal records: 0")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "examples" / "mobile-seed.json")
    build(parser.parse_args().output)
