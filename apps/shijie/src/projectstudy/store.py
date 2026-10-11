"""Versioned project snapshots, append-only learner evidence and atomic local storage."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid

from .analysis import InputError, analyze, collect_upload, relative_path, scan_directory, source_hash
from .contracts import flow_contract, validate_concepts, validate_evidence, validate_flows, validate_project_summary, SUMMARY_FIELDS
from .checks import CheckMixin
from .reviews import ReviewMixin

MAX_BACKUP_BYTES = 32 * 1024 * 1024
MAX_BODY_BYTES = MAX_BACKUP_BYTES + 1024 * 1024
MAX_PACK_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = MAX_BACKUP_BYTES
INTAKE_PROJECT = "__intake__"
LEGACY_TABLES = ("projects", "files", "concepts", "activities", "attempts", "hints", "evidence")
TABLES = (*LEGACY_TABLES, "checks", "flows", "project_summaries", "reviews", "review_settings")
ID = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")
KINDS = {"predict", "trace", "debug", "reproduce", "transfer"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def text(value, limit=12000, *, empty=False) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value or (not empty and not value.strip()):
        raise InputError("文本内容为空或超过允许长度。")
    return value.strip()


def identity(value) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise InputError("标识符格式不正确。")
    return value


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
    except (TypeError, ValueError):
        raise InputError("记录时间格式不正确。") from None
    return value


class Store(CheckMixin, ReviewMixin):
    def __init__(self, data_dir: Path, curriculum_builder=None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "study.sqlite3"
        self.lock = threading.RLock()
        if curriculum_builder is None:
            from .curriculum import build_curriculum
            curriculum_builder = build_curriculum
        self.curriculum_builder = curriculum_builder
        with self._connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS files(project_id TEXT NOT NULL,path TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(project_id,path));
                CREATE TABLE IF NOT EXISTS concepts(project_id TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(project_id,id));
                CREATE TABLE IF NOT EXISTS activities(project_id TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(project_id,id));
                CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS hints(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checks(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS flows(project_id TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(project_id,id));
                CREATE TABLE IF NOT EXISTS project_summaries(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS review_settings(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS builtin_imports(origin TEXT NOT NULL,bundle_hash TEXT NOT NULL,project_id TEXT NOT NULL,PRIMARY KEY(origin,bundle_hash));
            """)

    @contextmanager
    def _connection(self, write=False):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                if write:
                    db.execute("BEGIN IMMEDIATE")
                yield db
                if write:
                    # A backup accepted today must also be restorable today.
                    if len(dumps(self._backup(db)).encode()) > MAX_BACKUP_BYTES:
                        raise InputError("资料与记录已达到安全备份容量，请先导出备份并整理项目。")
                    db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    def _rows(self, db, table, pid=None):
        sql = f"SELECT payload FROM {table}"
        args = ()
        if pid is not None:
            sql += " WHERE project_id=?"
            args = (pid,)
        sql += " ORDER BY rowid"
        return [json.loads(row[0]) for row in db.execute(sql, args)]

    def _project(self, db, pid):
        row = db.execute("SELECT payload FROM projects WHERE id=?", (identity(pid),)).fetchone()
        if not row:
            raise InputError("项目不存在。")
        return json.loads(row[0])

    @staticmethod
    def _public_project(project):
        return {k: v for k, v in project.items() if k != "root"}

    def list_projects(self):
        with self._connection() as db:
            return [self._public_project(p) for p in self._rows(db, "projects")]

    def _activity(self, db, pid, aid):
        row = db.execute("SELECT payload FROM activities WHERE project_id=? AND id=?", (pid, identity(aid))).fetchone()
        if not row:
            raise InputError("活动不存在或已不属于当前源码版本。")
        return json.loads(row[0])

    def _insert(self, db, table, value, pid=None):
        payload = dumps(value)
        if table == "projects":
            db.execute("INSERT OR REPLACE INTO projects VALUES (?,?)", (value["id"], payload))
        elif table == "files":
            db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?)", (pid, value["path"], payload))
        elif table in {"concepts", "activities", "flows"}:
            db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?)", (pid, value["id"], payload))
        else:
            db.execute(f"INSERT INTO {table} VALUES (?,?,?)", (value["id"], pid, payload))

    def scan(self, path, name=None, project_id=None):
        files, report, root = scan_directory(path)
        return self.import_files(files, name, project_id, root=root, report=report)

    def import_selected_once(self, path):
        try:
            selected = str(Path(path).expanduser().resolve(strict=True))
        except (OSError, RuntimeError, TypeError):
            raise InputError("项目文件夹不可读取。") from None
        with self._connection() as db:
            old = next((p for p in self._rows(db, "projects") if p.get("root") == selected), None)
            if old:
                return self._detail(db, old["id"])
        return self.scan(path)

    def upload(self, files, name=None, project_id=None):
        cleaned, report = collect_upload(files)
        return self.import_files(cleaned, name, project_id, report=report)

    def import_files(self, files, name=None, project_id=None, *, root=None, report=None):
        if name is not None:
            name = text(name, 80)
            if any(x in name for x in ("\\", "/", ":")):
                raise InputError("项目名称不能包含本机路径。")
        fingerprint = source_hash(files)
        concepts = analyze(files)
        activities = self.curriculum_builder(files, concepts)
        with self._connection(True) as db:
            if not project_id and root:
                match = next((p for p in self._rows(db, "projects") if p.get("root") == root), None)
                project_id = match["id"] if match else None
            old = self._project(db, project_id) if project_id else None
            if not old and db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] >= 12:
                raise InputError("最多保存 12 个项目，请先备份并整理旧项目。")
            pid = old["id"] if old else uuid.uuid4().hex
            changed = not old or old["source_hash"] != fingerprint
            version = old["version"] + 1 if old and changed else old["version"] if old else 1
            project = {"id": pid, "name": name or (old["name"] if old else "我的项目"),
                       "version": version, "source_hash": fingerprint, "file_count": len(files),
                       "created_at": old["created_at"] if old else now(), "updated_at": now(),
                       "root": root if root is not None else (old.get("root") if old else None),
                       "scan_report": report or {"included": len(files), "skipped": 0}}
            self._insert(db, "projects", project)
            if changed:
                for table in ("files", "concepts", "activities", "flows", "drafts"):
                    db.execute(f"DELETE FROM {table} WHERE project_id=?", (pid,))
                for f in files:
                    self._insert(db, "files", f, pid)
                for concept in concepts:
                    self._insert(db, "concepts", concept, pid)
                checked = self._validate_activities(activities, concepts, files, maximum=150)
                for activity in checked:
                    activity["origin"] = "builtin"
                    self._insert(db, "activities", activity, pid)
            result = self._detail(db, pid)
            result["source_changed"] = changed
            return result

    def _validate_activities(self, activities, concepts, files, maximum=80, *, allow_empty=False):
        if not isinstance(activities, list) or not 1 <= len(activities) <= maximum:
            if activities == [] and (maximum == 150 or allow_empty):
                return []
            raise InputError("任务包应包含 1 至 80 个有效活动。")
        cids = {c["id"] for c in concepts}
        result, ids = [], set()
        allowed = {"id", "concept_id", "kind", "title", "prompt", "options", "answer_ids", "rubric", "explanation", "hints", "evidence", "due_after_hours", "option_mode", "multiple", "origin"}
        for raw in activities:
            if not isinstance(raw, dict) or set(raw) - allowed:
                raise InputError("任务字段不符合导入协议。")
            aid = identity(raw.get("id"))
            if aid in ids or raw.get("concept_id") not in cids or raw.get("kind") not in KINDS:
                raise InputError("任务标识、知识点或活动类型无效。")
            ids.add(aid)
            options = raw.get("options", [])
            if not isinstance(options, list) or len(options) > 8:
                raise InputError("选项数量无效。")
            opt_ids, checked_options = set(), []
            for option in options:
                if not isinstance(option, dict) or set(option) != {"id", "text"}:
                    raise InputError("选项必须包含稳定标识和文本。")
                oid = identity(option["id"])
                if oid in opt_ids:
                    raise InputError("选项标识不能重复。")
                opt_ids.add(oid)
                checked_options.append({"id": oid, "text": text(option["text"], 3000)})
            answers = raw.get("answer_ids", [])
            if not isinstance(answers, list) or any(not isinstance(x, str) or x not in opt_ids for x in answers) or len(set(answers)) != len(answers):
                raise InputError("标准答案必须引用有效选项。")
            if bool(options) != bool(answers) or (options and len(options) < 2):
                raise InputError("选择题需要有效标准答案与至少两个选项。")
            checked_evidence = validate_evidence(raw.get("evidence", []), files)
            rubric, hints = raw.get("rubric", []), raw.get("hints", [])
            if not isinstance(rubric, list) or not 1 <= len(rubric) <= 12 or not isinstance(hints, list) or not 1 <= len(hints) <= 3:
                raise InputError("活动需要验收标准和 1 至 3 级提示。")
            delay = raw.get("due_after_hours", 48 if raw["kind"] == "transfer" else 0)
            if type(delay) not in (int, float) or delay < 0 or delay > 168:
                raise InputError("延迟复习时间无效。")
            result.append({"id": aid, "concept_id": raw["concept_id"], "kind": raw["kind"],
                           "title": text(raw.get("title"), 200), "prompt": text(raw.get("prompt"), 16000),
                           "options": checked_options, "answer_ids": answers,
                           "rubric": [text(s, 3000) for s in rubric], "explanation": text(raw.get("explanation", ""), 12000, empty=True),
                           "hints": [text(s, 5000) for s in hints], "evidence": checked_evidence,
                           "due_after_hours": delay, "option_mode": "multiple" if len(answers) > 1 else "single" if options else "text"})
        return result

    def _validate_concepts(self, concepts, files, existing=(), *, legacy_static=False):
        return validate_concepts(concepts, files, identity, text, existing, legacy_static=legacy_static)

    @staticmethod
    def _activity_quality_warnings(activities):
        counts = {"缺少解析": 0, "所有选项均为正确答案": 0, "存在重复选项文本": 0}
        for activity in activities:
            if not activity["options"]:
                continue
            counts["缺少解析"] += not bool(activity["explanation"].strip())
            counts["所有选项均为正确答案"] += len(activity["answer_ids"]) == len(activity["options"])
            option_texts = [" ".join(option["text"].split()).casefold() for option in activity["options"]]
            counts["存在重复选项文本"] += len(set(option_texts)) != len(option_texts)
        return [f"题目质量提示：{count} 道选择题{issue}；请审阅后再写入，不用题量代替有效练习。" for issue, count in counts.items() if count]

    def _validate_flows(self, flows, concepts, files, existing=()):
        return validate_flows(flows, concepts, files, identity, text, existing)

    def _save_project_summary(self, db, pid, summary):
        if summary is not None:
            if db.execute("SELECT COUNT(*) FROM project_summaries").fetchone()[0] >= 10000:
                raise InputError("项目总结版本记录已达到上限，请备份并整理项目。")
            project = self._project(db, pid)
            self._insert(db, "project_summaries", {"id": uuid.uuid4().hex, "version": project["version"],
                         "source_hash": project["source_hash"], "created_at": now(), "content": summary}, pid)

    def _project_summary(self, db, pid, project, flows):
        records = [r for r in self._rows(db, "project_summaries", pid)
                   if r["version"] == project["version"] and r["source_hash"] == project["source_hash"]]
        if records:
            return {**records[-1]["content"], "origin": "agent_proposed", "version": project["version"],
                    "source_hash": project["source_hash"]}
        files = self._rows(db, "files", pid)
        readmes = [f for f in files if f["path"].lower().split("/")[-1] in {"readme.md", "readme.txt"}]
        readme = min(readmes, key=lambda f: (f["path"].count("/"), f["path"].lower()), default=None)
        excerpt = readme["text"][:6000] if readme else "尚无经过整理的项目作用说明。请让 Agent 依据真实项目补齐。"
        return {"purpose": excerpt, "audience": "尚未整理；不能仅凭代码推断实际用户。",
                "scenario": flows[0]["scenario"] if flows else "尚未整理使用场景。",
                "capabilities": [f["title"] for f in flows] or ["尚未整理功能调用链"],
                "learning_value": "可从已有功能学习输入、执行顺序、返回值及失败边界；具体知识以证据绑定的知识点为准。",
                "limitations": "当前是 README 摘录与已有教学链的保守回退；静态源码不能证明真实运行、生产能力或本人掌握。",
                "evidence": ([{"path": readme["path"], "line": 1, "quote": readme["text"].splitlines()[0][:800], "sha256": readme["sha256"]}]
                             if readme and readme["text"].splitlines() and readme["text"].splitlines()[0] else []),
                "origin": "readme_fallback" if readme else "missing", "version": project["version"], "source_hash": project["source_hash"]}

    def _flow_updates(self, updates, concepts, files, existing):
        if not isinstance(updates, list) or len(updates) > 12:
            raise InputError("调用链补充数量无效。")
        current = {f["id"]: f for f in existing}
        checked = self._validate_flows(updates, concepts, files)
        for flow in checked:
            old = current.get(flow["id"])
            if not old or [s["id"] for s in old["steps"]] != [s["id"] for s in flow["steps"]]:
                raise InputError("补充调用链必须保留已有调用链标识及步骤顺序。")
        return checked

    def _concept_updates(self, updates, files, existing):
        if not isinstance(updates, list) or len(updates) > 80:
            raise InputError("知识讲解补充数量无效。")
        current = {concept["id"]: concept for concept in existing}
        mutable = {"lesson", "mechanism", "failures", "references"}
        ids = []
        for update in updates:
            if not isinstance(update, dict):
                raise InputError("知识讲解补充应为完整知识点对象。")
            cid = identity(update.get("id"))
            old = current.get(cid)
            if not old or cid in ids:
                raise InputError("补充知识点必须已存在且不能重复；新知识点使用 concepts。")
            ids.append(cid)
            if set(update) - mutable != set(old) - mutable or not mutable <= set(update):
                raise InputError("知识讲解补充必须保留全部原字段，并提供 lesson、mechanism、failures、references。")
            if any(update[key] != value for key, value in old.items() if key not in mutable):
                raise InputError("补充不能更改知识点标识、分类、前置关系、原证据或其他基础字段。")
            for key in ("lesson", "mechanism"):
                value, previous = update[key], old.get(key, "")
                if not isinstance(value, str) or not value.startswith(previous):
                    raise InputError("补充讲解应保留原文并在其后追加内容。")
            for key in ("failures", "references"):
                value, previous = update[key], old.get(key, [])
                if not isinstance(value, list) or value[:len(previous)] != previous:
                    raise InputError("补充错误示例与参考来源必须保留原有条目和顺序。")
            if update == old:
                raise InputError("知识讲解补充没有新增内容。")
        remaining = [concept for concept in existing if concept["id"] not in ids]
        # Historical static candidates can have unresolved prerequisites. They
        # remain historical: this operation cannot change their prerequisite IDs.
        return self._validate_concepts(updates, files, remaining, legacy_static=True)

    @staticmethod
    def _require_content(concepts, activities, flows, project_summary=None, flow_updates=(), concept_updates=()):
        if not (concepts or activities or flows or project_summary or flow_updates or concept_updates):
            raise InputError("学习包至少应新增一个知识点、场景调用链或活动。")

    def _history(self, db, pid, aid=None):
        project = self._project(db, pid)
        records = self._rows(db, "attempts", pid)
        if aid:
            records = [r for r in records if r["activity_id"] == aid]
        return [{**r, "stale": r["version"] != project["version"]} for r in records]

    def _summary_activity(self, db, pid, activity, history=None):
        project = self._project(db, pid)
        history = history if history is not None else self._history(db, pid, activity["id"])
        history = [r for r in history if r["activity_id"] == activity["id"]]
        current = [r for r in history if not r["stale"]]
        due_at = None
        if activity.get("due_after_hours"):
            # Delay starts from the learner's first attempt in this concept, not import time.
            related = [r for r in self._history(db, pid) if not r["stale"] and r.get("concept_id") == activity["concept_id"] and r["kind"] != "transfer"]
            if related:
                base = datetime.fromisoformat(related[0]["created_at"])
                due_at = (base + timedelta(hours=activity["due_after_hours"])).isoformat(timespec="seconds")
        status = current[-1]["status"] if current else "stale" if history else "not_started"
        evidence = [e for e in self._rows(db, "evidence", pid) if e["activity_id"] == activity["id"] and e["version"] == project["version"]]
        return {"id": activity["id"], "concept_id": activity["concept_id"], "kind": activity["kind"],
                "title": activity["title"], "status": status, "due_at": due_at,
                "option_mode": activity.get("option_mode", "single"),
                "objective": bool(activity.get("options") and activity.get("answer_ids")),
                "delay_pending": bool(activity.get("due_after_hours") and (not due_at or due_at > now())),
                "attempt_count": len(history), "first_attempt": history[0] if history else None,
                "last_attempt": history[-1] if history else None, "evidence_count": len(evidence)}

    def _detail(self, db, pid):
        project = self._project(db, pid)
        concepts = self._rows(db, "concepts", pid)
        flows = self._rows(db, "flows", pid)
        history = self._history(db, pid)
        activities = [self._summary_activity(db, pid, a, history) for a in self._rows(db, "activities", pid)]
        materials = [{"path": f["path"], "sha256": f["sha256"]} for f in self._rows(db, "files", pid)
                     if f["path"].lower().endswith((".md", ".txt"))]
        return {"project": self._public_project(project), "project_summary": self._project_summary(db, pid, project, flows),
                "review_state": self._review_state(db, pid), "concepts": concepts, "flows": flows, "activities": activities, "materials": materials,
                "summary": {"concept_count": len(concepts), "flow_count": len(flows), "activity_count": len(activities),
                            "attempt_count": len(history), "review_required": sum(r["status"] == "review_required" and not r["stale"] for r in history),
                            "stale_attempt_count": sum(r["stale"] for r in history), "source_is_static": True,
                            "mastery_claim": "尚未验证独立掌握"}}

    def project_detail(self, pid):
        with self._connection() as db:
            return self._detail(db, pid)

    def activity_detail(self, pid, aid):
        with self._connection() as db:
            project, activity = self._project(db, pid), self._activity(db, pid, aid)
            public = {k: v for k, v in activity.items() if k not in {"answer_ids", "hints", "explanation"}}
            public.update(self._summary_activity(db, pid, activity))
            public["hint_count"] = len(activity["hints"])
            hints = [r for r in self._rows(db, "hints", pid) if r["activity_id"] == aid and r["version"] == project["version"]]
            evidence = [{**r, "stale": r["version"] != project["version"]} for r in self._rows(db, "evidence", pid) if r["activity_id"] == aid]
            return {"activity": public, "history": self._history(db, pid, aid), "hints_requested": hints, "evidence": evidence}

    def source(self, pid, path):
        with self._connection() as db:
            project = self._project(db, pid)
            row = db.execute("SELECT payload FROM files WHERE project_id=? AND path=?", (pid, relative_path(path))).fetchone()
            if not row:
                raise InputError("源码快照不存在。")
            return {**json.loads(row[0]), "version": project["version"]}

    def submit(self, pid, aid, answer, assistance="none"):
        if assistance not in {"none", "agent", "reference"}:
            raise InputError("辅助方式不正确。")
        with self._connection(True) as db:
            return self._submit(db, pid, aid, answer, assistance)

    def _submit(self, db, pid, aid, answer, assistance="none"):
        project, activity = self._project(db, pid), self._activity(db, pid, aid)
        if db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] >= 10000:
            raise InputError("记录已达到上限，请备份并整理项目。")
        if activity["options"]:
            if not isinstance(answer, list) or not answer or any(not isinstance(x, str) for x in answer) or len(answer) != len(set(answer)):
                raise InputError("请选择有效选项。")
            options = {o["id"] for o in activity["options"]}
            if set(answer) - options or (activity["option_mode"] == "single" and len(answer) != 1):
                raise InputError("选项标识或数量不正确。")
            score = 100 if set(answer) == set(activity["answer_ids"]) else 0
            status = "correct" if score == 100 else "incorrect"
        else:
            answer = text(answer)
            score, status = None, "review_required"
        hints = [r for r in self._rows(db, "hints", pid) if r["activity_id"] == aid and r["version"] == project["version"]]
        summary = self._summary_activity(db, pid, activity)
        attempt = {"id": uuid.uuid4().hex, "activity_id": aid, "concept_id": activity["concept_id"],
                   "kind": activity["kind"], "answer": answer, "assistance": assistance,
                   "hint_level": max((r["level"] for r in hints), default=0), "version": project["version"],
                   "created_at": now(), "status": status, "score": score, "explanation": activity["explanation"],
                   "rubric": activity["rubric"], "source_hash": project["source_hash"],
                   "assessment": {"option_ids": [o["id"] for o in activity["options"]], "answer_ids": activity["answer_ids"]},
                   "question_snapshot": {k: activity[k] for k in ("id", "concept_id", "kind", "title", "prompt", "options", "evidence")},
                   "delayed_retest": activity["kind"] == "transfer" and not summary["delay_pending"],
                   "stale": False, "mastery_claim": "选择题分数不代表独立掌握" if score is not None else "主观回答待人工复核"}
        self._insert(db, "attempts", attempt, pid)
        return {"attempt": attempt, "activity": self._summary_activity(db, pid, activity)}

    def hint(self, pid, aid, level):
        if type(level) is not int or level not in (1, 2, 3):
            raise InputError("提示级别应为 1 至 3。")
        with self._connection(True) as db:
            project, activity = self._project(db, pid), self._activity(db, pid, aid)
            if level > len(activity["hints"]):
                raise InputError("该活动没有这一层提示。")
            requested = [r for r in self._rows(db, "hints", pid) if r["activity_id"] == aid and r["version"] == project["version"]]
            previous = next((r for r in requested if r["level"] == level), None)
            if previous:
                return {**previous, "activity": self._summary_activity(db, pid, activity)}
            if level > max((r["level"] for r in requested), default=0) + 1:
                raise InputError("请按顺序逐级请求提示。")
            record = {"id": uuid.uuid4().hex, "activity_id": aid, "version": project["version"],
                      "level": level, "text": activity["hints"][level - 1], "created_at": now(),
                      "source_hash": project["source_hash"],
                      "question_snapshot": {k: activity[k] for k in ("id", "concept_id", "kind", "title", "prompt", "options", "evidence")}}
            self._insert(db, "hints", record, pid)
            return {**record, "activity": self._summary_activity(db, pid, activity)}

    def submit_evidence(self, pid, payload):
        allowed = {"attempt_id", "summary", "test_type", "tests_run", "passed", "failures", "errors", "skipped", "independent", "scope"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise InputError("验收证据字段不正确。")
        counts = {k: payload.get(k, 0) for k in ("tests_run", "passed", "failures", "errors", "skipped")}
        if any(type(v) is not int or not 0 <= v <= 100000 for v in counts.values()) or counts["tests_run"] != sum(counts[k] for k in ("passed", "failures", "errors", "skipped")):
            raise InputError("测试总数必须等于通过、失败、错误与跳过之和。")
        if type(payload.get("independent", False)) is not bool:
            raise InputError("独立完成标记应为布尔值。")
        with self._connection(True) as db:
            project = self._project(db, pid)
            attempt = next((r for r in self._rows(db, "attempts", pid) if r["id"] == payload.get("attempt_id")), None)
            if not attempt or attempt["version"] != project["version"]:
                raise InputError("请为当前源码版本的作答提交证据。")
            hints = [r for r in self._rows(db, "hints", pid) if r["activity_id"] == attempt["activity_id"] and r["version"] == project["version"]]
            if payload.get("independent") and (attempt["assistance"] != "none" or attempt["hint_level"] or hints):
                raise InputError("本次已使用提示或外部辅助，不能标记为无辅助独立完成。")
            record = {"id": uuid.uuid4().hex, "attempt_id": attempt["id"], "activity_id": attempt["activity_id"],
                      "version": project["version"], "source_hash": project["source_hash"], "created_at": now(),
                      "summary": text(payload.get("summary"), 12000), "test_type": text(payload.get("test_type", "口述或手操记录"), 100),
                      "scope": text(payload.get("scope", "尚未说明真实依赖范围"), 4000),
                      "independent": payload.get("independent", False), "status": "review_required",
                      "verification": "用户提交的证据，应用未执行测试或验证口述", **counts}
            self._insert(db, "evidence", record, pid)
            return {"evidence": record, "attempt": attempt}

    def preview_pack(self, pid, pack):
        if len(dumps(pack).encode()) > MAX_PACK_BYTES:
            raise InputError("任务包超过 2 MiB 容量上限。")
        if not isinstance(pack, dict) or type(pack.get("schema_version")) is not int or pack.get("schema_version") not in {1, 2}:
            raise InputError("只支持 schema_version 为 1 或 2 的数据任务包。")
        allowed = {"schema_version", "project_version", "activities", "title"}
        if pack["schema_version"] == 2:
            allowed |= {"source_hash", "concepts", "flows", "project_summary", "flow_updates", "concept_updates"}
        if set(pack) - allowed:
            raise InputError("任务包字段不符合导入协议。")
        with self._connection(True) as db:
            project = self._project(db, pid)
            if type(pack.get("project_version")) is not int or pack["project_version"] != project["version"]:
                raise InputError("任务包源码版本已过期，请重新导出上下文。")
            if pack["schema_version"] == 2 and pack.get("source_hash") != project["source_hash"]:
                raise InputError("任务包源码或资料哈希已过期，请重新导出上下文。")
            files, existing_concepts = self._rows(db, "files", pid), self._rows(db, "concepts", pid)
            concepts = self._validate_concepts(pack.get("concepts", []), files, existing_concepts)
            concept_updates = self._concept_updates(pack.get("concept_updates", []), files, existing_concepts)
            activities = self._validate_activities(pack.get("activities", []), existing_concepts + concepts, files, allow_empty=pack["schema_version"] == 2)
            flows = self._validate_flows(pack.get("flows", []), existing_concepts + concepts, files, self._rows(db, "flows", pid))
            summary = validate_project_summary(pack.get("project_summary"), files, text)
            updates = self._flow_updates(pack.get("flow_updates", []), existing_concepts + concepts, files, self._rows(db, "flows", pid))
            if {f["id"] for f in flows} & {f["id"] for f in updates}:
                raise InputError("新增和补充调用链标识不能冲突。")
            self._require_content(concepts, activities, flows, summary, updates, concept_updates)
            self._check_additions(db, pid, concepts, activities, flows)
            draft = {"id": uuid.uuid4().hex, "project_version": project["version"], "source_hash": project["source_hash"],
                     "title": text(pack.get("title", "Agent 学习任务包"), 120), "concepts": concepts,
                     "activities": activities, "flows": flows, "project_summary": summary, "flow_updates": updates,
                     "concept_updates": concept_updates,
                     "concept_update_bases": [c for c in existing_concepts if c["id"] in {u["id"] for u in concept_updates}],
                     "flow_update_bases": [f for f in self._rows(db, "flows", pid) if f["id"] in {u["id"] for u in updates}],
                     "created_at": now(), "schema_version": pack["schema_version"]}
            # Only one pending draft per project keeps capacity predictable.
            db.execute("DELETE FROM drafts WHERE project_id=?", (pid,))
            self._insert(db, "drafts", draft, pid)
            return {"draft_id": draft["id"], "title": draft["title"], "activity_count": len(activities),
                    "concept_count": len(concepts), "flow_count": len(flows), "concepts": concepts, "activities": activities, "flows": flows,
                    "project_summary": summary, "flow_updates": updates,
                    "concept_updates": concept_updates, "updated_concept_count": len(concept_updates),
                    "warnings": ["源码锚点已校验；教学内容和标准答案仍需你审阅。", "任务包仅含数据，不执行包内代码。", *self._activity_quality_warnings(activities)]}

    def _check_additions(self, db, pid, concepts, activities, flows=()):
        existing_concepts = {c["id"] for c in self._rows(db, "concepts", pid)}
        existing = {a["id"] for a in self._rows(db, "activities", pid)}
        existing_flows = {f["id"] for f in self._rows(db, "flows", pid)}
        if existing_concepts & {c["id"] for c in concepts} or existing & {a["id"] for a in activities} or existing_flows & {f["id"] for f in flows}:
            raise InputError("导入知识点、场景调用链或活动标识已存在，请使用新的稳定标识。")
        if len(existing) + len(activities) > 150:
            raise InputError("一个项目最多保存 150 个活动。")
        if len(existing_flows) + len(flows) > 12:
            raise InputError("一个项目最多保存 12 条场景调用链。")

    def commit_pack(self, pid, draft_id, confirmed):
        if confirmed is not True:
            raise InputError("请先预览并明确确认写入。")
        with self._connection(True) as db:
            project = self._project(db, pid)
            row = db.execute("SELECT payload FROM drafts WHERE id=? AND project_id=?", (identity(draft_id), pid)).fetchone()
            if not row:
                raise InputError("预览草稿不存在或已失效。")
            draft = json.loads(row[0])
            if draft["project_version"] != project["version"] or draft["source_hash"] != project["source_hash"]:
                raise InputError("源码已变化，请重新预览任务包。")
            files, existing_concepts = self._rows(db, "files", pid), self._rows(db, "concepts", pid)
            current_flows = self._rows(db, "flows", pid)
            if any(base not in current_flows for base in draft.get("flow_update_bases", [])):
                raise InputError("调用链已在预览后更新，请重新生成补充包并预览。")
            if any(base not in existing_concepts for base in draft.get("concept_update_bases", [])):
                raise InputError("知识讲解已在预览后更新，请重新生成补充包并预览。")
            concepts = self._validate_concepts(draft.get("concepts", []), files, existing_concepts)
            concept_updates = self._concept_updates(draft.get("concept_updates", []), files, existing_concepts)
            activities = self._validate_activities(draft["activities"], existing_concepts + concepts, files, allow_empty=draft["schema_version"] == 2)
            flows = self._validate_flows(draft.get("flows", []), existing_concepts + concepts, files, self._rows(db, "flows", pid))
            summary = validate_project_summary(draft.get("project_summary"), files, text)
            updates = self._flow_updates(draft.get("flow_updates", []), existing_concepts + concepts, files, self._rows(db, "flows", pid))
            self._require_content(concepts, activities, flows, summary, updates, concept_updates)
            self._check_additions(db, pid, concepts, activities, flows)
            for concept in concepts:
                self._insert(db, "concepts", concept, pid)
            for concept in concept_updates:
                self._insert(db, "concepts", concept, pid)
            for activity in activities:
                activity["origin"] = "agent_import"
                self._insert(db, "activities", activity, pid)
            for flow in flows:
                self._insert(db, "flows", flow, pid)
            for flow in updates:
                self._insert(db, "flows", flow, pid)
            self._save_project_summary(db, pid, summary)
            db.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
            return {"project": self._public_project(project), "added_count": len(activities), "added_concept_count": len(concepts), "added_flow_count": len(flows),
                    "updated_concept_count": len(concept_updates),
                    "updated_flow_count": len(updates), "project_summary": self._project_summary(db, pid, project, self._rows(db, "flows", pid))}

    @staticmethod
    def _project_name(value):
        name = text(value, 80)
        if any(x in name for x in ("\\", "/", ":")):
            raise InputError("项目名称不能包含本机路径。")
        return name

    def _prepare_bundle(self, bundle):
        if not isinstance(bundle, dict) or type(bundle.get("schema_version")) is not int or bundle.get("schema_version") != 2 or set(bundle) - {"schema_version", "title", "project", "files", "concepts", "activities", "flows", "project_summary"}:
            raise InputError("完整学习包应为 schema_version=2，包含 project、files、concepts 与 activities。")
        if len(dumps(bundle).encode()) > MAX_BUNDLE_BYTES:
            raise InputError("完整学习包超过安全容量上限。")
        raw_project = bundle.get("project")
        if not isinstance(raw_project, dict) or set(raw_project) != {"name"}:
            raise InputError("学习包 project 应仅包含 name。")
        name = self._project_name(raw_project["name"])
        files, report = collect_upload(bundle.get("files"))
        concepts = self._validate_concepts(bundle.get("concepts"), files)
        activities = self._validate_activities(bundle.get("activities"), concepts, files, maximum=150)
        flows = self._validate_flows(bundle.get("flows", []), concepts, files)
        summary = validate_project_summary(bundle.get("project_summary"), files, text)
        self._require_content(concepts, activities, flows, summary)
        draft = {"id": uuid.uuid4().hex, "kind": "intake", "schema_version": 2,
                 "title": text(bundle.get("title", "完整学习包"), 120), "project_name": name,
                 "files": files, "concepts": concepts, "activities": activities, "flows": flows, "project_summary": summary,
                 "source_hash": source_hash(files), "scan_report": report, "created_at": now()}
        warnings = ["当前源码与资料锚点已校验；教学内容、出处与答案仍需你审阅。", "完整包仅保存数据，不执行代码或请求外部模型；未确认草稿不进入备份。"]
        warnings.extend(self._activity_quality_warnings(activities))
        if report["skipped"]:
            warnings.append(f"已安全排除 {report['skipped']} 个配置、密钥或超限文件；保留的证据均已匹配。")
        return draft, warnings

    def preview_bundle(self, bundle):
        draft, warnings = self._prepare_bundle(bundle)
        with self._connection(True) as db:
            if db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] >= 12:
                raise InputError("最多保存 12 个项目，请先备份并整理旧项目。")
            db.execute("DELETE FROM drafts WHERE project_id=?", (INTAKE_PROJECT,))
            self._insert(db, "drafts", draft, INTAKE_PROJECT)
        return {"draft_id": draft["id"], "title": draft["title"], "project_name": draft["project_name"],
                "file_count": len(draft["files"]), "concept_count": len(draft["concepts"]),
                "activity_count": len(draft["activities"]), "flow_count": len(draft["flows"]),
                "concepts": draft["concepts"], "activities": draft["activities"], "flows": draft["flows"], "project_summary": draft["project_summary"], "warnings": warnings}

    def _commit_bundle(self, db, draft):
        if db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] >= 12:
            raise InputError("最多保存 12 个项目，请先备份并整理旧项目。")
        files, _ = collect_upload([{"path": f["path"], "text": f["text"]} for f in draft["files"]])
        if source_hash(files) != draft["source_hash"]:
            raise InputError("完整学习包快照已失效，请重新预览。")
        concepts = self._validate_concepts(draft["concepts"], files)
        activities = self._validate_activities(draft["activities"], concepts, files, maximum=150)
        flows = self._validate_flows(draft.get("flows", []), concepts, files)
        summary = validate_project_summary(draft.get("project_summary"), files, text)
        self._require_content(concepts, activities, flows, summary)
        pid = uuid.uuid4().hex
        project = {"id": pid, "name": draft["project_name"], "version": 1, "source_hash": source_hash(files),
                   "file_count": len(files), "created_at": now(), "updated_at": now(), "root": None,
                   "scan_report": draft["scan_report"]}
        db.execute("INSERT INTO projects VALUES (?,?)", (pid, dumps(project)))
        for table, records in (("files", files), ("concepts", concepts), ("activities", activities), ("flows", flows)):
            for record in records:
                if table == "activities":
                    record["origin"] = "agent_import"
                self._insert(db, table, record, pid)
        self._save_project_summary(db, pid, summary)
        return self._detail(db, pid)

    def import_builtin_bundle(self, bundle, origin):
        """Reuse only internally marked, unchanged built-ins; ordinary imports stay new."""
        origin = identity(origin)
        draft, _ = self._prepare_bundle(bundle)
        content = {k: v for k, v in draft.items() if k not in {"id", "created_at"}}
        canonical = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        bundle_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._connection(True) as db:
            row = db.execute("SELECT project_id FROM builtin_imports WHERE origin=? AND bundle_hash=?", (origin, bundle_hash)).fetchone()
            if row:
                pid = row["project_id"]
                project = self._project(db, pid)
                unchanged = project["source_hash"] == draft["source_hash"]
                for table in ("concepts", "activities", "flows"):
                    current = [{k: v for k, v in record.items() if k != "origin"} for record in self._rows(db, table, pid)]
                    unchanged = unchanged and all(record in current for record in draft[table])
                if unchanged:
                    return self._detail(db, pid)
            detail = self._commit_bundle(db, draft)
            db.execute("INSERT OR REPLACE INTO builtin_imports VALUES (?,?,?)", (origin, bundle_hash, detail["project"]["id"]))
            return detail

    def commit_bundle(self, draft_id, confirmed):
        if confirmed is not True:
            raise InputError("请先预览并明确确认写入完整学习包。")
        with self._connection(True) as db:
            row = db.execute("SELECT payload FROM drafts WHERE id=? AND project_id=?", (identity(draft_id), INTAKE_PROJECT)).fetchone()
            if not row:
                raise InputError("完整学习包预览草稿不存在或已失效。")
            draft = json.loads(row[0])
            detail = self._commit_bundle(db, draft)
            db.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
            return detail

    def add_materials(self, pid, items):
        if not isinstance(items, list) or not items:
            raise InputError("请提供 Markdown 或纯文本学习资料。")
        prepared = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {"path", "text"}:
                raise InputError("学习资料应包含 path 与 text。")
            path = relative_path(item["path"])
            if not path.lower().endswith((".md", ".txt")):
                raise InputError("追加学习资料只支持 .md 或 .txt。")
            prepared.append({"path": path if path.startswith("materials/") else "materials/" + path, "text": item["text"]})
        materials, report = collect_upload(prepared)
        with self._connection(True) as db:
            project = self._project(db, pid)
            merged = {f["path"]: {"path": f["path"], "text": f["text"]} for f in self._rows(db, "files", pid)}
            merged.update({f["path"]: {"path": f["path"], "text": f["text"]} for f in materials})
            files, merged_report = collect_upload(list(merged.values()))
            fingerprint = source_hash(files)
            changed = fingerprint != project["source_hash"]
            if changed:
                project.update(version=project["version"] + 1, source_hash=fingerprint, file_count=len(files),
                               updated_at=now(), scan_report=merged_report)
                self._insert(db, "projects", project)
                for table in ("files", "concepts", "activities", "flows", "drafts"):
                    db.execute(f"DELETE FROM {table} WHERE project_id=?", (pid,))
                for f in files:
                    self._insert(db, "files", f, pid)
            detail = self._detail(db, pid)
            detail.update(source_changed=changed, warnings=["资料版本已更新，请让 Agent 按新 version 和 source_hash 生成知识点与任务；历史答案与证据仍保留并标为过期。"] if changed else ["资料与当前快照相同，版本未改变。"])
            if report["skipped"]:
                detail["warnings"].append(f"已安全排除 {report['skipped']} 个私密或超限资料文件。")
            return detail

    def handoff(self, pid):
        with self._connection() as db:
            project = self._project(db, pid)
            activities = self._rows(db, "activities", pid)
            public_activities = [{k: v for k, v in a.items() if k not in {"answer_ids", "explanation", "hints"}} for a in activities]
            return {"schema_version": 2, "project": self._public_project(project),
                    "project_summary": self._project_summary(db, pid, project, self._rows(db, "flows", pid)),
                    "review_state": self._review_state(db, pid),
                    "project_version": project["version"], "source_hash": project["source_hash"],
                    "concepts": self._rows(db, "concepts", pid), "flows": self._rows(db, "flows", pid), "activities": public_activities,
                    "attempts": self._history(db, pid), "hints": self._rows(db, "hints", pid),
                    "evidence": self._rows(db, "evidence", pid),
                    "source_files": self._rows(db, "files", pid),
                    "materials": [f for f in self._rows(db, "files", pid) if f["path"].lower().endswith((".md", ".txt"))],
                    "concept_contract": {"fields": ["id", "title", "group", "explanation", "why", "prerequisites", "evidence", "detection", "limitations", "mechanism", "failures", "lesson", "references"],
                                         "evidence": {"path": "当前源码或资料相对路径", "line": "1起算行号", "quote": "该行片段", "sha256": "完整文件UTF-8 SHA256"},
                                         "references": [{"title": "出处标题", "url": "https://出处地址", "note": "来源用途；未自动联网验证"}],
                                         "max_concepts": 80, "max_groups": 8, "lesson_format": "纯文本或Markdown，作为文本展示，不执行HTML"},
                    "flow_contract": flow_contract(),
                    "project_summary_contract": {"fields": sorted(SUMMARY_FIELDS), "optional": True},
                    "concept_update_contract": {"field": "concept_updates", "schema_version": 2,
                                                "format": "完整现存知识点对象", "mutable_fields": ["lesson", "mechanism", "failures", "references"],
                                                "rule": "保留全部原字段；讲解与机制保留原文后追加，错误与出处保留原条目顺序后追加；预览后基础对象变化则拒绝提交。"},
                    "instructions": "按四阶段生成学习内容：首先 project_summary 解释项目用途、使用者、场景、功能、学习价值与边界；其次按功能拆分 flows，完整列出真实 methods 签名、调用/返回/停止及参数追踪，非法案例用 skipped_step_ids 明确未调用步骤；再次提供每隔 n 天由用户自己写的项目复述与反馈，不能自动精确评分；最后用项目知识生成选择题即时复习与 80% 固定分母检测。部署与导入只放 Agent 说明，不做学习主界面。依据当前源码与学习资料设计 flows、concepts、讲义和活动，领域不限Java；静态Java候选仅辅助。新包须包含至少一条完整场景调用链，每步解释 actor/action/input/output/why，引用同批或现存 concept_ids 与当前文件证据；scenario/input 使用具体输入示例。为每条调用链提供完整 trace_cases（具体 input/result、按 step_id 的 frames，每个 values 含 name/type/value/source，value 用字符串），关键拒绝或失败分支用 branches 写清跳过的步骤。resources 只记录真实存在资源的归属、生命周期和失败行为，无资源可为空。调用链与案例属于静态教学推演，不证明实际执行、真实模型、生产能力或独立掌握。可新增任意稳定概念与调用链id，不能覆盖现存题目；现存知识讲解可用 concept_updates 完整对象补充，仅 lesson/mechanism/failures/references 允许保留原内容后追加，其他字段与原证据必须完全一致；预览后原知识点变化则整包拒绝；既有调用链可通过 flow_updates 补充方法与追踪，但必须保留 flow id 与已有步骤顺序；前置须存在且不能循环。输出 schema_version=2、project_version、source_hash、title、project_summary、flows、flow_updates、concepts、concept_updates、activities 的JSON任务包，activities 可以为空但整个包不能全空；每个概念、调用链步骤与活动绑定当前文件行号、quote、sha256。机制、设计原因、常见错误、课文与出处分别提供mechanism/why/failures/lesson/references。参考链接仅是出处，应用不联网核验。先预测再解释、逐级提示；开放答案待人工复核，选择题通过不证明独立掌握。不执行代码、不请求外部模型。"}

    def _backup(self, db):
        data = {table: self._rows(db, table) if table == "projects" else [
            {"project_id": row["project_id"], "data": json.loads(row["payload"])}
            for row in db.execute(f"SELECT project_id,payload FROM {table} ORDER BY rowid")]
                for table in TABLES}
        data["projects"] = [self._public_project(p) for p in data["projects"]]
        return {"schema_version": 2, "app": "project-study", "exported_at": now(), "data": data,
                "privacy": "不包含本机原项目绝对路径；包含源码快照与个人答案，请自行保管。"}

    def backup(self):
        with self._connection() as db:
            return self._backup(db)

    def restore(self, backup, confirmed):
        if confirmed is not True:
            raise InputError("恢复会替换应用内数据，请明确确认。")
        if not isinstance(backup, dict) or type(backup.get("schema_version")) is not int or backup.get("schema_version") not in {1, 2} or backup.get("app") != "project-study" or len(dumps(backup).encode()) > MAX_BACKUP_BYTES:
            raise InputError("备份格式或容量不正确。")
        data = backup.get("data")
        required = set(LEGACY_TABLES) | ({"checks"} if backup["schema_version"] == 2 else set())
        if not isinstance(data, dict) or not required <= set(data) or set(data) - set(TABLES):
            raise InputError("备份表结构不完整。")
        data = {**data, **{table: data.get(table, []) for table in TABLES if table not in LEGACY_TABLES}}
        if any(not isinstance(data[k], list) for k in TABLES):
            raise InputError("备份表结构不完整。")
        projects = data["projects"]
        if len(projects) > 12 or len(data["attempts"]) > 10000 or any(len(data[t]) > 30000 for t in TABLES):
            raise InputError("备份记录超过允许上限。")
        pids, sources, concepts = set(), {}, {}
        for p in projects:
            if not isinstance(p, dict) or "root" in p or set(p) != {"id", "name", "version", "source_hash", "file_count", "created_at", "updated_at", "scan_report"}:
                raise InputError("备份项目字段不正确。")
            identity(p["id"])
            text(p["name"], 80)
            if any(x in p["name"] for x in ("\\", "/", ":")):
                raise InputError("备份项目名称不能包含本机路径。")
            timestamp(p["created_at"])
            timestamp(p["updated_at"])
            if p["id"] in pids or type(p["version"]) is not int or p["version"] < 1:
                raise InputError("备份项目版本或标识重复。")
            pids.add(p["id"])
            sources[p["id"]], concepts[p["id"]] = [], []
        seen_keys = {t: set() for t in TABLES}
        for table in TABLES[1:]:
            for row in data[table]:
                if not isinstance(row, dict) or set(row) != {"project_id", "data"} or row["project_id"] not in pids or not isinstance(row["data"], dict):
                    raise InputError("备份包含无效项目关联。")
                record, pid = row["data"], row["project_id"]
                if table != "files":
                    identity(record.get("id"))
                key = (pid, relative_path(record.get("path")) if table == "files" else record.get("id")) if table in {"files", "concepts", "activities", "flows"} else record.get("id")
                if key in seen_keys[table]:
                    raise InputError("备份记录标识重复。")
                seen_keys[table].add(key)
                if table == "files":
                    if set(record) != {"path", "text", "sha256"}:
                        raise InputError("源码快照字段不正确。")
                    sources[pid].append(record)
                else:
                    identity(record.get("id"))
                if table == "concepts":
                    concepts[pid].append(record)
        for p in projects:
            files = sources[p["id"]]
            cleaned, _ = collect_upload([{"path": f["path"], "text": f["text"]} for f in files])
            if len(cleaned) != len(files) or {f["path"]: f["sha256"] for f in cleaned} != {f["path"]: f["sha256"] for f in files} or source_hash(cleaned) != p["source_hash"] or len(files) != p["file_count"]:
                raise InputError("备份源码哈希或文件数量不匹配。")
            # Historical static candidates may mention prerequisites that were
            # not detected. Only exact matches to the old analyzer get that
            # compatibility exemption; Agent concepts obey the complete graph.
            expected_concepts = analyze(cleaned)
            static = [c for c in concepts[p["id"]] if c in expected_concepts]
            proposed = [c for c in concepts[p["id"]] if c not in static]
            self._validate_concepts(proposed, cleaned, static)
            activities = [r["data"] for r in data["activities"] if r["project_id"] == p["id"]]
            self._validate_activities(activities, concepts[p["id"]], cleaned, maximum=150)
            flows = [r["data"] for r in data["flows"] if r["project_id"] == p["id"]]
            self._validate_flows(flows, concepts[p["id"]], cleaned)
        project_versions = {p["id"]: p["version"] for p in projects}
        project_hashes = {p["id"]: p["source_hash"] for p in projects}
        self.validate_review_records(data, project_versions, project_hashes)
        for row in data["project_summaries"]:
            record, pid = row["data"], row["project_id"]
            if set(record) != {"id", "version", "source_hash", "created_at", "content"} or type(record["version"]) is not int or not 1 <= record["version"] <= project_versions[pid]:
                raise InputError("项目总结备份版本无效。")
            timestamp(record["created_at"])
            self._validate_historical_hash(record, pid, project_versions, project_hashes)
            if record["version"] == project_versions[pid]:
                validate_project_summary(record["content"], sources[pid], text)
            else:
                content = record["content"]
                if not isinstance(content, dict) or set(content) != SUMMARY_FIELDS or not isinstance(content["capabilities"], list) or not 1 <= len(content["capabilities"]) <= 20:
                    raise InputError("历史项目总结结构无效。")
                for key in SUMMARY_FIELDS - {"evidence", "capabilities"}:
                    text(content[key], 8000)
                for value in content["capabilities"]:
                    text(value, 1000)
                anchors = content["evidence"]
                if not isinstance(anchors, list) or not 1 <= len(anchors) <= 8:
                    raise InputError("历史总结缺少证据。")
                for anchor in anchors:
                    if not isinstance(anchor, dict) or set(anchor) != {"path", "line", "quote", "sha256"} or type(anchor["line"]) is not int or anchor["line"] < 1:
                        raise InputError("历史总结证据无效。")
                    relative_path(anchor["path"])
                    text(anchor["quote"], 800)
                    if not isinstance(anchor["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", anchor["sha256"]):
                        raise InputError("历史总结证据哈希无效。")
        current_activities = {(row["project_id"], row["data"]["id"]): row["data"] for row in data["activities"]}
        attempt_ids = {}
        for row in data["attempts"]:
            r, pid = row["data"], row["project_id"]
            identity(r.get("activity_id"))
            timestamp(r.get("created_at"))
            if type(r.get("version")) is not int or not 1 <= r["version"] <= project_versions[pid] or r.get("status") not in {"correct", "incorrect", "review_required"} or r.get("assistance") not in {"none", "agent", "reference"} or type(r.get("hint_level")) is not int or r["hint_level"] not in (0, 1, 2, 3):
                raise InputError("作答记录结构不正确。")
            if "guessed" in r and type(r["guessed"]) is not bool:
                raise InputError("猜测作答标记应为布尔值。")
            if isinstance(r.get("answer"), list):
                if not r["answer"] or len(r["answer"]) > 8 or any(not isinstance(x, str) or not ID.fullmatch(x) for x in r["answer"]):
                    raise InputError("选择题记录不正确。")
            else:
                text(r.get("answer"))
            assessment, snapshot = r.get("assessment"), r.get("question_snapshot")
            if not isinstance(assessment, dict) or set(assessment) != {"option_ids", "answer_ids"} or not isinstance(snapshot, dict) or set(snapshot) != {"id", "concept_id", "kind", "title", "prompt", "options", "evidence"}:
                raise InputError("作答缺少当时的题目与评分依据。")
            if snapshot["id"] != r["activity_id"] or snapshot["concept_id"] != r.get("concept_id") or snapshot["kind"] != r.get("kind") or snapshot["kind"] not in KINDS:
                raise InputError("历史题目与作答关联不正确。")
            for label, count_limit in (("title", 200), ("prompt", 16000)):
                text(snapshot.get(label), count_limit)
            if not isinstance(assessment["option_ids"], list) or not isinstance(assessment["answer_ids"], list) or any(not isinstance(x, str) or not ID.fullmatch(x) for x in assessment["option_ids"] + assessment["answer_ids"]) or len(set(assessment["option_ids"])) != len(assessment["option_ids"]) or len(set(assessment["answer_ids"])) != len(assessment["answer_ids"]) or set(assessment["answer_ids"]) - set(assessment["option_ids"]):
                raise InputError("历史评分依据不正确。")
            if not isinstance(snapshot["options"], list) or [o.get("id") for o in snapshot["options"] if isinstance(o, dict)] != assessment["option_ids"]:
                raise InputError("历史选项与评分依据不匹配。")
            active = current_activities.get((pid, r["activity_id"]))
            if r["version"] == project_versions[pid] and (not active or active["answer_ids"] != assessment["answer_ids"] or active["options"] != snapshot["options"] or active["evidence"] != snapshot["evidence"] or r.get("source_hash") != project_hashes[pid]):
                raise InputError("当前作答引用的题目或源码证据不匹配。")
            if assessment["option_ids"]:
                if not isinstance(r["answer"], list) or set(r["answer"]) - set(assessment["option_ids"]) or not assessment["answer_ids"]:
                    raise InputError("历史作答选项不正确。")
                expected_score = 100 if set(r["answer"]) == set(assessment["answer_ids"]) else 0
                expected_status = "correct" if expected_score == 100 else "incorrect"
            else:
                if not isinstance(r["answer"], str) or assessment["answer_ids"]:
                    raise InputError("主观作答评分依据不正确。")
                expected_score, expected_status = None, "review_required"
            if r.get("score") != expected_score or r["status"] != expected_status or (r["score"] is not None and type(r["score"]) is not int):
                raise InputError("作答分数不正确。")
            attempt_ids[r["id"]] = (pid, r)
        for row in data["hints"]:
            r, pid = row["data"], row["project_id"]
            identity(r.get("activity_id"))
            timestamp(r.get("created_at"))
            if type(r.get("level")) is not int or r["level"] not in (1, 2, 3) or type(r.get("version")) is not int or not 1 <= r["version"] <= project_versions[pid]:
                raise InputError("提示记录不正确。")
            text(r.get("text"), 5000)
            snapshot = r.get("question_snapshot")
            if not isinstance(snapshot, dict) or set(snapshot) != {"id", "concept_id", "kind", "title", "prompt", "options", "evidence"} or snapshot["id"] != r["activity_id"] or snapshot["kind"] not in KINDS or not isinstance(r.get("source_hash"), str) or not re.fullmatch("[0-9a-f]{64}", r["source_hash"]):
                raise InputError("提示缺少当时的题目与源码版本。")
            if r["version"] == project_versions[pid]:
                active = current_activities.get((pid, r["activity_id"]))
                if not active or len(active["hints"]) < r["level"] or active["hints"][r["level"] - 1] != r["text"] or active["evidence"] != snapshot["evidence"] or r["source_hash"] != project_hashes[pid]:
                    raise InputError("提示与当前活动不匹配。")
        for row in data["evidence"]:
            r, pid = row["data"], row["project_id"]
            parent = attempt_ids.get(r.get("attempt_id"))
            if not parent or parent[0] != pid or r.get("activity_id") != parent[1]["activity_id"] or r.get("version") != parent[1]["version"] or r.get("source_hash") != parent[1]["source_hash"] or r.get("status") != "review_required" or type(r.get("independent")) is not bool:
                raise InputError("验收证据关联不正确。")
            counts = [r.get(k) for k in ("tests_run", "passed", "failures", "errors", "skipped")]
            if any(type(v) is not int or not 0 <= v <= 100000 for v in counts) or counts[0] != sum(counts[1:]):
                raise InputError("验收测试数量不正确。")
            text(r.get("summary"))
            timestamp(r.get("created_at"))
            if r["independent"] and (parent[1]["assistance"] != "none" or parent[1]["hint_level"] or any(h["project_id"] == pid and h["data"]["activity_id"] == r["activity_id"] and h["data"]["version"] == r["version"] for h in data["hints"])):
                raise InputError("独立完成证据与辅助记录冲突。")
        self.validate_check_records(data, project_versions, project_hashes, current_activities, attempt_ids)
        with self._connection(True) as db:
            db.execute("DELETE FROM builtin_imports")
            for table in (*TABLES, "drafts"):
                db.execute(f"DELETE FROM {table}")
            for p in projects:
                self._insert(db, "projects", {**p, "root": None})
            for table in TABLES[1:]:
                for row in data[table]:
                    self._insert(db, table, row["data"], row["project_id"])
        return {"restored": True, "mode": "replace", "project_count": len(projects), "attempt_count": len(data["attempts"])}

    @staticmethod
    def _validate_historical_hash(record, pid, project_versions, project_hashes):
        digest = record.get("source_hash")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or (record["version"] == project_versions[pid] and digest != project_hashes[pid]):
            raise InputError("历史记录源码哈希无效。")

    def delete_project(self, pid, confirmed):
        if confirmed is not True:
            raise InputError("请明确确认删除应用内快照。")
        with self._connection(True) as db:
            self._project(db, pid)
            db.execute("DELETE FROM builtin_imports WHERE project_id=?", (pid,))
            for table in (*TABLES[1:], "drafts"):
                db.execute(f"DELETE FROM {table} WHERE project_id=?", (pid,))
            db.execute("DELETE FROM projects WHERE id=?", (pid,))
            return {"deleted": True, "original_source_unchanged": True}
