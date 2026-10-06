"""Offline, source-bound interview questions. No model or external service calls."""
from __future__ import annotations

import copy
import json
import random
import time
import uuid

TOPICS = {"java": "Java 基础", "concurrency": "并发与 JVM", "spring": "Spring 与 HTTP",
          "data": "数据库 / Redis / MQ", "project": "Agent 项目", "algorithm": "算法思路"}
INTERVAL_DAYS = (1, 3, 7, 14, 30)


def explanation_for_display(question):
    explanation = question["explanation"]
    if question.get("option_explanations"):
        # IDs stay stable for grading while the displayed A/B/C/D are shuffled.
        explanation += "\n\n" + "\n".join(
            chr(65 + index) + "（" + option["text"] + "）：" + question["option_explanations"][option["id"]]
            for index, option in enumerate(question["options"]))
    return explanation


def research_sources():
    return []


class QuizStore:
    def __init__(self, library, bank=None, clock=time.time, rng=None):
        self.library = library
        self._fixture_bank = copy.deepcopy(bank) if bank is not None else None
        self.clock = clock
        self.rng = rng or random.SystemRandom()
        with library.lock, library.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS quiz_sessions (
              id TEXT PRIMARY KEY, mode TEXT NOT NULL, topic TEXT NOT NULL, scope TEXT NOT NULL,
              questions TEXT NOT NULL, answers TEXT NOT NULL, created_at INTEGER NOT NULL,
              finished_at INTEGER)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS quiz_review (
              question_id TEXT PRIMARY KEY, version TEXT NOT NULL, attempts INTEGER NOT NULL,
              wrong_count INTEGER NOT NULL, needs_review INTEGER NOT NULL, correct_streak INTEGER NOT NULL,
              due_at INTEGER NOT NULL, last_answered_at INTEGER NOT NULL)""")

    @property
    def bank(self):
        return self._fixture_bank if self._fixture_bank is not None else self.library.bank()

    def available(self, conn):
        sources = {r["source_key"]: dict(r) for r in conn.execute(
            "SELECT id,source_key,title,sha256 FROM materials WHERE trashed=0 AND missing=0")}
        result = []
        for raw in (self._fixture_bank if self._fixture_bank is not None else self.library.bank(conn)):
            source = sources.get(raw["source_key"])
            if not source or source["sha256"] != raw["source_sha256"]:
                continue
            q = copy.deepcopy(raw)
            q["source"] = {"id": source["id"], "title": source["title"], "sha256": source["sha256"],
                           "section": q["source_section"], "url": "/index.html?material=" + source["id"]}
            result.append(q)
        return result

    def catalog(self, material=""):
        now = int(self.clock())
        with self.library.connect() as conn:
            questions = self.available(conn)
            available_count = len(questions)
            if material:
                questions = [q for q in questions if q["source"]["id"] == material]
            states = {r["question_id"]: dict(r) for r in conn.execute("SELECT * FROM quiz_review")}
            current = {q["id"]: states[q["id"]] for q in questions
                       if q["id"] in states and states[q["id"]]["version"] == q["version"]}
            active = conn.execute("SELECT id FROM quiz_sessions WHERE finished_at IS NULL ORDER BY created_at DESC,rowid DESC LIMIT 1").fetchone()
            recent = conn.execute("SELECT * FROM quiz_sessions WHERE finished_at IS NOT NULL ORDER BY finished_at DESC,rowid DESC LIMIT 5").fetchall()
        return {"total": len(questions), "unavailable": len(self.bank) - available_count,
                "topics": [{"id": key, "label": label, "count": sum(q["topic"] == key for q in questions)} for key, label in TOPICS.items()],
                "stats": {"practiced": len(current), "wrong": sum(r["needs_review"] for r in current.values()),
                          "due": sum(r["due_at"] <= now for r in current.values())},
                "active": active["id"] if active else None,
                "recent": [{k: v for k, v in self.public(dict(r)).items() if k != "questions"} for r in recent],
                "material": material, "sources": research_sources()}

    def start(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("请求格式无效。")
        mode, topic, scope = payload.get("mode", "practice"), payload.get("topic", ""), payload.get("scope", "all")
        count, material = payload.get("count", 10), payload.get("material", "")
        if mode not in ("practice", "exam") or topic not in ("", *TOPICS) or scope not in ("mixed", "wrong", "due", "all"):
            raise ValueError("练习范围无效。")
        if type(count) is not int or not 1 <= count <= 30 or not isinstance(material, str):
            raise ValueError("每轮题数应为 1—30 题。")
        now = int(self.clock())
        with self.library.lock, self.library.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidates = [q for q in self.available(conn) if (not topic or q["topic"] == topic)
                          and (not material or q["source"]["id"] == material)]
            states = {r["question_id"]: dict(r) for r in conn.execute("SELECT * FROM quiz_review")}
            def state(q):
                r = states.get(q["id"])
                return r if r and r["version"] == q["version"] else None
            if scope == "wrong":
                candidates = [q for q in candidates if state(q) and state(q)["needs_review"]]
            elif scope == "due":
                candidates = [q for q in candidates if state(q) and state(q)["due_at"] <= now]
            if not candidates:
                raise ValueError("这个范围暂时没有可练题目，请选择其他主题或全部题目。")
            self.rng.shuffle(candidates)
            if scope == "mixed":
                def priority(q):
                    r = state(q)
                    return 0 if r and r["due_at"] <= now else 1 if r and r["needs_review"] else 2 if not r else 3
                candidates.sort(key=priority)
            if mode == "exam" and not topic and scope == "all" and not material and count >= len(TOPICS):
                first = [next((q for q in candidates if q["topic"] == t), None) for t in TOPICS]
                first = [q for q in first if q]
                candidates = first + [q for q in candidates if q not in first]
            chosen = candidates[:count]
            for q in chosen:
                self.rng.shuffle(q["options"])
            identity = uuid.uuid4().hex
            conn.execute("INSERT INTO quiz_sessions VALUES (?,?,?,?,?,?,?,NULL)",
                         (identity, mode, topic, scope, json.dumps(chosen, ensure_ascii=False), "{}", now))
            return self.public(dict(conn.execute("SELECT * FROM quiz_sessions WHERE id=?", (identity,)).fetchone()))

    @staticmethod
    def public(row):
        questions, answers = json.loads(row["questions"]), json.loads(row["answers"])
        finished = row["finished_at"] is not None
        reveal = finished or row["mode"] == "practice"
        public_questions = []
        for q in questions:
            item = {k: q[k] for k in ("id", "topic", "type", "title", "prompt", "options", "difficulty")}
            a = answers.get(q["id"])
            if a:
                item["response"] = {"selected": a["selected"], "guessed": a["guessed"]}
                if reveal:
                    item["feedback"] = {"correct": a["correct"], "answer": q["answer"],
                                        "explanation": explanation_for_display(q), "source": q["source"], "due_at": a["due_at"],
                                        "review_paused": a.get("review_paused", False)}
            public_questions.append(item)
        total, correct = len(questions), sum(a["correct"] for a in answers.values())
        return {"id": row["id"], "mode": row["mode"], "topic": row["topic"], "scope": row["scope"],
                "created_at": row["created_at"], "finished": finished, "answered": len(answers), "total": total,
                "questions": public_questions,
                "result": {"correct": correct, "total": total, "percent": round(100 * correct / total, 1),
                           "passed": correct * 100 >= total * 80,
                           "guessed": sum(a["guessed"] for a in answers.values())} if finished else None}

    def get(self, identity):
        with self.library.connect() as conn:
            row = conn.execute("SELECT * FROM quiz_sessions WHERE id=?", (identity,)).fetchone()
        if not row:
            raise KeyError(identity)
        return self.public(dict(row))

    def finish(self, identity):
        with self.library.lock:
            session = self.get(identity)
            for q in session["questions"]:
                if "response" not in q:
                    session = self.submit(identity, {"question_id": q["id"], "selected": []})
            return session

    def submit(self, identity, payload):
        if not isinstance(payload, dict):
            raise ValueError("作答格式无效。")
        selected, guessed = payload.get("selected"), payload.get("guessed", False)
        if not isinstance(selected, list) or any(not isinstance(x, str) for x in selected) or len(set(selected)) != len(selected) or type(guessed) is not bool:
            raise ValueError("请选择有效且不重复的选项。")
        now = int(self.clock())
        with self.library.lock, self.library.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM quiz_sessions WHERE id=?", (identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            row = dict(row)
            questions, answers = json.loads(row["questions"]), json.loads(row["answers"])
            q = next((q for q in questions if q["id"] == payload.get("question_id")), None)
            if not q or not set(selected) <= {o["id"] for o in q["options"]} or q["type"] == "single" and len(selected) > 1:
                raise ValueError("题号或选项无效。")
            previous = answers.get(q["id"])
            if previous:
                if set(previous["selected"]) != set(selected) or previous["guessed"] != guessed:
                    raise ValueError("首答已保存，不能改写；请在下一轮复习。")
                return self.public(row)
            pending = next((item for item in questions if item["id"] not in answers), None)
            if not pending or pending["id"] != q["id"]:
                raise ValueError("请按本轮题目顺序作答。")
            correct = set(selected) == set(q["answer"])
            current_question = next((b for b in self.bank if b["id"] == q["id"]), None)
            current_source = conn.execute("SELECT sha256,trashed,missing FROM materials WHERE source_key=?", (q["source_key"],)).fetchone()
            review_paused = not (current_question and current_question["version"] == q["version"]
                                 and current_source and current_source["sha256"] == q["source_sha256"]
                                 and not current_source["trashed"] and not current_source["missing"])
            old = conn.execute("SELECT * FROM quiz_review WHERE question_id=?", (q["id"],)).fetchone()
            if old and old["version"] != q["version"]:
                old = None
            streak = old["correct_streak"] if old else 0
            weak = not correct or guessed
            if weak:
                streak, due = 0, now + 600
            elif old and now < old["due_at"]:
                due = old["due_at"]
                weak = bool(old["needs_review"])
            else:
                streak += 1
                due = now + INTERVAL_DAYS[min(streak - 1, len(INTERVAL_DAYS) - 1)] * 86400
            attempts, wrong = (old["attempts"] if old else 0) + 1, (old["wrong_count"] if old else 0) + (not correct)
            if not review_paused:
                conn.execute("INSERT OR REPLACE INTO quiz_review VALUES (?,?,?,?,?,?,?,?)",
                             (q["id"], q["version"], attempts, wrong, int(weak), streak, due, now))
            answers[q["id"]] = {"selected": sorted(selected), "guessed": guessed, "correct": correct,
                                 "due_at": None if review_paused else due, "review_paused": review_paused}
            finished_at = now if len(answers) == len(questions) else None
            row.update(answers=json.dumps(answers, ensure_ascii=False), finished_at=finished_at)
            conn.execute("UPDATE quiz_sessions SET answers=?,finished_at=? WHERE id=?", (row["answers"], finished_at, identity))
            return self.public(row)
