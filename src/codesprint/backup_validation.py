"""Validate every field of restored quiz/draft state before replacing live data."""
from __future__ import annotations

import hashlib
import json
import re

from .generation import generation_request, validate_questions
from .quiz import TOPICS
from .storage import dumps


def identity(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def natural(value):
    return type(value) is int and value >= 0


def question_version(q):
    base = {k: v for k, v in q.items() if k not in ("version", "source")}
    base["options"] = sorted(base["options"], key=lambda o: o["id"])
    return hashlib.sha256(dumps(base).encode()).hexdigest()


def validate_stored_question(q, library):
    if not isinstance(q, dict) or not identity(q.get("id"), r"[a-z0-9-]{1,80}") or q.get("topic") not in TOPICS:
        raise ValueError("备份题目编号或领域无效。")
    if not identity(q.get("version"), r"[a-f0-9]{64}") or q["version"] != question_version(q):
        raise ValueError("备份题目版本校验失败。")
    with library.connect() as conn:
        row = conn.execute("SELECT * FROM materials WHERE source_key=?", (q.get("source_key"),)).fetchone()
    if not row or q.get("source_sha256") != row["sha256"]:
        raise ValueError("备份题目来源校验失败。")
    source = library.public(row, True)
    validate_questions([q], {"count": 1, "topic": q["topic"], "keywords": q.get("keywords"), "focus": [q.get("focus")]}, source)
    if "source" in q:
        reference = q["source"]
        if not isinstance(reference, dict) or reference.get("id") != source["id"] or reference.get("sha256") != source["sha256"] or reference.get("url") != "/index.html?material=" + source["id"] or reference.get("section") != q["source_section"]:
            raise ValueError("备份练习来源引用无效。")


def validate_session(row, library):
    if not identity(row["id"], r"[a-f0-9]{32}") or row["mode"] not in ("practice", "exam") or row["topic"] not in ("", *TOPICS) or row["scope"] not in ("all", "mixed", "wrong", "due") or not natural(row["created_at"]):
        raise ValueError("备份练习字段无效。")
    questions, answers = json.loads(row["questions"]), json.loads(row["answers"])
    if not isinstance(questions, list) or not 1 <= len(questions) <= 30 or not isinstance(answers, dict):
        raise ValueError("备份题目和首答结构无效。")
    for q in questions:
        validate_stored_question(q, library)
    ids = [q["id"] for q in questions]
    if len(set(ids)) != len(ids) or list(answers) != ids[:len(answers)] or len(answers) > len(ids):
        raise ValueError("备份首答顺序或题号无效。")
    complete = len(answers) == len(ids)
    if complete != (row["finished_at"] is not None) or row["finished_at"] is not None and (not natural(row["finished_at"]) or row["finished_at"] < row["created_at"]):
        raise ValueError("备份完成状态无效。")
    for q in questions[:len(answers)]:
        a = answers[q["id"]]
        if not isinstance(a, dict) or set(a) != {"selected", "guessed", "correct", "due_at", "review_paused"}:
            raise ValueError("备份首答字段无效。")
        selected = a["selected"]
        if not isinstance(selected, list) or any(not isinstance(x, str) for x in selected) or len(set(selected)) != len(selected) or not set(selected) <= set("abcd") or q["type"] == "single" and len(selected) > 1:
            raise ValueError("备份已选答案无效。")
        if any(type(a[k]) is not bool for k in ("correct", "guessed", "review_paused")) or a["correct"] != (set(selected) == set(q["answer"])):
            raise ValueError("备份判分与答案不一致。")
        if a["review_paused"] and a["due_at"] is not None or not a["review_paused"] and not natural(a["due_at"]):
            raise ValueError("备份复习时间无效。")


def validate_review(row):
    if not identity(row["question_id"], r"[a-z0-9-]{1,80}") or not identity(row["version"], r"[a-f0-9]{64}") or any(not natural(row[k]) for k in ("attempts", "wrong_count", "correct_streak", "due_at", "last_answered_at")) or type(row["needs_review"]) is not int or row["needs_review"] not in (0, 1):
        raise ValueError("备份复习字段无效。")
    if row["wrong_count"] > row["attempts"] or row["correct_streak"] > row["attempts"]:
        raise ValueError("备份复习计数无效。")


def validate_draft(row, library):
    if not identity(row["id"], r"[a-f0-9]{32}") or not natural(row["created_at"]) or row["committed_at"] is not None and (not natural(row["committed_at"]) or row["committed_at"] < row["created_at"]):
        raise ValueError("备份草稿状态无效。")
    draft = json.loads(row["payload"])
    source = library.get(row["material_id"], True)
    if not isinstance(draft, dict) or draft.get("id") != row["id"] or source["sha256"] != row["source_sha256"] or not isinstance(draft.get("source"), dict) or draft["source"].get("id") != source["id"] or draft["source"].get("sha256") != source["sha256"]:
        raise ValueError("备份草稿来源无效。")
    request = generation_request(dict(draft["request"], consent=True))
    if request != draft["request"] or request["material_id"] != source["id"]:
        raise ValueError("备份草稿出题要求无效。")
    checked = validate_questions(draft["questions"], request, source)
    if len({q["id"] for q in draft["questions"]}) != len(checked):
        raise ValueError("备份草稿题号重复。")
    for q in draft["questions"]:
        validate_stored_question(q, library)
    if not isinstance(draft.get("warnings"), list) or any(not isinstance(w, str) or len(w) > 1000 for w in draft["warnings"]):
        raise ValueError("备份草稿提示无效。")
    if row["committed_at"] is not None:
        with library.connect() as conn:
            for q in draft["questions"]:
                stored = conn.execute("SELECT payload FROM questions WHERE id=?", (q["id"],)).fetchone()
                if not stored or json.loads(stored["payload"])["version"] != q["version"]:
                    raise ValueError("备份已写入草稿与题库不一致。")
