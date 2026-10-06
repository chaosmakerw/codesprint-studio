"""Private local files and SQLite. No user data is part of the repository."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import tempfile
import time
import uuid
import zipfile

CATEGORIES = ["Java 基础", "Spring 与后端", "数据库与缓存", "Agent 与 RAG", "工程与部署", "面试准备", "算法与数据结构", "其他资料"]
MAX_UPLOAD = 50 * 1024 * 1024
TEXT_LIMIT = 2 * 1024 * 1024
TEXT_TYPES = {".md", ".txt", ".java", ".json", ".csv", ".xml", ".yml", ".yaml", ".py", ".js", ".css", ".html", ".sql", ".log", ".rst"}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def clean_string(value, maximum, label, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()) or "\x00" in value:
        raise ValueError(f"{label}格式或长度无效。")
    return value.strip()


def clean_tags(value):
    if isinstance(value, str):
        value = [x.strip() for x in value.split(",") if x.strip()]
    if not isinstance(value, list) or len(value) > 12:
        raise ValueError("标签最多 12 项。")
    return list(dict.fromkeys(clean_string(x, 40, "标签") for x in value))


def file_text(filename, content):
    if Path(filename).suffix.lower() not in TEXT_TYPES or len(content) > TEXT_LIMIT:
        return ""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = content.decode(encoding)
            return text if "\x00" not in text else ""
        except UnicodeDecodeError:
            continue
    return ""


class Library:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.blobs = self.directory / "blobs"
        self.blobs.mkdir(exist_ok=True)
        self.database = self.directory / "library.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS materials (
              id TEXT PRIMARY KEY, source_key TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
              filename TEXT NOT NULL, category TEXT NOT NULL, tags TEXT NOT NULL,
              sha256 TEXT NOT NULL, size INTEGER NOT NULL, text_content TEXT NOT NULL,
              favorite INTEGER NOT NULL DEFAULT 0, trashed INTEGER NOT NULL DEFAULT 0,
              missing INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS questions (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS generation_drafts (
              id TEXT PRIMARY KEY, material_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
              payload TEXT NOT NULL, created_at INTEGER NOT NULL, committed_at INTEGER);
            CREATE TABLE IF NOT EXISTS study_tasks (
              id TEXT PRIMARY KEY, date TEXT NOT NULL, title TEXT NOT NULL, topic TEXT NOT NULL,
              material_id TEXT NOT NULL, notes TEXT NOT NULL, estimated_minutes INTEGER NOT NULL,
              actual_minutes INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, completed_at INTEGER);
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.database, timeout=20)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def public(row, detail=False):
        item = dict(row)
        item["tags"] = json.loads(item["tags"])
        for flag in ("favorite", "trashed", "missing"):
            item[flag] = bool(item[flag])
        if not detail:
            item.pop("text_content", None)
        item["file_url"] = f'/api/materials/{item["id"]}/file'
        item["type"] = Path(item["filename"]).suffix.lower().lstrip(".") or "file"
        return item

    def get(self, identity, include_trashed=False):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM materials WHERE id=?", (identity,)).fetchone()
        if not row or (row["trashed"] and not include_trashed):
            raise KeyError(identity)
        return self.public(row, True)

    def listing(self, search="", category="", trashed=False, favorite=False):
        if len(search) > 200 or category and category not in CATEGORIES:
            raise ValueError("检索条件无效。")
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM materials WHERE trashed=? ORDER BY created_at DESC,rowid DESC", (int(trashed),)).fetchall()
        items = [self.public(row) for row in rows if (not category or row["category"] == category)
                 and (not favorite or row["favorite"])
                 and (not search or search.casefold() in (row["title"] + row["filename"] + row["tags"] + row["text_content"]).casefold())]
        return {"items": items, "total": len(items)}

    def upload(self, filename, content, category="其他资料", tags=(), source_key=None):
        filename = clean_string(filename, 180, "文件名")
        if filename != filename.replace("\\", "/").split("/")[-1] or filename in (".", ".."):
            raise ValueError("文件名不能包含目录。")
        if category not in CATEGORIES or not content or len(content) > MAX_UPLOAD:
            raise ValueError("请选择有效分类及 50 MB 以内的非空文件。")
        tags = clean_tags(list(tags) if isinstance(tags, tuple) else tags)
        identity = uuid.uuid4().hex
        digest = hashlib.sha256(content).hexdigest()
        row = (identity, source_key or "upload:" + identity, Path(filename).stem, filename, category,
               dumps(tags), digest, len(content), file_text(filename, content), 0, 0, 0, int(time.time()))
        with self.lock:
            path = self.blobs / digest
            if not path.exists():
                temp = self.blobs / (digest + "." + uuid.uuid4().hex + ".tmp")
                try:
                    temp.write_bytes(content)
                    os.replace(temp, path)
                finally:
                    temp.unlink(missing_ok=True)
            with self.connect() as conn:
                conn.execute("INSERT INTO materials VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
        return self.get(identity)

    def patch(self, identity, payload):
        if not isinstance(payload, dict) or not payload or not set(payload) <= {"title", "category", "tags", "favorite", "trashed"}:
            raise ValueError("只允许修改标题、分类、标签、收藏及回收站状态。")
        values = {}
        for key, value in payload.items():
            if key == "title":
                values[key] = clean_string(value, 160, "标题")
            elif key == "category":
                if value not in CATEGORIES:
                    raise ValueError("分类无效。")
                values[key] = value
            elif key == "tags":
                values[key] = dumps(clean_tags(value))
            else:
                if type(value) is not bool:
                    raise ValueError("状态必须是布尔值。")
                values[key] = int(value)
        with self.lock, self.connect() as conn:
            if not conn.execute("SELECT id FROM materials WHERE id=?", (identity,)).fetchone():
                raise KeyError(identity)
            conn.execute("UPDATE materials SET " + ",".join(k + "=?" for k in values) + " WHERE id=?", (*values.values(), identity))
        return self.get(identity, True)

    def read_file(self, identity):
        item = self.get(identity)
        path = self.blobs / item["sha256"]
        try:
            data = path.read_bytes()
        except OSError:
            raise ValueError("本地文件不存在，请从备份恢复。") from None
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("资料原件校验失败，请从备份恢复。")
        return item, data

    def bank(self, conn=None):
        if conn is not None:
            return [json.loads(r["payload"]) for r in conn.execute("SELECT payload FROM questions ORDER BY id")]
        with self.connect() as current:
            return self.bank(current)

    def backup(self):
        """Consistent rows plus hash-addressed files; excludes env and API credentials."""
        with self.lock, self.connect() as conn:
            data = {"schema": "codesprint-backup-v2", "exported_at": int(time.time()), "tables": {}}
            for name in ("materials", "questions", "generation_drafts", "study_tasks", "quiz_sessions", "quiz_review"):
                data["tables"][name] = [dict(r) for r in conn.execute("SELECT * FROM " + name)]
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("backup.json", dumps(data))
                for digest in sorted({r["sha256"] for r in data["tables"]["materials"]}):
                    content = (self.blobs / digest).read_bytes()
                    if hashlib.sha256(content).hexdigest() != digest:
                        raise ValueError("备份前资料校验失败。")
                    archive.writestr("blobs/" + digest, content)
            return stream.getvalue()

    def restore(self, content):
        """Validate in an isolated database before any live write; never extract paths."""
        from .backup_validation import validate_stored_question, validate_session, validate_review, validate_draft
        from .quiz import QuizStore
        from .planning import validate_task_row, MAX_TASKS
        if not content or len(content) > 128 * 1024 * 1024:
            raise ValueError("请选择 128 MB 以内的学习站 ZIP 备份。")
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
            with archive:
                members = archive.infolist()
                if len(members) > 20000 or sum(x.file_size for x in members) > 256 * 1024 * 1024:
                    raise ValueError("备份解压总量过大。")
                names = [x.filename for x in members]
                if len(names) != len(set(names)) or "backup.json" not in names or any(n != "backup.json" and not re.fullmatch(r"blobs/[a-f0-9]{64}", n) for n in names):
                    raise ValueError("备份包含无效路径或重复文件。")
                if archive.getinfo("backup.json").file_size > 64 * 1024 * 1024:
                    raise ValueError("备份元数据过大。")
                manifest = json.loads(archive.read("backup.json"))
                if not isinstance(manifest, dict) or manifest.get("schema") not in ("codesprint-backup-v1", "codesprint-backup-v2"):
                    raise ValueError("备份版本不受支持。")
                legacy = manifest["schema"] == "codesprint-backup-v1"
                tables = manifest.get("tables")
                required = {"materials", "questions", "generation_drafts", "quiz_sessions", "quiz_review"}
                if not legacy:
                    required.add("study_tasks")
                if not isinstance(tables, dict) or set(tables) != required or any(not isinstance(rows, list) or len(rows) > 100000 for rows in tables.values()):
                    raise ValueError("备份数据表结构无效。")
                if not legacy and len(tables["study_tasks"]) > MAX_TASKS:
                    raise ValueError("备份任务数量超过限制。")
                with tempfile.TemporaryDirectory(prefix="codesprint-restore-") as temp:
                    staged = Library(temp)
                    QuizStore(staged)
                    with staged.connect() as conn:
                        for name, rows in tables.items():
                            columns = [r["name"] for r in conn.execute("PRAGMA table_info(" + name + ")")]
                            for row in rows:
                                source_columns = [c for c in columns if c != "task_id"] if legacy and name == "quiz_sessions" else columns
                                if not isinstance(row, dict) or set(row) != set(source_columns):
                                    raise ValueError("备份记录字段不完整。")
                                conn.execute("INSERT INTO " + name + " (" + ",".join(source_columns) + ") VALUES (" + ",".join("?" for _ in source_columns) + ")", [row[k] for k in source_columns])
                    with staged.connect() as conn:
                        for row in tables.get("study_tasks", []):
                            validate_task_row(row, conn)
                    for row in tables["materials"]:
                        if not re.fullmatch(r"[a-f0-9]{32}", row["id"]) or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]):
                            raise ValueError("备份资料标识无效。")
                        clean_string(row["source_key"], 120, "来源标识")
                        clean_string(row["title"], 160, "资料标题")
                        clean_string(row["filename"], 180, "文件名")
                        if row["filename"] != row["filename"].replace("\\", "/").split("/")[-1] or row["filename"] in (".", "..") or type(row["size"]) is not int or type(row["created_at"]) is not int or row["created_at"] < 0:
                            raise ValueError("备份文件名或时间无效。")
                        clean_tags(json.loads(row["tags"]))
                        if row["category"] not in CATEGORIES or any(type(row[f]) is not int or row[f] not in (0, 1) for f in ("favorite", "trashed", "missing")):
                            raise ValueError("备份资料状态无效。")
                        data = archive.read("blobs/" + row["sha256"])
                        if not data or len(data) > MAX_UPLOAD or len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"] or file_text(row["filename"], data) != row["text_content"]:
                            raise ValueError("备份资料原件或正文校验失败。")
                        (staged.blobs / row["sha256"]).write_bytes(data)
                    bank = staged.bank()
                    for raw in tables["questions"]:
                        q = json.loads(raw["payload"])
                        if raw["id"] != q.get("id"):
                            raise ValueError("备份题库主键不一致。")
                        validate_stored_question(q, staged)
                    for row in tables["quiz_sessions"]:
                        validate_session(dict(row, task_id="") if legacy else row, staged)
                    for row in tables["quiz_review"]:
                        validate_review(row)
                    for row in tables["generation_drafts"]:
                        validate_draft(row, staged)
                    # Files are content-addressed: write-new only. They stay invisible if DB swap fails.
                    with self.lock:
                        for path in staged.blobs.iterdir():
                            destination = self.blobs / path.name
                            if not destination.exists():
                                temporary = self.blobs / (path.name + ".restore.tmp")
                                temporary.write_bytes(path.read_bytes())
                                os.replace(temporary, destination)
                        temporary_db = self.directory / "restore.sqlite3.tmp"
                        temporary_db.write_bytes(staged.database.read_bytes())
                        os.replace(temporary_db, self.database)
                    return {"restored": True, "materials": len(tables["materials"]), "questions": len(bank), "sessions": len(tables["quiz_sessions"]), "tasks": len(tables.get("study_tasks", []))}
        except (OSError, zipfile.BadZipFile, sqlite3.Error, KeyError, TypeError, AttributeError, json.JSONDecodeError):
            raise ValueError("备份无效或损坏；当前资料和记录未被替换。") from None
