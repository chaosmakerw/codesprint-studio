"""User-confirmed study tasks stored alongside materials and actual quiz sessions."""
from __future__ import annotations

from datetime import date
import re
import time
import uuid

from .quiz import TOPICS, QuizStore
from .storage import clean_string

MAX_TASKS = 1000
MAX_IMPORT = 100
EDIT_FIELDS = {"date", "title", "topic", "material_id", "notes", "estimated_minutes", "actual_minutes", "completed"}
IMPORT_FIELDS = {"id", "date", "title", "topic", "material_id", "notes", "estimated_minutes"}


def task_id(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-z0-9-]{1,80}", value) is None:
        raise ValueError("任务编号应为 1—80 位小写字母、数字或短横线。")
    return value


def task_date(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("任务日期应为有效的 YYYY-MM-DD。")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError("任务日期应为有效的 YYYY-MM-DD。") from None
    return value


def clean_task(payload, importing=False):
    allowed = IMPORT_FIELDS if importing else IMPORT_FIELDS | {"actual_minutes", "completed"}
    if not isinstance(payload, dict) or not set(payload) <= allowed or not {"date", "title"} <= set(payload):
        raise ValueError("任务字段无效，请至少填写标题和日期。")
    if importing and "id" not in payload:
        raise ValueError("导入任务需要稳定编号，重复导入才不会覆盖进度。")
    result = {"id": task_id(payload.get("id", uuid.uuid4().hex)), "date": task_date(payload["date"]),
              "title": clean_string(payload["title"], 160, "任务标题"), "topic": payload.get("topic", ""),
              "material_id": payload.get("material_id", ""), "notes": clean_string(payload.get("notes", ""), 6000, "任务笔记", True),
              "estimated_minutes": payload.get("estimated_minutes", 30), "actual_minutes": 0, "completed": False}
    if result["topic"] not in ("", *TOPICS):
        raise ValueError("任务领域无效。")
    if not isinstance(result["material_id"], str) or result["material_id"] and re.fullmatch(r"[a-f0-9]{32}", result["material_id"]) is None:
        raise ValueError("关联资料编号无效。")
    if type(result["estimated_minutes"]) is not int or not 0 <= result["estimated_minutes"] <= 720:
        raise ValueError("预计学习时间应为 0—720 分钟的整数。")
    # Imported/created tasks do not claim work has already been performed.
    if payload.get("completed", False) is not False or payload.get("actual_minutes", 0) != 0 or type(payload.get("actual_minutes", 0)) is not int:
        raise ValueError("新任务默认未完成，实际时间请完成学习后在页面记录。")
    return result


def validate_material(conn, material_id, available=True):
    if not material_id:
        return
    source = conn.execute("SELECT trashed,missing FROM materials WHERE id=?", (material_id,)).fetchone()
    if not source or available and (source["trashed"] or source["missing"]):
        raise ValueError("关联资料不存在或暂不可用，请先上传或恢复资料。")


def quiz_summary(rows):
    sessions = [QuizStore.public(dict(row)) for row in rows]
    finished = [s for s in sessions if s["finished"]]
    correct = sum(s["result"]["correct"] for s in finished)
    total = sum(s["result"]["total"] for s in finished)
    return {"sessions": len(sessions), "finished": len(finished), "correct": correct, "total": total,
            "percent": round(100 * correct / total, 1) if total else None,
            "passed_sessions": sum(s["result"]["passed"] for s in finished),
            "recent": [{k: v for k, v in s.items() if k != "questions"} for s in sessions[:5]]}


class PlanningStore:
    def __init__(self, library, clock=time.time):
        self.library = library
        self.clock = clock

    @staticmethod
    def public(row, conn):
        item = dict(row)
        item["completed"] = bool(item["completed"])
        source = conn.execute("SELECT trashed,missing,title FROM materials WHERE id=?", (item["material_id"],)).fetchone() if item["material_id"] else None
        item["material_available"] = not item["material_id"] or bool(source and not source["trashed"] and not source["missing"])
        item["material_title"] = source["title"] if source else ""
        rows = conn.execute("SELECT * FROM quiz_sessions WHERE task_id=? ORDER BY created_at DESC,rowid DESC", (item["id"],)).fetchall()
        item["quiz"] = quiz_summary(rows)
        return item

    def get(self, identity):
        task_id(identity)
        with self.library.connect() as conn:
            row = conn.execute("SELECT * FROM study_tasks WHERE id=?", (identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            return self.public(row, conn)

    def listing(self, selected_date="", status="all", search=""):
        if selected_date:
            task_date(selected_date)
        if status not in ("all", "pending", "completed") or not isinstance(search, str) or len(search) > 200:
            raise ValueError("任务筛选条件无效。")
        conditions, params = [], []
        if selected_date:
            conditions.append("date=?")
            params.append(selected_date)
        if status != "all":
            conditions.append("completed=?")
            params.append(int(status == "completed"))
        with self.library.connect() as conn:
            rows = conn.execute("SELECT * FROM study_tasks" + (" WHERE " + " AND ".join(conditions) if conditions else "") + " ORDER BY date,created_at,rowid", params).fetchall()
            items = [self.public(row, conn) for row in rows if not search or search.casefold() in (row["title"] + row["notes"]).casefold()]
        return {"items": items, "total": len(items)}

    @staticmethod
    def insert(conn, item, now):
        conn.execute("INSERT INTO study_tasks (id,date,title,topic,material_id,notes,estimated_minutes,actual_minutes,completed,created_at,updated_at,completed_at) VALUES (?,?,?,?,?,?,?,0,0,?,?,NULL)",
                     (item["id"], item["date"], item["title"], item["topic"], item["material_id"], item["notes"], item["estimated_minutes"], now, now))

    def create(self, payload):
        item = clean_task(payload)
        with self.library.lock, self.library.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            validate_material(conn, item["material_id"])
            if conn.execute("SELECT id FROM study_tasks WHERE id=?", (item["id"],)).fetchone():
                raise ValueError("任务编号已经存在，请编辑原任务。")
            if conn.execute("SELECT COUNT(*) FROM study_tasks").fetchone()[0] >= MAX_TASKS:
                raise ValueError("最多保存 1000 项任务，请缩小计划范围。")
            self.insert(conn, item, int(self.clock()))
        return self.get(item["id"])

    def patch(self, identity, payload):
        task_id(identity)
        if not isinstance(payload, dict) or not payload or not set(payload) <= EDIT_FIELDS:
            raise ValueError("任务修改字段无效。")
        with self.library.lock, self.library.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM study_tasks WHERE id=?", (identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            row = dict(row)
            merged = {k: payload.get(k, row[k]) for k in IMPORT_FIELDS}
            checked = clean_task(merged, importing=True)
            if any(checked[k] != row[k] for k in ("topic", "material_id")):
                if conn.execute("SELECT id FROM quiz_sessions WHERE task_id=? LIMIT 1", (identity,)).fetchone():
                    raise ValueError("任务已有练习记录，请另建任务来更换领域或资料，保留原有历史。")
                validate_material(conn, checked["material_id"])
            minutes = payload.get("actual_minutes", row["actual_minutes"])
            completed = payload.get("completed", bool(row["completed"]))
            if type(minutes) is not int or not 0 <= minutes <= 1440 or type(completed) is not bool:
                raise ValueError("实际学习时间应为 0—1440 分钟整数，完成状态必须是布尔值。")
            now = int(self.clock())
            completed_at = row["completed_at"] if completed and row["completed"] else now if completed else None
            conn.execute("UPDATE study_tasks SET date=?,title=?,topic=?,material_id=?,notes=?,estimated_minutes=?,actual_minutes=?,completed=?,updated_at=?,completed_at=? WHERE id=?",
                         (checked["date"], checked["title"], checked["topic"], checked["material_id"], checked["notes"], checked["estimated_minutes"], minutes, int(completed), now, completed_at, identity))
        return self.get(identity)

    def import_tasks(self, payload):
        if not isinstance(payload, dict) or not set(payload) <= {"schema", "version", "confirmed", "tasks"} or payload.get("confirmed") is not True:
            raise ValueError("请预览学习计划并明确确认导入。")
        valid_schema = payload.get("schema") == "shizhi-plan-v1" and "version" not in payload
        valid_version = type(payload.get("version")) is int and payload["version"] == 1 and "schema" not in payload
        tasks = payload.get("tasks")
        if not (valid_schema or valid_version) or not isinstance(tasks, list) or not 1 <= len(tasks) <= MAX_IMPORT:
            raise ValueError("计划需要 shizhi-plan-v1 格式、1—100 项任务。")
        items = [clean_task(task, importing=True) for task in tasks]
        if len({task["id"] for task in items}) != len(items):
            raise ValueError("导入计划中的任务编号重复。")
        now = int(self.clock())
        with self.library.lock, self.library.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Even a skipped entry is checked; an invalid late row cannot hide behind an existing ID.
            for item in items:
                validate_material(conn, item["material_id"])
            existing = {row["id"] for row in conn.execute("SELECT id FROM study_tasks")}
            added = [item for item in items if item["id"] not in existing]
            if len(existing) + len(added) > MAX_TASKS:
                raise ValueError("导入后任务超过 1000 项，请缩小计划范围。")
            for item in added:
                self.insert(conn, item, now)
        return {"added": len(added), "skipped": len(items) - len(added), "total": len(existing) + len(added)}

    def summary(self, selected_date=""):
        selected_date = task_date(selected_date or date.today().isoformat())
        with self.library.connect() as conn:
            rows = conn.execute("SELECT * FROM study_tasks WHERE date=?", (selected_date,)).fetchall()
            sessions = conn.execute("SELECT s.* FROM quiz_sessions s JOIN study_tasks t ON t.id=s.task_id WHERE t.date=? ORDER BY s.created_at DESC,s.rowid DESC", (selected_date,)).fetchall()
            return {"date": selected_date, "total": len(rows), "completed": sum(r["completed"] for r in rows),
                    "pending": sum(not r["completed"] for r in rows), "planned_minutes": sum(r["estimated_minutes"] for r in rows),
                    "actual_minutes": sum(r["actual_minutes"] for r in rows), "quiz": quiz_summary(sessions)}


def validate_task_row(row, conn):
    """Full validation of backup tasks, including explicit completion and references."""
    checked = clean_task({k: row[k] for k in IMPORT_FIELDS}, importing=True)
    if any(checked[k] != row[k] for k in IMPORT_FIELDS):
        raise ValueError("备份任务内容格式无效。")
    if type(row["actual_minutes"]) is not int or not 0 <= row["actual_minutes"] <= 1440 or type(row["completed"]) is not int or row["completed"] not in (0, 1):
        raise ValueError("备份任务学习记录无效。")
    if any(type(row[k]) is not int or row[k] < 0 for k in ("created_at", "updated_at")) or row["updated_at"] < row["created_at"]:
        raise ValueError("备份任务时间无效。")
    completed_at = row["completed_at"]
    if row["completed"]:
        if type(completed_at) is not int or not row["created_at"] <= completed_at <= row["updated_at"]:
            raise ValueError("备份任务完成时间无效。")
    elif completed_at is not None:
        raise ValueError("备份未完成任务不能包含完成时间。")
    validate_material(conn, row["material_id"], available=False)
