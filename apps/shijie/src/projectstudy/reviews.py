"""Periodic project explanations are learner statements, never automatic mastery scores."""
from __future__ import annotations

from datetime import datetime, timedelta
import uuid

from .analysis import InputError

REVIEW_PROMPTS = [
    {"id": "purpose", "title": "这个项目解决什么问题？", "prompt": "用自己的话说出使用者、具体场景、输入和最终结果。"},
    {"id": "chain", "title": "选一个功能，讲完调用顺序", "prompt": "从入口开始，按实际方法说明谁调用谁、每步接收什么、返回什么。"},
    {"id": "boundary", "title": "追踪一个正常或非法输入", "prompt": "写出参数的类型和值如何变化、在哪一步拒绝，以及哪些后续调用不会发生。"},
    {"id": "next_step", "title": "哪些地方还不能独立解释？", "prompt": "写出不确定的知识点、需要核对的证据和下一次准备复现的一个小行为。"},
]
REVIEW_KEYS = {p["id"] for p in REVIEW_PROMPTS}


class ReviewMixin:
    def _review_state(self, db, pid):
        from .store import now
        project = self._project(db, pid)
        settings = self._rows(db, "review_settings", pid)
        interval = settings[-1]["interval_days"] if settings else 3
        history = [{**r, "stale": r["version"] != project["version"] or r["source_hash"] != project["source_hash"]}
                   for r in self._rows(db, "reviews", pid)]
        current = [r for r in history if not r["stale"]]
        due_at = ((datetime.fromisoformat(current[-1]["created_at"]) + timedelta(days=interval)).isoformat(timespec="seconds")
                  if current else project["updated_at"])
        return {"interval_days": interval, "due": not current or datetime.fromisoformat(due_at) <= datetime.fromisoformat(now()), "due_at": due_at,
                "current_version": project["version"], "prompts": REVIEW_PROMPTS, "history": history,
                "mastery_claim": "回顾保存本人解释与辅助程度，不自动评分，也不证明独立掌握。"}

    def reviews(self, pid):
        with self._connection() as db:
            return self._review_state(db, pid)

    def set_review_interval(self, pid, interval_days):
        from .store import now
        if type(interval_days) is not int or not 1 <= interval_days <= 30:
            raise InputError("回顾间隔应为 1 至 30 天。")
        with self._connection(True) as db:
            self._project(db, pid)
            db.execute("DELETE FROM review_settings WHERE project_id=?", (pid,))
            self._insert(db, "review_settings", {"id": pid, "interval_days": interval_days, "updated_at": now()}, pid)
            return self._review_state(db, pid)

    def submit_review(self, pid, answers, assistance="none"):
        from .store import now, text
        if not isinstance(answers, dict) or set(answers) != REVIEW_KEYS or assistance not in {"none", "reference", "agent"}:
            raise InputError("回顾需完成四项本人解释，并说明辅助方式。")
        checked = {key: text(answers[key], 12000) for key in REVIEW_KEYS}
        with self._connection(True) as db:
            project = self._project(db, pid)
            if db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] >= 10000:
                raise InputError("回顾记录已达到上限，请备份并整理项目。")
            record = {"id": uuid.uuid4().hex, "version": project["version"], "source_hash": project["source_hash"],
                      "created_at": now(), "answers": checked, "assistance": assistance, "status": "self_reported",
                      "mastery_claim": "本人回顾原文；软件未验证解释正确性、实际运行或独立掌握。"}
            self._insert(db, "reviews", record, pid)
            return {"review": record, **self._review_state(db, pid)}

    def validate_review_records(self, data, project_versions, project_hashes):
        from .store import text, timestamp
        for row in data["review_settings"]:
            record = row["data"]
            if set(record) != {"id", "interval_days", "updated_at"} or record["id"] != row["project_id"] or type(record["interval_days"]) is not int or not 1 <= record["interval_days"] <= 30:
                raise InputError("回顾间隔备份无效。")
            timestamp(record["updated_at"])
        for row in data["reviews"]:
            record, pid = row["data"], row["project_id"]
            if set(record) != {"id", "version", "source_hash", "created_at", "answers", "assistance", "status", "mastery_claim"} or type(record["version"]) is not int or not 1 <= record["version"] <= project_versions[pid] or record["assistance"] not in {"none", "reference", "agent"} or record["status"] != "self_reported":
                raise InputError("回顾记录备份无效。")
            self._validate_historical_hash(record, pid, project_versions, project_hashes)
            timestamp(record["created_at"])
            if not isinstance(record["answers"], dict) or set(record["answers"]) != REVIEW_KEYS:
                raise InputError("回顾原文不完整。")
            for answer in record["answers"].values():
                text(answer, 12000)
            if record["mastery_claim"] != "本人回顾原文；软件未验证解释正确性、实际运行或独立掌握。":
                raise InputError("回顾不能冒充自动验收结论。")
