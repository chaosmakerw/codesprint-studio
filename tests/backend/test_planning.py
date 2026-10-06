"""Real SQLite/loopback HTTP task progress, import atomicity and backup compatibility."""
from __future__ import annotations

import copy
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from codesprint.planning import PlanningStore
from codesprint.quiz import QuizStore
from codesprint.server import Application, make_server
from codesprint.storage import Library, dumps


def backup_entries(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def write_backup(entries, manifest):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, dumps(manifest) if name == "backup.json" else data)
    return stream.getvalue()


class PlanningTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="shizhi-plan-test-")
        self.app = Application(self.temp.name)
        self.app.import_demo({"confirmed": True})
        self.source = next(item for item in self.app.library.listing()["items"] if item["source_key"] == "demo:java-collections")
        self.plan = self.app.planning
        self.payload = {"id": "day-01-java", "date": "2026-10-06", "title": "阅读集合与运行一轮练习", "topic": "java",
                        "material_id": self.source["id"], "notes": "先口述，再对照资料核对。", "estimated_minutes": 45}

    def tearDown(self):
        self.temp.cleanup()

    def create(self):
        return self.plan.create(self.payload)

    def finished_session(self, identity):
        session = self.app.quiz.start({"task_id": identity, "count": 1})
        with self.app.library.connect() as conn:
            row = conn.execute("SELECT questions FROM quiz_sessions WHERE id=?", (session["id"],)).fetchone()
        question = json.loads(row["questions"])[0]
        return self.app.quiz.submit(session["id"], {"question_id": question["id"], "selected": question["answer"]})

    def test_manual_progress_persists_and_can_be_restored(self):
        task = self.create()
        self.assertFalse(task["completed"])
        self.assertTrue(task["material_available"])
        task = self.plan.patch(task["id"], {"completed": True, "actual_minutes": 50})
        self.assertIsNotNone(task["completed_at"])
        reopened = PlanningStore(Library(self.temp.name))
        self.assertTrue(reopened.get(task["id"])["completed"])
        summary = reopened.summary("2026-10-06")
        self.assertEqual((summary["total"], summary["completed"], summary["pending"], summary["planned_minutes"], summary["actual_minutes"]), (1, 1, 0, 45, 50))
        restored = reopened.patch(task["id"], {"completed": False})
        self.assertIsNone(restored["completed_at"])
        self.assertEqual(reopened.listing(status="pending", search="集合")["total"], 1)
        self.assertEqual(reopened.listing(status="completed")["total"], 0)

    def test_real_quiz_is_bound_to_task_without_automatic_completion(self):
        task = self.create()
        result = self.finished_session(task["id"])
        self.assertEqual(result["task_id"], task["id"])
        self.assertTrue(result["result"]["passed"])
        self.assertEqual(result["topic"], "java")
        task = self.plan.get(task["id"])
        self.assertFalse(task["completed"])
        self.assertEqual(task["actual_minutes"], 0)
        self.assertEqual((task["quiz"]["sessions"], task["quiz"]["finished"], task["quiz"]["total"], task["quiz"]["percent"]), (1, 1, 1, 100))
        self.assertNotIn("questions", task["quiz"]["recent"][0])
        self.assertEqual(self.plan.summary(task["date"])["quiz"]["passed_sessions"], 1)

    def test_binding_rejects_unknown_conflicting_or_unavailable_targets(self):
        task = self.create()
        for payload in ({"task_id": "unknown", "count": 1}, {"task_id": task["id"], "topic": "data"},
                        {"task_id": task["id"], "material": "0" * 32}):
            with self.assertRaises((KeyError, ValueError)):
                self.app.quiz.start(payload)
        self.app.library.patch(self.source["id"], {"trashed": True})
        self.assertFalse(self.plan.get(task["id"])["material_available"])
        with self.assertRaises(ValueError):
            self.app.quiz.start({"task_id": task["id"]})
        with self.app.library.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM quiz_sessions").fetchone()[0], 0)

    def test_changes_keep_history_and_general_tasks_allow_focused_practice(self):
        task = self.create()
        self.finished_session(task["id"])
        for change in ({"topic": "data"}, {"material_id": ""}):
            with self.assertRaises(ValueError):
                self.plan.patch(task["id"], change)
        task = self.plan.patch(task["id"], {"title": "复盘集合", "date": "2026-10-07"})
        self.assertEqual(task["quiz"]["finished"], 1)
        general = self.plan.create({"title": "自由复习", "date": "2026-10-06"})
        self.assertTrue(general["material_available"])
        session = self.app.quiz.start({"task_id": general["id"], "topic": "spring", "count": 1})
        self.assertEqual(session["topic"], "spring")

    def test_invalid_tasks_and_patch_are_rejected_without_writes(self):
        cases = [{"date": "2026-02-30"}, {"date": "2026-1-1"}, {"title": " "}, {"topic": "unknown"},
                 {"material_id": "0" * 32}, {"id": "../bad"}, {"notes": "x" * 6001}, {"estimated_minutes": True},
                 {"estimated_minutes": 721}, {"completed": True}, {"actual_minutes": 1}, {"actual_minutes": False}, {"extra": "bad"}]
        for change in cases:
            with self.assertRaises(ValueError):
                self.plan.create(dict(self.payload, **change))
        self.assertEqual(self.plan.listing()["total"], 0)
        task = self.create()
        for change in ({"completed": 1}, {"actual_minutes": True}, {"actual_minutes": 1441}, {"created_at": 0}, {"topic": "unknown"}):
            with self.assertRaises(ValueError):
                self.plan.patch(task["id"], change)
        self.assertFalse(self.plan.get(task["id"])["completed"])

    def test_import_merges_without_overwriting_existing_user_progress(self):
        task = self.create()
        self.plan.patch(task["id"], {"completed": True, "actual_minutes": 60, "notes": "我已经自己讲过这部分"})
        payload = {"schema": "shizhi-plan-v1", "confirmed": True, "tasks": [dict(self.payload, title="AI 的另一个标题"), dict(self.payload, id="day-02-java", date="2026-10-07")]}
        first = self.plan.import_tasks(payload)
        self.assertEqual(first, {"added": 1, "skipped": 1, "total": 2})
        self.assertEqual(self.plan.import_tasks(payload), {"added": 0, "skipped": 2, "total": 2})
        existing = self.plan.get(task["id"])
        self.assertEqual(existing["title"], self.payload["title"])
        self.assertTrue(existing["completed"])
        self.assertEqual(existing["actual_minutes"], 60)
        self.assertEqual(existing["notes"], "我已经自己讲过这部分")

    def test_import_invalid_late_row_does_not_write_early_valid_row(self):
        for late in (dict(self.payload, id="late", date="2026-02-30"), dict(self.payload, id="late", material_id="0" * 32),
                     dict(self.payload, id="late", completed=True)):
            with self.assertRaises(ValueError):
                self.plan.import_tasks({"schema": "shizhi-plan-v1", "confirmed": True, "tasks": [self.payload, late]})
            self.assertEqual(self.plan.listing()["total"], 0)
        for change in ({"confirmed": False}, {"version": True}, {"schema": "bad"}, {"tasks": [self.payload] * 101}, {"tasks": [self.payload] * 2}):
            payload = {"schema": "shizhi-plan-v1", "confirmed": True, "tasks": [self.payload]}
            payload.update(change)
            with self.assertRaises(ValueError):
                self.plan.import_tasks(payload)
        self.assertEqual(self.plan.listing()["total"], 0)

    def test_backup_v2_restores_tasks_session_links_and_trashed_history(self):
        task = self.create()
        session = self.finished_session(task["id"])
        self.plan.patch(task["id"], {"completed": True, "actual_minutes": 45})
        self.app.library.patch(self.source["id"], {"trashed": True})
        backup = self.app.library.backup()
        manifest = json.loads(backup_entries(backup)["backup.json"])
        self.assertEqual(manifest["schema"], "codesprint-backup-v2")
        with tempfile.TemporaryDirectory() as target:
            restored = Application(target)
            result = restored.library.restore(backup)
            self.assertEqual(result["tasks"], 1)
            self.assertTrue(restored.planning.get(task["id"])["completed"])
            self.assertFalse(restored.planning.get(task["id"])["material_available"])
            self.assertEqual(restored.quiz.get(session["id"])["task_id"], task["id"])
            self.assertEqual(restored.planning.get(task["id"])["quiz"]["percent"], 100)

    def test_backup_rejects_forged_completion_or_orphan_and_conflicting_session(self):
        task = self.create()
        session = self.finished_session(task["id"])
        backup = self.app.library.backup()
        entries = backup_entries(backup)
        baseline = json.loads(entries["backup.json"])
        def orphan(manifest):
            manifest["tables"]["quiz_sessions"][0]["task_id"] = "missing-task"
        def topic(manifest):
            manifest["tables"]["study_tasks"][0]["topic"] = "data"
        def material(manifest):
            alternate = next(item for item in self.app.library.listing()["items"] if item["id"] != self.source["id"])
            manifest["tables"]["study_tasks"][0]["material_id"] = alternate["id"]
        def completion(manifest):
            manifest["tables"]["study_tasks"][0]["completed"] = 1
        def field(manifest):
            manifest["tables"]["study_tasks"][0]["private_extra"] = "bad"
        def source(manifest):
            row = manifest["tables"]["quiz_sessions"][0]
            questions = json.loads(row["questions"])
            del questions[0]["source"]
            row["questions"] = dumps(questions)
        def timestamp(manifest):
            manifest["tables"]["quiz_sessions"][0]["created_at"] = 0
        for mutate in (orphan, topic, material, completion, field, source, timestamp):
            manifest = copy.deepcopy(baseline)
            mutate(manifest)
            with self.assertRaises(ValueError):
                self.app.library.restore(write_backup(entries, manifest))
            self.assertFalse(self.plan.get(task["id"])["completed"])
            self.assertEqual(self.app.quiz.get(session["id"])["result"]["percent"], 100)

    def test_old_v1_zip_schema_restores_without_task_or_new_session_columns(self):
        session = self.app.quiz.start({"count": 1, "topic": "java"})
        self.app.quiz.finish(session["id"])
        entries = backup_entries(self.app.library.backup())
        old = json.loads(entries["backup.json"])
        old["schema"] = "codesprint-backup-v1"
        del old["tables"]["study_tasks"]
        for row in old["tables"]["quiz_sessions"]:
            del row["task_id"]
        self.assertEqual(set(old["tables"]), {"materials", "questions", "generation_drafts", "quiz_sessions", "quiz_review"})
        self.assertEqual(set(old["tables"]["quiz_sessions"][0]), {"id", "mode", "topic", "scope", "questions", "answers", "created_at", "finished_at"})
        self.create()
        result = self.app.library.restore(write_backup(entries, old))
        self.assertEqual(result["tasks"], 0)
        self.assertEqual(self.plan.listing()["total"], 0)
        self.assertEqual(self.app.quiz.get(session["id"])["task_id"], "")
        self.assertTrue(self.app.quiz.get(session["id"])["finished"])
        with self.app.library.connect() as conn:
            self.assertIn("task_id", {row["name"] for row in conn.execute("PRAGMA table_info(quiz_sessions)")})

    def test_old_live_sqlite_schema_is_migrated_without_erasing_sessions(self):
        session = self.app.quiz.start({"count": 1})
        with self.app.library.connect() as conn:
            row = dict(conn.execute("SELECT * FROM quiz_sessions WHERE id=?", (session["id"],)).fetchone())
            conn.execute("DROP TABLE quiz_sessions")
            conn.execute("CREATE TABLE quiz_sessions (id TEXT PRIMARY KEY, mode TEXT NOT NULL, topic TEXT NOT NULL, scope TEXT NOT NULL, questions TEXT NOT NULL, answers TEXT NOT NULL, created_at INTEGER NOT NULL, finished_at INTEGER)")
            old_columns = ["id", "mode", "topic", "scope", "questions", "answers", "created_at", "finished_at"]
            conn.execute("INSERT INTO quiz_sessions VALUES (?,?,?,?,?,?,?,?)", [row[k] for k in old_columns])
        quiz = QuizStore(self.app.library)
        self.assertEqual(quiz.get(session["id"])["answered"], 0)
        self.assertEqual(quiz.get(session["id"])["task_id"], "")


class PlanningHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="shizhi-plan-http-")
        self.server = make_server(0, self.temp.name)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = self.request("GET", "/api/bootstrap")[1]["token"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, method, path, payload=None, token=None):
        headers = {"Content-Type": "application/json", "X-Archive-Token": token if token is not None else getattr(self, "token", "")}
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, dumps(payload).encode() if payload is not None else None, headers)
            response = connection.getresponse()
            data = response.read()
            return response.status, json.loads(data)
        finally:
            connection.close()

    def test_real_http_create_edit_import_and_summary_token_guard(self):
        payload = {"date": "2026-10-06", "title": "我的资料计划", "topic": "java"}
        self.assertEqual(self.request("POST", "/api/planning/tasks", payload, "wrong")[0], 403)
        status, task = self.request("POST", "/api/planning/tasks", payload)
        self.assertEqual(status, 201)
        path = "/api/planning/tasks/" + task["id"]
        self.assertEqual(self.request("GET", path)[1]["title"], payload["title"])
        self.assertTrue(self.request("PATCH", path, {"completed": True, "actual_minutes": 35})[1]["completed"])
        imported = self.request("POST", "/api/planning/import", {"schema": "shizhi-plan-v1", "confirmed": True, "tasks": [dict(payload, id="day-02")]})
        self.assertEqual(imported[1]["added"], 1)
        summary = self.request("GET", "/api/planning/summary?date=2026-10-06")[1]
        self.assertEqual((summary["total"], summary["completed"], summary["actual_minutes"]), (2, 1, 35))
        self.assertEqual(self.request("GET", "/api/planning/tasks?status=pending")[1]["total"], 1)
        self.assertEqual(self.request("PATCH", path, {"completed": "yes"})[0], 400)
        self.assertEqual(self.request("GET", "/api/planning/tasks/missing")[0], 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
