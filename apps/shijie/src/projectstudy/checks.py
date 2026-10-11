"""Objective, version-bound checks; passing is never a mastery certificate."""
from __future__ import annotations

import json
import random
import re
import uuid
from datetime import datetime, timedelta

from .analysis import InputError

THRESHOLD = 80


def mastery_claim(score):
    return "本次客观检测通过；选择题检测不证明独立掌握。" if score >= THRESHOLD else "本次客观检测未达 80 分；请复习后再检测。"


class CheckMixin:
    @staticmethod
    def _public_check(record, project):
        hidden = {"questions", "activity_ids", "attempt_ids", "initial_exposure"}
        return {**{k: v for k, v in record.items() if k not in hidden},
                "stale": record["version"] != project["version"] or record["source_hash"] != project["source_hash"]}

    def checks(self, pid):
        with self._connection() as db:
            project = self._project(db, pid)
            return {"checks": [self._public_check(c, project) for c in self._rows(db, "checks", pid)]}

    def check_detail(self, pid, check_id):
        from .store import identity
        with self._connection() as db:
            project = self._project(db, pid)
            row = db.execute("SELECT payload FROM checks WHERE id=? AND project_id=?", (identity(check_id), pid)).fetchone()
            if not row:
                raise InputError("检测记录不存在。")
            check = json.loads(row[0])
            return {"check": self._public_check(check, project), "questions": check["questions"]}

    @staticmethod
    def _choice_due(latest):
        from .store import now
        if not latest:
            return False
        minutes = 10 if latest["status"] == "incorrect" or latest.get("guessed", False) else 24 * 60
        return datetime.fromisoformat(latest["created_at"]) + timedelta(minutes=minutes) <= datetime.fromisoformat(now())

    def quiz_inventory(self, pid):
        with self._connection() as db:
            project = self._project(db, pid)
            latest = {r["activity_id"]: r for r in self._rows(db, "attempts", pid) if r["version"] == project["version"]}
            choices = [a for a in self._rows(db, "activities", pid) if a["options"] and a["answer_ids"]]
            return {"all": len(choices), "wrong": sum(latest.get(a["id"], {}).get("status") == "incorrect" for a in choices),
                    "due": sum(self._choice_due(latest.get(a["id"])) for a in choices),
                    "unseen": sum(a["id"] not in latest for a in choices), "threshold": THRESHOLD,
                    "review_rule": "答错或标记猜测后 10 分钟复习；明确答对后 1 天复习。"}

    @staticmethod
    def _balanced_selection(activities, count, latest, rng, rotation=0):
        remaining = list(activities)
        rng.shuffle(remaining)
        selected, coverage, kind_coverage, previous = [], {}, {}, None
        def category(activity):
            record = latest.get(activity["id"])
            if not record:
                return "unseen"
            if record["status"] == "incorrect" or record.get("guessed") or CheckMixin._choice_due(record):
                return "review"
            return "recent"
        while remaining and len(selected) < count:
            # Coverage remains first. Alternate review/unseen instead of exhausting
            # all newly imported variants before revisiting a weak or overdue item.
            desired = "review" if (rotation + len(selected)) % 2 == 0 else "unseen"
            activity = min(remaining, key=lambda a: (
                coverage.get(a["concept_id"], 0), a["concept_id"] == previous,
                0 if category(a) == desired else 1 if category(a) != "recent" else 2,
                kind_coverage.get(a.get("kind"), 0),
                0 if a["id"] in latest and CheckMixin._choice_due(latest[a["id"]]) else 1,
                latest.get(a["id"], {}).get("created_at", "")))
            remaining.remove(activity)
            selected.append(activity)
            previous = activity["concept_id"]
            coverage[previous] = coverage.get(previous, 0) + 1
            kind = activity.get("kind")
            kind_coverage[kind] = kind_coverage.get(kind, 0) + 1
        return selected

    def start_check(self, pid, count=5, concept_id=None, scope="all", mode="exam"):
        from .store import identity, now
        if type(count) is not int or not 1 <= count <= 30 or scope not in {"all", "wrong", "due"} or mode not in {"practice", "exam"}:
            raise InputError("检测数量应为 1 至 30，范围应为 all、wrong 或 due，模式为 practice 或 exam。")
        with self._connection(True) as db:
            project = self._project(db, pid)
            if db.execute("SELECT COUNT(*) FROM checks").fetchone()[0] >= 10000:
                raise InputError("检测记录已达到上限，请备份并整理项目。")
            if concept_id is not None:
                identity(concept_id)
                if concept_id not in {c["id"] for c in self._rows(db, "concepts", pid)}:
                    raise InputError("检测知识点不存在。")
            history = [r for r in self._rows(db, "attempts", pid) if r["version"] == project["version"]]
            latest = {r["activity_id"]: r for r in history}
            activities = [a for a in self._rows(db, "activities", pid) if a["options"] and a["answer_ids"]
                          and (concept_id is None or a["concept_id"] == concept_id)
                          and (scope == "all" or scope == "wrong" and latest.get(a["id"], {}).get("status") == "incorrect"
                               or scope == "due" and self._choice_due(latest.get(a["id"])))]
            if not activities:
                raise InputError("当前范围没有可自动评分的选择题，请先导入任务或调整范围。")
            rng = random.SystemRandom()
            rotation = sum(c.get("mode") == "practice" and c["version"] == project["version"] for c in self._rows(db, "checks", pid))
            selected = self._balanced_selection(activities, min(count, len(activities)), latest if mode == "practice" else {}, rng, rotation if mode == "practice" else 0)
            questions = []
            for activity in selected:
                options = [dict(o) for o in activity["options"]]
                rng.shuffle(options)
                questions.append({**{k: activity[k] for k in ("id", "concept_id", "title", "prompt", "kind", "option_mode")},
                                  "options": options})
            hints = [h for h in self._rows(db, "hints", pid) if h["version"] == project["version"]]
            exposure = any(a["id"] in latest or any(h["activity_id"] == a["id"] for h in hints) for a in selected)
            record = {"id": uuid.uuid4().hex, "status": "active", "threshold": THRESHOLD,
                      "question_count": len(selected), "version": project["version"], "source_hash": project["source_hash"],
                      "created_at": now(), "scope": scope, "concept_id": concept_id,
                      "activity_ids": [a["id"] for a in selected], "questions": questions,
                      "initial_exposure": exposure}
            if mode == "practice":
                record.update(mode="practice", answered_count=0, results=[], attempt_ids=[], current_index=0)
            self._insert(db, "checks", record, pid)
            return {"check": self._public_check(record, project), "questions": questions}

    def answer_check(self, pid, check_id, activity_id, answer, guessed=False):
        from .store import identity, now, dumps
        if type(guessed) is not bool:
            raise InputError("猜测标记应为布尔值。")
        with self._connection(True) as db:
            project = self._project(db, pid)
            row = db.execute("SELECT payload FROM checks WHERE id=? AND project_id=?", (identity(check_id), pid)).fetchone()
            if not row:
                raise InputError("检测记录不存在。")
            check = json.loads(row[0])
            if check.get("mode") != "practice":
                raise InputError("只有练习模式支持单题即时反馈。")
            if check["version"] != project["version"] or check["source_hash"] != project["source_hash"]:
                raise InputError("源码或资料版本已改变，请开始新的练习。")
            aid = identity(activity_id)
            previous = next((r for r in check["results"] if r["activity_id"] == aid), None)
            if previous:
                return {"check": self._public_check(check, project), "result": previous, "already_answered": True}
            if check["status"] != "active" or aid != check["activity_ids"][check["current_index"]]:
                raise InputError("请按本轮顺序提交当前题目。")
            submitted = self._submit(db, pid, aid, answer, "none")
            attempt = submitted["attempt"]
            if guessed:
                attempt["guessed"] = True
                db.execute("UPDATE attempts SET payload=? WHERE id=?", (dumps(attempt), attempt["id"]))
            activity = self._activity(db, pid, aid)
            result = {"activity_id": aid, "correct": attempt["status"] == "correct", "score": attempt["score"],
                      "answer_ids": list(activity["answer_ids"]), "explanation": activity["explanation"], "answer": list(answer),
                      "guessed": guessed, "needs_review": guessed or attempt["status"] != "correct"}
            check["results"].append(result)
            check["attempt_ids"].append(attempt["id"])
            check["answered_count"] += 1
            check["current_index"] = check["answered_count"]
            if check["answered_count"] == check["question_count"]:
                correct = sum(r["correct"] for r in check["results"])
                score = round(correct * 100 / check["question_count"], 2)
                exposed = check["initial_exposure"] or any(r["version"] == project["version"] and r["activity_id"] in check["activity_ids"]
                          and r["id"] not in check["attempt_ids"] for table in ("attempts", "hints") for r in self._rows(db, table, pid))
                check.update(status="finished", score=score, correct_count=correct, passed=score >= THRESHOLD,
                             independent=not exposed and not any(r["guessed"] for r in check["results"]),
                             finished_at=now(), mastery_claim=mastery_claim(score))
            db.execute("UPDATE checks SET payload=? WHERE id=? AND project_id=?", (dumps(check), check_id, pid))
            return {"check": self._public_check(check, project), "result": result, "already_answered": False}

    def submit_check(self, pid, check_id, answers):
        from .store import identity, now, dumps
        with self._connection(True) as db:
            project = self._project(db, pid)
            row = db.execute("SELECT payload FROM checks WHERE id=? AND project_id=?", (identity(check_id), pid)).fetchone()
            if not row:
                raise InputError("检测记录不存在。")
            check = json.loads(row[0])
            if check.get("mode") == "practice":
                raise InputError("练习模式应逐题提交，不能使用整卷提交接口。")
            if check["status"] != "active":
                raise InputError("检测已经提交，不能重复提交。")
            if check["version"] != project["version"] or check["source_hash"] != project["source_hash"]:
                raise InputError("源码或资料版本已改变，请开始新的检测。")
            if not isinstance(answers, list) or len(answers) != check["question_count"]:
                raise InputError("请完整提交检测的每一道题。")
            mapped = {}
            for item in answers:
                if not isinstance(item, dict) or set(item) != {"activity_id", "answer"}:
                    raise InputError("检测答案格式不正确。")
                aid = identity(item["activity_id"])
                if aid in mapped or aid not in check["activity_ids"]:
                    raise InputError("检测题目标识重复或不匹配。")
                answer = item["answer"]
                activity = self._activity(db, pid, aid)
                options = {o["id"] for o in activity["options"]}
                if not isinstance(answer, list) or not answer or any(not isinstance(a, str) or a not in options for a in answer) or len(set(answer)) != len(answer) or (activity["option_mode"] == "single" and len(answer) != 1):
                    raise InputError("检测答案必须使用有效且不重复的选项标识。")
                mapped[aid] = answer
            if set(mapped) != set(check["activity_ids"]):
                raise InputError("检测题目与答案不完整匹配。")
            # Re-check exposure at submission: ordinary study may have occurred
            # after the check started. Check attempts are written only afterwards.
            exposed = check["initial_exposure"] or any(r["version"] == project["version"] and r["activity_id"] in mapped
                         for table in ("attempts", "hints") for r in self._rows(db, table, pid))
            results, attempt_ids = [], []
            for aid in check["activity_ids"]:
                submitted = self._submit(db, pid, aid, mapped[aid], "none")
                attempt = submitted["attempt"]
                activity = self._activity(db, pid, aid)
                results.append({"activity_id": aid, "correct": attempt["status"] == "correct", "score": attempt["score"],
                                "answer_ids": list(activity["answer_ids"]), "explanation": activity["explanation"], "answer": mapped[aid]})
                attempt_ids.append(attempt["id"])
            correct = sum(r["correct"] for r in results)
            score = round(correct * 100 / check["question_count"], 2)
            check.update(status="finished", score=score, correct_count=correct, passed=score >= THRESHOLD,
                         independent=not exposed, finished_at=now(), attempt_ids=attempt_ids, results=results,
                         mastery_claim=mastery_claim(score))
            db.execute("UPDATE checks SET payload=? WHERE id=? AND project_id=?", (dumps(check), check_id, pid))
            return {"check": self._public_check(check, project), "results": results}

    def validate_check_records(self, data, project_versions, project_hashes, current_activities, attempt_ids):
        from .store import identity, timestamp, text
        for row in data["checks"]:
            r, pid = row["data"], row["project_id"]
            fields = {"id", "status", "threshold", "question_count", "version", "source_hash", "created_at", "scope", "concept_id", "activity_ids", "questions", "initial_exposure"}
            practice = r.get("mode") == "practice"
            if practice:
                fields |= {"mode", "answered_count", "current_index", "results", "attempt_ids"}
            if r.get("status") == "finished":
                fields |= {"score", "correct_count", "passed", "independent", "finished_at", "attempt_ids", "results", "mastery_claim"}
            if set(r) != fields or r.get("scope") not in {"all", "wrong", "due"} or not isinstance(r.get("source_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", r["source_hash"]):
                raise InputError("检测记录字段或源码哈希不正确。")
            if r["concept_id"] is not None:
                identity(r["concept_id"])
            if r.get("status") not in {"active", "finished"} or type(r.get("version")) is not int or not 1 <= r["version"] <= project_versions[pid] or r.get("threshold") != THRESHOLD or type(r.get("question_count")) is not int or not 1 <= r["question_count"] <= 30 or type(r.get("initial_exposure")) is not bool:
                raise InputError("检测记录结构不正确。")
            timestamp(r.get("created_at"))
            aids, questions = r.get("activity_ids"), r.get("questions")
            if not isinstance(aids, list) or len(aids) != r["question_count"] or len({identity(a) for a in aids}) != len(aids) or not isinstance(questions, list) or len(questions) != len(aids) or [q.get("id") for q in questions if isinstance(q, dict)] != aids:
                raise InputError("检测题目关联不正确。")
            current = r["version"] == project_versions[pid]
            if current and r.get("source_hash") != project_hashes[pid]:
                raise InputError("检测源码哈希不匹配。")
            for q in questions:
                if set(q) != {"id", "concept_id", "title", "prompt", "kind", "option_mode", "options"} or q["kind"] not in {"predict", "trace", "debug", "reproduce", "transfer"} or q["option_mode"] not in {"single", "multiple"}:
                    raise InputError("检测题目字段不正确。")
                identity(q["concept_id"])
                text(q["title"], 200)
                text(q["prompt"], 16000)
                options = q.get("options")
                if not isinstance(options, list) or not 2 <= len(options) <= 8 or any(not isinstance(o, dict) or set(o) != {"id", "text"} for o in options) or len({identity(o["id"]) for o in options}) != len(options):
                    raise InputError("检测选项结构不正确。")
                for option in options:
                    text(option["text"], 3000)
                if current:
                    a = current_activities.get((pid, q["id"]))
                    if not a or not a["answer_ids"] or {o["id"]: o["text"] for o in q["options"]} != {o["id"]: o["text"] for o in a["options"]} or any(q.get(k) != a[k] for k in ("concept_id", "title", "prompt", "kind", "option_mode")):
                        raise InputError("检测题目与当前任务不匹配。")
            if practice:
                answered = r.get("answered_count")
                if type(answered) is not int or not 0 <= answered <= len(aids) or type(r.get("current_index")) is not int or r["current_index"] != answered or (r["status"] == "finished") != (answered == len(aids)):
                    raise InputError("练习进度与题目数量不匹配。")
                records, results = r.get("attempt_ids"), r.get("results")
                if not isinstance(records, list) or len(records) != answered or len(set(records)) != len(records) or not isinstance(results, list) or len(results) != answered:
                    raise InputError("练习作答关联不完整。")
                for aid, attempt_id, result in zip(aids[:answered], records, results):
                    parent = attempt_ids.get(attempt_id)
                    if not parent or parent[0] != pid or parent[1]["activity_id"] != aid or parent[1]["version"] != r["version"] or parent[1]["source_hash"] != r["source_hash"]:
                        raise InputError("练习结果与真实作答不匹配。")
                    attempt = parent[1]
                    guessed = attempt.get("guessed", False)
                    expected = {"activity_id": aid, "correct": attempt["status"] == "correct", "score": attempt["score"],
                                "answer_ids": attempt["assessment"]["answer_ids"], "explanation": attempt["explanation"], "answer": attempt["answer"],
                                "guessed": guessed, "needs_review": guessed or attempt["status"] != "correct"}
                    if result != expected or type(guessed) is not bool:
                        raise InputError("练习反馈与实际作答不匹配。")
            if r["status"] == "finished":
                timestamp(r.get("finished_at"))
                records, results = r.get("attempt_ids"), r.get("results")
                if not isinstance(records, list) or len(records) != len(aids) or len(set(records)) != len(records) or not isinstance(results, list) or len(results) != len(aids) or type(r.get("independent")) is not bool:
                    raise InputError("检测结果结构不正确。")
                expected_correct = 0
                for aid, attempt_id, result in zip(aids, records, results):
                    parent = attempt_ids.get(attempt_id)
                    if not parent or parent[0] != pid or parent[1]["activity_id"] != aid or parent[1]["version"] != r["version"] or parent[1]["source_hash"] != r.get("source_hash") or not isinstance(result, dict):
                        raise InputError("检测作答记录关联不正确。")
                    attempt = parent[1]
                    expected = {"activity_id": aid, "correct": attempt["status"] == "correct", "score": attempt["score"],
                                "answer_ids": attempt["assessment"]["answer_ids"], "explanation": attempt["explanation"], "answer": attempt["answer"]}
                    if practice:
                        guessed = attempt.get("guessed", False)
                        expected.update(guessed=guessed, needs_review=guessed or attempt["status"] != "correct")
                    if result != expected:
                        raise InputError("检测结果与实际作答不匹配。")
                    expected_correct += expected["correct"]
                expected_score = round(expected_correct * 100 / len(aids), 2)
                if type(r.get("correct_count")) is not int or type(r.get("score")) not in {int, float} or r.get("correct_count") != expected_correct or r.get("score") != expected_score or type(r.get("passed")) is not bool or r["passed"] != (expected_score >= THRESHOLD):
                    raise InputError("检测分数与实际作答不匹配。")
                if r["mastery_claim"] != mastery_claim(expected_score):
                    raise InputError("检测结论与客观得分的证据边界不匹配。")
                before_check = []
                for ar in data["attempts"]:
                    if ar["data"]["id"] in records:
                        break
                    before_check.append(ar)
                if r["independent"] and (r["initial_exposure"] or any(attempt_ids[aid][1]["hint_level"] or attempt_ids[aid][1]["assistance"] != "none" or attempt_ids[aid][1].get("guessed", False) for aid in records) or any(ar["project_id"] == pid and ar["data"]["version"] == r["version"] and ar["data"]["activity_id"] in aids for ar in before_check)):
                    raise InputError("检测独立标记与历史辅助或作答冲突。")
