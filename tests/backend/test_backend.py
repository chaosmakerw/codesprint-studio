"""Real temporary SQLite/HTTP, controlled AI stub, no paid network/model calls."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import http.client
import io
import json
from pathlib import Path
import random
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from codesprint.generation import CompatibleProvider, Generation, ProviderError, generation_request, provider_target
from codesprint.quiz import QuizStore
from codesprint.server import Application, ROOT, make_server
from codesprint.storage import Library, dumps

DOCUMENT = "# 测试资料\nJava volatile 保证变量读写的可见性，不保证复合更新操作的原子性。多个线程对同一个共享计数器执行递增时，需要锁或原子类协调读改写。\n"
EVIDENCE = "Java volatile 保证变量读写的可见性，不保证复合更新操作的原子性。"


def question(index=0, multiple=False):
    return {"title": "volatile 的可见性边界", "prompt": f"共享变量被 volatile 修饰，两个线程同时递增。场景 {index} 中应如何理解保证范围？",
            "topic": "concurrency", "type": "multiple" if multiple else "single", "difficulty": "应用",
            "options": [{"id": "a", "text": "可见性保证不能代替读改写的原子性"}, {"id": "b", "text": "可用原子类控制计数器更新"},
                        {"id": "c", "text": "volatile 自动把整个递增过程变成一个原子操作"}, {"id": "d", "text": "只要线程少就不会发生共享状态问题"}],
            "answer": ["a", "b"] if multiple else ["a"], "explanation": "volatile 是可见性机制，递增包含读取、计算和写回多个步骤，并发线程可能读到同一个旧值。保证单次读写可见并不使整个复合操作原子化，因此要使用锁或原子类协调共享计数器。不能根据线程数量推断安全性，应分析是否共享及更新边界。",
            "option_explanations": {"a": "可见性与原子性保证不同，不能用前者推导后者。", "b": "原子类提供复合更新边界，可避免计数器更新丢失。", "c": "递增分成多个步骤，volatile 不会自动把它们合并。", "d": "两个线程也可能共享同一个旧值，线程少不等于安全。"},
            "source_section": "volatile 与共享计数器", "evidence_quote": EVIDENCE, "keywords": ["volatile"], "focus": "boundary"}


class FakeProvider:
    configured = True

    def __init__(self, questions=None):
        self.questions = questions or [question()]
        self.messages = []

    def generate(self, messages):
        self.messages = copy.deepcopy(messages)
        return {"questions": copy.deepcopy(self.questions)}


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codesprint-test-")
        self.library = Library(self.temp.name)
        self.provider = FakeProvider()
        self.generation = Generation(self.library, self.provider)
        self.quiz = QuizStore(self.library, rng=random.Random(7))
        self.source = self.library.upload("study.md", DOCUMENT.encode(), "Java 基础", ["volatile"])
        self.payload = {"material_id": self.source["id"], "topic": "concurrency", "count": 1, "keywords": ["volatile"],
                        "focus": ["boundary"], "audience": "java-intern", "consent": True}

    def tearDown(self):
        self.temp.cleanup()

    def commit(self, questions=None):
        if questions:
            self.provider.questions = questions
            self.payload["count"] = len(questions)
        draft = self.generation.create(self.payload)
        self.generation.commit(draft["id"], {"confirmed": True})
        return draft

    def test_empty_default(self):
        with tempfile.TemporaryDirectory() as directory:
            library = Library(directory)
            quiz = QuizStore(library)
            self.assertEqual(quiz.catalog()["total"], 0)
            self.assertEqual(library.listing()["total"], 0)

    def test_upload_search_metadata_and_trash(self):
        self.assertEqual(self.library.listing("复合更新")["total"], 1)
        result = self.library.patch(self.source["id"], {"title": "我自己的笔记", "favorite": True, "tags": ["并发", "并发"]})
        self.assertEqual(result["tags"], ["并发"])
        self.assertEqual(self.library.listing(favorite=True)["total"], 1)
        self.library.patch(self.source["id"], {"trashed": True})
        self.assertEqual(self.library.listing()["total"], 0)
        self.assertEqual(self.library.listing(trashed=True)["total"], 1)
        self.library.patch(self.source["id"], {"trashed": False})
        self.assertEqual(self.library.read_file(self.source["id"])[1], DOCUMENT.encode())

    def test_upload_rejects_path_empty_and_metadata_hash_write(self):
        for name in ("../secret.md", "a/b.txt", "a\\b.txt"):
            with self.assertRaises(ValueError):
                self.library.upload(name, b"x")
        with self.assertRaises(ValueError):
            self.library.upload("empty.md", b"")
        with self.assertRaises(ValueError):
            self.library.patch(self.source["id"], {"sha256": "0" * 64})

    def test_prompt_separates_untrusted_document(self):
        draft = self.generation.create(self.payload)
        self.assertEqual(len(draft["questions"]), 1)
        messages = self.provider.messages
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertEqual(json.loads(messages[1]["content"])["document"], DOCUMENT)
        self.assertNotIn(DOCUMENT, messages[0]["content"])
        self.assertTrue(draft["quality"]["requires_human_review"])
        self.assertEqual(self.quiz.catalog()["total"], 0)

    def test_generate_commit_is_explicit_and_idempotent(self):
        draft = self.generation.create(self.payload)
        with self.assertRaises(ValueError):
            self.generation.commit(draft["id"], {"confirmed": False})
        result = self.generation.commit(draft["id"], {"confirmed": True})
        self.assertEqual(result["written"], 1)
        self.assertEqual(self.generation.commit(draft["id"], {"confirmed": True})["written"], 0)
        self.assertEqual(self.quiz.catalog()["total"], 1)

    def test_invalid_generation_fields_never_save_partial_draft(self):
        mutations = [lambda q: q.update(evidence_quote="这是来源中完全不存在的证据引用，不应该通过任何校验。"),
                     lambda q: q["options"].__setitem__(1, copy.deepcopy(q["options"][0])),
                     lambda q: q.update(answer=["a", "b"]), lambda q: q.update(option_explanations={"a": "short"}),
                     lambda q: q.update(keywords=["并不存在的关键词"]), lambda q: q.update(focus="unknown")]
        for mutate in mutations:
            q = question()
            mutate(q)
            self.provider.questions = [q]
            with self.assertRaises(ValueError):
                self.generation.create(self.payload)
        with self.library.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM generation_drafts").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 0)

    def test_count_consent_and_duplicate_question_rejected(self):
        for values in ({"count": True}, {"count": 21}, {"consent": False}, {"keywords": []}, {"focus": [{}]}):
            with self.assertRaises(ValueError):
                generation_request(dict(self.payload, **values))
        self.payload["count"] = 2
        self.provider.questions = [question(), question()]
        with self.assertRaises(ValueError):
            self.generation.create(self.payload)

    def test_invalid_commit_source_has_no_partial_writes(self):
        self.provider.questions = [question(0), question(1)]
        self.payload["count"] = 2
        draft = self.generation.create(self.payload)
        self.library.patch(self.source["id"], {"trashed": True})
        with self.assertRaises(ValueError):
            self.generation.commit(draft["id"], {"confirmed": True})
        self.assertEqual(self.library.bank(), [])
        self.assertFalse(self.generation.get(draft["id"])["committed"])

    def test_corrupted_original_cannot_be_committed(self):
        draft = self.generation.create(self.payload)
        (self.library.blobs / self.source["sha256"]).write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            self.generation.commit(draft["id"], {"confirmed": True})
        self.assertEqual(self.library.bank(), [])

    def test_concurrent_commit_is_atomic(self):
        draft = self.generation.create(self.payload)
        with ThreadPoolExecutor(4) as executor:
            results = list(executor.map(lambda _: self.generation.commit(draft["id"], {"confirmed": True}), range(8)))
        self.assertEqual(sum(r["written"] for r in results), 1)

    def test_multiselect_exact_and_exam_feedback_hidden(self):
        self.commit([question(1, True), question(2, True)])
        session = self.quiz.start({"mode": "exam", "count": 2})
        first = session["questions"][0]
        self.assertNotIn("answer", first)
        result = self.quiz.submit(session["id"], {"question_id": first["id"], "selected": ["a"]})
        self.assertNotIn("feedback", result["questions"][0])
        second = result["questions"][1]
        result = self.quiz.submit(session["id"], {"question_id": second["id"], "selected": ["b", "a"]})
        self.assertEqual(result["result"]["correct"], 1)
        self.assertEqual(result["result"]["percent"], 50)
        self.assertFalse(result["result"]["passed"])

    def test_option_explanations_follow_shuffled_display_letters(self):
        self.commit()
        session = self.quiz.start({"count": 1})
        q = session["questions"][0]
        result = self.quiz.submit(session["id"], {"question_id": q["id"], "selected": ["a"]})
        feedback = result["questions"][0]["feedback"]["explanation"]
        original = question()
        for index, option in enumerate(q["options"]):
            expected = chr(65 + index) + "（" + option["text"] + "）：" + original["option_explanations"][option["id"]]
            self.assertIn(expected, feedback)

    def test_fixed_denominator_and_80_percent_boundary(self):
        self.commit([question(i) for i in range(5)])
        for successes in (3, 4):
            session = self.quiz.start({"mode": "practice", "count": 5})
            for index, q in enumerate(session["questions"]):
                session = self.quiz.submit(session["id"], {"question_id": q["id"], "selected": ["a"] if index < successes else []})
            self.assertEqual(session["result"]["passed"], successes == 4)
        session = self.quiz.start({"count": 5})
        first = session["questions"][0]
        self.quiz.submit(session["id"], {"question_id": first["id"], "selected": ["a"]})
        finished = self.quiz.finish(session["id"])
        self.assertEqual(finished["result"]["percent"], 20)
        self.assertEqual(finished["total"], 5)

    def test_first_answer_immutable_sequential_and_restart(self):
        self.commit([question(1), question(2)])
        session = self.quiz.start({"count": 2})
        q1, q2 = session["questions"]
        with self.assertRaises(ValueError):
            self.quiz.submit(session["id"], {"question_id": q2["id"], "selected": ["a"]})
        answer = {"question_id": q1["id"], "selected": ["b"]}
        self.quiz.submit(session["id"], answer)
        with self.assertRaises(ValueError):
            self.quiz.submit(session["id"], dict(answer, selected=["a"]))
        restarted = QuizStore(Library(self.temp.name))
        self.assertEqual(restarted.get(session["id"])["questions"][0]["response"]["selected"], ["b"])
        self.assertEqual(restarted.submit(session["id"], answer)["answered"], 1)
        with ThreadPoolExecutor(4) as executor:
            list(executor.map(lambda _: restarted.submit(session["id"], {"question_id": q2["id"], "selected": ["a"]}), range(4)))
        self.assertEqual(restarted.get(session["id"])["answered"], 2)

    def test_guess_review_due_and_early_success_not_promoted(self):
        self.commit()
        now = [100000]
        self.quiz.clock = lambda: now[0]
        session = self.quiz.start({"count": 1})
        self.quiz.submit(session["id"], {"question_id": session["questions"][0]["id"], "selected": ["a"], "guessed": True})
        self.assertEqual(self.quiz.catalog()["stats"]["wrong"], 1)
        with self.library.connect() as conn:
            review = dict(conn.execute("SELECT * FROM quiz_review").fetchone())
        self.assertEqual(review["due_at"], now[0] + 600)
        session = self.quiz.start({"count": 1})
        self.quiz.submit(session["id"], {"question_id": session["questions"][0]["id"], "selected": ["a"]})
        with self.library.connect() as conn:
            self.assertEqual(conn.execute("SELECT correct_streak FROM quiz_review").fetchone()[0], 0)
        now[0] += 601
        self.assertEqual(self.quiz.catalog()["stats"]["due"], 1)
        session = self.quiz.start({"count": 1, "scope": "due"})
        self.quiz.submit(session["id"], {"question_id": session["questions"][0]["id"], "selected": ["a"]})
        with self.library.connect() as conn:
            self.assertEqual(conn.execute("SELECT correct_streak FROM quiz_review").fetchone()[0], 1)

    def test_trashed_source_unavailable_but_historical_answer_paused(self):
        self.commit()
        session = self.quiz.start({"count": 1})
        self.library.patch(self.source["id"], {"trashed": True})
        self.assertEqual(self.quiz.catalog()["total"], 0)
        result = self.quiz.submit(session["id"], {"question_id": session["questions"][0]["id"], "selected": ["a"]})
        self.assertTrue(result["questions"][0]["feedback"]["review_paused"])
        self.assertEqual(result["result"]["percent"], 100)
        with self.library.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM quiz_review").fetchone()[0], 0)

    def test_backup_restore_complete_rows_and_invalid_is_atomic(self):
        draft = self.commit()
        session = self.quiz.start({"count": 1})
        self.quiz.submit(session["id"], {"question_id": session["questions"][0]["id"], "selected": ["b"]})
        self.library.patch(self.source["id"], {"favorite": True})
        backup = self.library.backup()
        with tempfile.TemporaryDirectory() as directory:
            other = Library(directory)
            QuizStore(other)
            result = other.restore(backup)
            self.assertEqual(result["questions"], 1)
            self.assertEqual(other.get(self.source["id"])["text_content"], DOCUMENT)
            self.assertTrue(other.get(self.source["id"])["favorite"])
            self.assertTrue(Generation(other, self.provider).get(draft["id"])["committed"])
            self.assertEqual(QuizStore(other).get(session["id"])["result"]["percent"], 0)
            with zipfile.ZipFile(io.BytesIO(backup)) as z:
                entries = {n: z.read(n) for n in z.namelist()}
            entries["blobs/" + self.source["sha256"]] = b"corrupt"
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as z:
                for n, v in entries.items():
                    z.writestr(n, v)
            with self.assertRaises(ValueError):
                other.restore(stream.getvalue())
            self.assertEqual(other.read_file(self.source["id"])[1], DOCUMENT.encode())

    def test_provider_refuses_private_http_redirect_style_and_userinfo(self):
        for endpoint in ("http://example.com/v1", "https://user:secret@example.com/v1", "https://example.com/v1?x=secret", "https://example.com:8443/v1"):
            with self.assertRaises(ProviderError):
                provider_target(endpoint)
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1"):
            with patch("codesprint.generation.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]):
                with self.assertRaises(ProviderError):
                    provider_target("https://example.com/v1")
        with patch("codesprint.generation.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]):
            self.assertEqual(provider_target("https://example.com/v1")[2], "/v1/chat/completions")

    def test_missing_provider_config_disables_only_ai(self):
        provider = CompatibleProvider(model="", api_key="")
        self.assertFalse(provider.configured)
        with self.assertRaises(ProviderError):
            provider.generate([])
        self.assertEqual(self.library.listing()["total"], 1)

    def test_restore_rejects_late_forged_answers_draft_and_question_version(self):
        draft = self.commit()
        session = self.quiz.start({"count": 1})
        self.quiz.submit(session["id"], {"question_id": session["questions"][0]["id"], "selected": ["b"]})
        backup = self.library.backup()
        with zipfile.ZipFile(io.BytesIO(backup)) as z:
            entries = {n: z.read(n) for n in z.namelist()}
        baseline = json.loads(entries["backup.json"])

        def corrupt_grade(manifest):
            row = manifest["tables"]["quiz_sessions"][0]
            answers = json.loads(row["answers"])
            next(iter(answers.values()))["correct"] = True
            row["answers"] = dumps(answers)

        def corrupt_draft(manifest):
            row = manifest["tables"]["generation_drafts"][0]
            payload = json.loads(row["payload"])
            payload["request"]["count"] = True
            row["payload"] = dumps(payload)

        def corrupt_version(manifest):
            row = manifest["tables"]["questions"][0]
            payload = json.loads(row["payload"])
            payload["prompt"] += " 被篡改"
            row["payload"] = dumps(payload)

        for mutate in (corrupt_grade, corrupt_draft, corrupt_version):
            manifest = copy.deepcopy(baseline)
            mutate(manifest)
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as z:
                for name, data in entries.items():
                    z.writestr(name, dumps(manifest) if name == "backup.json" else data)
            with self.assertRaises(ValueError):
                self.library.restore(stream.getvalue())
            self.assertEqual(self.quiz.get(session["id"])["result"]["percent"], 0)
            self.assertTrue(self.generation.get(draft["id"])["committed"])
            self.assertEqual(self.library.read_file(self.source["id"])[1], DOCUMENT.encode())

    def test_original_demo_is_18_questions_idempotent_preserving_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Application(directory)
            imported = app.import_demo({"confirmed": True})
            self.assertEqual(imported["materials"], 6)
            self.assertEqual(imported["written"], 18)
            item = app.library.listing()["items"][0]
            app.library.patch(item["id"], {"title": "我的示例标题", "favorite": True})
            again = app.import_demo({"confirmed": True})
            self.assertEqual(again["written"], 0)
            self.assertTrue(again["already_imported"])
            self.assertEqual(app.library.get(item["id"])["title"], "我的示例标题")
            self.assertTrue(app.library.get(item["id"])["favorite"])
            self.assertEqual(app.quiz.catalog()["total"], 18)
            restored = app.library.restore(app.library.backup())
            self.assertEqual(restored["questions"], 18)

    def test_provider_rejects_redirect_and_upstream_body_never_replayed(self):
        class Response:
            status = 302
            def read(self):
                return b"PRIVATE-UPSTREAM-SECRET"
        class Connection:
            def __init__(self, *a, **kw):
                self.requested = False
            def request(self, *a, **kw):
                self.requested = True
            def getresponse(self):
                return Response()
            def close(self):
                pass
        provider = CompatibleProvider("https://example.com/v1", "test-model", "SECRET-API-KEY")
        with patch("codesprint.generation.provider_target", return_value=("example.com", ["8.8.8.8"], "/v1/chat/completions")), patch("codesprint.generation.PinnedHTTPSConnection", Connection):
            with self.assertRaises(ProviderError) as error:
                provider.generate([])
            self.assertNotIn("PRIVATE", str(error.exception))
            self.assertNotIn("SECRET", str(error.exception))

    def test_generation_capacity_released_on_provider_failure(self):
        class Broken:
            configured = True
            def generate(self, messages):
                raise ProviderError("安全供应商错误")
        self.generation.provider = Broken()
        for _ in range(4):
            with self.assertRaises(ProviderError):
                self.generation.create(self.payload)
        self.generation.provider = self.provider
        self.assertEqual(len(self.generation.create(self.payload)["questions"]), 1)


class HTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codesprint-http-")
        self.fake = FakeProvider()
        self.server = make_server(0, self.temp.name, self.fake)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = self.request("GET", "/api/bootstrap")[1]["token"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, method, path, payload=None, headers=None, raw=False):
        hdrs = {"X-Archive-Token": getattr(self, "token", ""), "Content-Type": "application/json"}
        hdrs.update(headers or {})
        body = payload if isinstance(payload, bytes) else dumps(payload).encode() if payload is not None else None
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body, hdrs)
            response = connection.getresponse()
            content = response.read()
            return response.status, content if raw else json.loads(content), dict(response.getheaders())
        finally:
            connection.close()

    def test_host_origin_token_and_options_rejected(self):
        for headers in ({"Host": "evil.test"}, {"Origin": "https://evil.test"}, {"Origin": "null"}, {"Referer": "https://evil.test/x"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("GET", "/api/bootstrap", headers=headers)[0], 403)
        self.assertEqual(self.request("POST", "/api/demo/import", {"confirmed": True}, {"X-Archive-Token": "wrong"})[0], 403)
        self.assertEqual(self.request("OPTIONS", "/api/bootstrap")[0], 403)

    def test_static_cannot_expose_env_database_source_or_parent(self):
        for path in ("/.env", "/data/library.sqlite3", "/src/codesprint/server.py", "/../LICENSE", "/%2e%2e/README.md", "/.git/config"):
            status, data, _ = self.request("GET", path)
            self.assertEqual(status, 404)
            self.assertNotIn("Traceback", dumps(data))

    def test_real_http_upload_generation_commit_quiz_and_backup(self):
        status, item, _ = self.request("POST", "/api/upload?filename=note.md&category=" + __import__("urllib.parse", fromlist=["quote"]).quote("Java 基础"), DOCUMENT.encode(), {"Content-Type": "application/octet-stream"})
        self.assertEqual(status, 201)
        payload = {"material_id": item["id"], "topic": "concurrency", "count": 1, "keywords": ["volatile"], "focus": ["boundary"], "consent": True}
        status, draft, _ = self.request("POST", "/api/generation/drafts", payload)
        self.assertEqual(status, 201)
        self.assertEqual(self.request("GET", "/api/quiz/catalog")[1]["total"], 0)
        self.assertEqual(self.request("POST", f'/api/generation/drafts/{draft["id"]}/commit', {"confirmed": True})[1]["written"], 1)
        status, session, _ = self.request("POST", "/api/quiz/sessions", {"count": 1, "mode": "practice"})
        self.assertEqual(status, 201)
        status, result, _ = self.request("POST", f'/api/quiz/sessions/{session["id"]}/answer', {"question_id": session["questions"][0]["id"], "selected": ["a"]})
        self.assertEqual(result["result"]["percent"], 100)
        status, backup, headers = self.request("GET", "/api/backup", raw=True)
        self.assertEqual(status, 200)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(backup)))
        self.assertEqual(headers["Content-Type"], "application/zip")
        self.assertEqual(self.request("POST", "/api/restore?confirmed=true", backup, {"Content-Type": "application/zip"})[0], 200)

    def test_provider_exception_never_exposes_secret_or_body(self):
        item = self.server.app.library.upload("x.md", DOCUMENT.encode())
        class Broken:
            configured = True
            def generate(self, messages):
                raise RuntimeError("SECRET-API-KEY raw provider body PRIVATE-DOCUMENT")
        self.server.app.generation.provider = Broken()
        payload = {"material_id": item["id"], "topic": "concurrency", "count": 1, "keywords": ["volatile"], "focus": ["boundary"], "consent": True}
        status, data, _ = self.request("POST", "/api/generation/drafts", payload)
        self.assertEqual(status, 500)
        text = dumps(data)
        for marker in ("SECRET", "PRIVATE", "raw provider", DOCUMENT):
            self.assertNotIn(marker, text)
        self.assertEqual(self.server.app.quiz.catalog()["total"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
