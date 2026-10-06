"""Server-only provider calls; untrusted document -> validated draft -> explicit commit."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit
import uuid

from .quiz import TOPICS
from .storage import clean_string, dumps

FOCUS = {"mechanism": "运行机制", "scenario": "应用场景", "boundary": "边界与误区", "debug": "故障排查", "design": "设计理由"}
MAX_RESPONSE = 2 * 1024 * 1024


class ProviderError(Exception):
    """Safe public error: never include upstream response, keys or document text."""


def provider_target(base_url):
    try:
        url = urlsplit(base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.port not in (None, 443):
            raise ValueError()
        addresses = socket.getaddrinfo(url.hostname, 443, type=socket.SOCK_STREAM)
        ips = {entry[4][0] for entry in addresses}
        if not ips or any(not ipaddress.ip_address(addr).is_global for addr in ips):
            raise ValueError()
        path = url.path.rstrip("/") + "/chat/completions"
        if not re.fullmatch(r"/[A-Za-z0-9._/-]+", path) or ".." in path:
            raise ValueError()
        return url.hostname, sorted(ips), path
    except (ValueError, OSError):
        raise ProviderError("AI 供应商地址无效：仅支持公开 HTTPS 服务的 443 端口。") from None


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Avoid a second DNS lookup after validating all answers (rebinding boundary).
        connection = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
        except Exception:
            connection.close()
            raise


class CompatibleProvider:
    def __init__(self, base_url=None, model=None, api_key=None):
        self.base_url = base_url or os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
        self.model = model or os.environ.get("AI_MODEL", "")
        self._api_key = api_key if api_key is not None else os.environ.get("AI_API_KEY", "")

    @property
    def configured(self):
        return bool(self.model and self._api_key)

    def generate(self, messages):
        if not self.configured:
            raise ProviderError("AI 出题未配置。请在服务端 .env 设置自己的模型及 API Key 后重启。")
        host, addresses, path = provider_target(self.base_url)
        body = dumps({"model": self.model, "messages": messages, "temperature": 0.3,
                      "response_format": {"type": "json_object"}, "max_tokens": 12000}).encode("utf-8")
        connection = PinnedHTTPSConnection(host, addresses[0], timeout=45)
        deadline = time.monotonic() + 60
        try:
            connection.request("POST", path, body, {"Authorization": "Bearer " + self._api_key,
                               "Content-Type": "application/json", "Accept": "application/json"})
            response = connection.getresponse()
            if response.status != 200:  # no redirects, no replay of credentials
                raise ProviderError("AI 服务暂不可用或拒绝请求，请检查配置后重试；题库未改变。")
            raw = bytearray()
            while True:
                if time.monotonic() > deadline:
                    raise ProviderError("AI 请求超时，题库未改变。")
                chunk = response.read1(min(65536, MAX_RESPONSE + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE:
                    raise ProviderError("AI 返回过大，题库未改变。")
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or len(content) > MAX_RESPONSE:
                raise ValueError()
            return json.loads(content)
        except ProviderError:
            raise
        except (OSError, ValueError, KeyError, IndexError, TypeError, http.client.HTTPException):
            raise ProviderError("AI 返回无效或连接失败，题库未改变。请稍后重试。") from None
        finally:
            connection.close()


def generation_request(payload):
    if not isinstance(payload, dict) or payload.get("consent") is not True:
        raise ValueError("需要确认将所选资料发送给你配置的 AI 供应商。")
    allowed = {"material_id", "topic", "count", "keywords", "focus", "audience", "extra_instructions", "consent"}
    if not set(payload) <= allowed:
        raise ValueError("出题参数包含未知字段。")
    if not isinstance(payload.get("topic"), str) or payload["topic"] not in TOPICS or type(payload.get("count")) is not int or not 1 <= payload["count"] <= 20:
        raise ValueError("请选择有效领域及 1—20 道题。")
    if payload.get("audience", "java-intern") != "java-intern":
        raise ValueError("当前支持 Java 实习面试难度。")
    words, focus = payload.get("keywords"), payload.get("focus")
    if not isinstance(words, list) or not 1 <= len(words) <= 12:
        raise ValueError("请提供 1—12 个知识关键词。")
    words = list(dict.fromkeys(clean_string(x, 60, "关键词") for x in words))
    if not isinstance(focus, list) or not focus or len(focus) > len(FOCUS) or any(not isinstance(x, str) or x not in FOCUS for x in focus) or len(set(focus)) != len(focus):
        raise ValueError("请选择至少一个有效考察角度。")
    return {"material_id": clean_string(payload.get("material_id"), 80, "资料编号"),
            "topic": payload["topic"], "count": payload["count"], "keywords": words, "focus": focus,
            "audience": "java-intern", "extra_instructions": clean_string(payload.get("extra_instructions", ""), 1000, "补充要求", True)}


def validate_questions(raw, request, source, check_count=True):
    if not isinstance(request.get("topic"), str) or request["topic"] not in TOPICS or not isinstance(request.get("keywords"), list) or not request["keywords"] or any(not isinstance(w, str) or not w.strip() or len(w) > 60 for w in request["keywords"]) or not isinstance(request.get("focus"), list) or not request["focus"] or any(not isinstance(f, str) or f not in FOCUS for f in request["focus"]):
        raise ValueError("题目知识领域、关键词或考察角度无效。")
    if not isinstance(raw, list) or not raw or len(raw) > 20 or check_count and len(raw) != request["count"]:
        raise ValueError("AI 未生成要求数量的有效题目，草稿未写入。")
    result, seen = [], set()
    for supplied in raw:
        if not isinstance(supplied, dict):
            raise ValueError("题目结构无效。")
        q = {}
        for field, minimum, maximum in (("title", 2, 120), ("prompt", 12, 2400), ("explanation", 60, 6000), ("source_section", 2, 140), ("evidence_quote", 16, 500)):
            q[field] = clean_string(supplied.get(field), maximum, "题目" + field)
            if len(q[field]) < minimum:
                raise ValueError("题干、解析或证据过短，请重新生成。")
        if q["evidence_quote"] not in source["text_content"]:
            raise ValueError("引用证据不在所选资料原文中，草稿未写入。")
        normalized = re.sub(r"\s+", "", q["prompt"]).casefold()
        if normalized in seen:
            raise ValueError("生成结果包含重复题干。")
        seen.add(normalized)
        if supplied.get("type") not in ("single", "multiple") or supplied.get("difficulty") not in ("基础", "应用"):
            raise ValueError("题型或难度无效。")
        q.update(type=supplied["type"], difficulty=supplied["difficulty"], topic=request["topic"])
        options = supplied.get("options")
        if not isinstance(options, list) or len(options) != 4 or any(not isinstance(o, dict) for o in options):
            raise ValueError("每题必须包含 4 个有效选项。")
        if any(not isinstance(o.get("id"), str) for o in options) or {o.get("id") for o in options} != set("abcd"):
            raise ValueError("选项编号必须为 a、b、c、d。")
        q["options"] = sorted([{"id": o["id"], "text": clean_string(o.get("text"), 1200, "选项")} for o in options], key=lambda o: o["id"])
        if len({re.sub(r"\s+", "", o["text"]).casefold() for o in q["options"]}) != 4:
            raise ValueError("选项内容不得重复。")
        answer = supplied.get("answer")
        if not isinstance(answer, list) or any(not isinstance(x, str) for x in answer) or len(set(answer)) != len(answer) or not set(answer) <= set("abcd"):
            raise ValueError("答案编号无效。")
        if q["type"] == "single" and len(answer) != 1 or q["type"] == "multiple" and len(answer) not in (2, 3):
            raise ValueError("答案数量与题型不一致。")
        q["answer"] = sorted(answer)
        explains = supplied.get("option_explanations")
        if not isinstance(explains, dict) or set(explains) != set("abcd"):
            raise ValueError("必须给出全部 4 个选项的逐项解析。")
        q["option_explanations"] = {key: clean_string(explains[key], 1800, "选项解析") for key in "abcd"}
        if any(len(s) < 12 for s in q["option_explanations"].values()):
            raise ValueError("选项解析过短。")
        words = supplied.get("keywords")
        if not isinstance(words, list) or not words or any(not isinstance(x, str) or x not in request["keywords"] for x in words):
            raise ValueError("题目必须对应至少一个指定关键词。")
        q["keywords"] = list(dict.fromkeys(words))
        content = (q["title"] + q["prompt"] + q["explanation"]).casefold()
        if not any(w.casefold() in content for w in q["keywords"]):
            raise ValueError("题目正文没有实际命中指定关键词。")
        if supplied.get("focus") not in request["focus"]:
            raise ValueError("题目考察角度无效。")
        q["focus"] = supplied["focus"]
        q.update(id="q-" + uuid.uuid4().hex, source_key=source["source_key"], source_sha256=source["sha256"])
        q["version"] = hashlib.sha256(dumps(q).encode()).hexdigest()
        result.append(q)
    return result


class Generation:
    def __init__(self, library, provider=None, prompt_path=None):
        self.library = library
        self.provider = provider or CompatibleProvider()
        self.prompt_path = prompt_path or Path(__file__).resolve().parents[2] / "prompts/question-author.md"
        self.capacity = threading.BoundedSemaphore(2)

    def create(self, payload):
        request = generation_request(payload)
        source = self.library.get(request["material_id"])
        # Read and verify exact original bytes rather than silently trust SQLite text.
        self.library.read_file(source["id"])
        if not source["text_content"] or len(source["text_content"]) > 60000:
            raise ValueError("AI 出题需要可读文本资料（MD/TXT/代码等），且正文不超过 6 万字；PDF 可保存，但请另上传你有权使用的文本摘录出题。")
        messages = [{"role": "system", "content": self.prompt_path.read_text(encoding="utf-8")},
                    {"role": "user", "content": dumps({"request": request, "document": source["text_content"]})}]
        if not self.capacity.acquire(blocking=False):
            raise ValueError("已有两项 AI 出题任务正在运行，请稍后重试。")
        try:
            raw = self.provider.generate(messages)
        finally:
            self.capacity.release()
        if not isinstance(raw, dict) or set(raw) != {"questions"}:
            raise ValueError("AI 结果必须是仅含 questions 的 JSON 对象。")
        questions = validate_questions(raw["questions"], request, source)
        used_words = {word for q in questions for word in q["keywords"]}
        used_focus = {q["focus"] for q in questions}
        warnings = ["结构和逐字证据检查已通过；语义准确性仍需你逐题核对，AI 结果可能有误。"]
        if set(request["keywords"]) - used_words:
            warnings.append("部分关键词未覆盖：" + "、".join(sorted(set(request["keywords"]) - used_words)))
        if set(request["focus"]) - used_focus:
            warnings.append("部分考察角度未覆盖：" + "、".join(FOCUS[x] for x in request["focus"] if x not in used_focus))
        identity = uuid.uuid4().hex
        draft = {"id": identity, "questions": questions, "warnings": warnings, "request": request,
                 "source": {k: source[k] for k in ("id", "title", "sha256")}, "committed": False,
                 "quality": {"structure_valid": True, "evidence_exact": True, "requires_human_review": True,
                             "keyword_coverage": len(used_words), "focus_coverage": len(used_focus)}}
        with self.library.lock, self.library.connect() as conn:
            self._current_source(conn, source["id"], source["sha256"])
            conn.execute("INSERT INTO generation_drafts VALUES (?,?,?,?,?,NULL)",
                         (identity, source["id"], source["sha256"], dumps(draft), int(time.time())))
        return draft

    @staticmethod
    def _current_source(conn, identity, digest):
        row = conn.execute("SELECT * FROM materials WHERE id=?", (identity,)).fetchone()
        if not row or row["trashed"] or row["missing"] or row["sha256"] != digest:
            raise ValueError("来源已移除或版本改变，请重新生成；题库未改变。")
        return dict(row)

    def get(self, identity):
        with self.library.connect() as conn:
            row = conn.execute("SELECT * FROM generation_drafts WHERE id=?", (identity,)).fetchone()
        if not row:
            raise KeyError(identity)
        draft = json.loads(row["payload"])
        draft["committed"] = row["committed_at"] is not None
        return draft

    def commit(self, identity, payload):
        if not isinstance(payload, dict) or payload != {"confirmed": True}:
            raise ValueError("请逐题核对解析及原文，再确认写入。")
        with self.library.lock, self.library.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM generation_drafts WHERE id=?", (identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            if row["committed_at"] is not None:
                return {"written": 0, "total": conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], "already_committed": True}
            source = self._current_source(conn, row["material_id"], row["source_sha256"])
            source = self.library.public(source, True)
            self.library.read_file(source["id"])
            draft = json.loads(row["payload"])
            # Revalidate persisted output. Never accept edited client answers or forged IDs.
            checked = validate_questions(draft["questions"], draft["request"], source)
            existing = {re.sub(r"\s+", "", q["prompt"]).casefold() for q in self.library.bank(conn)}
            if any(re.sub(r"\s+", "", q["prompt"]).casefold() in existing for q in checked):
                raise ValueError("题库已有相同题干，请调整关键词重新生成；此次没有部分写入。")
            for original in draft["questions"]:
                conn.execute("INSERT INTO questions VALUES (?,?)", (original["id"], dumps(original)))
            conn.execute("UPDATE generation_drafts SET committed_at=? WHERE id=?", (int(time.time()), identity))
            return {"written": len(checked), "total": conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], "already_committed": False}
