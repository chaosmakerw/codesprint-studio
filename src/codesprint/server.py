"""Loopback-only HTTP server, static allowlist, CSRF guards and safe responses."""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urlsplit
import uuid
import webbrowser

from .generation import Generation, ProviderError, validate_questions
from .quiz import QuizStore, TOPICS
from .storage import CATEGORIES, Library, MAX_UPLOAD, dumps, file_text

ROOT = Path(__file__).resolve().parents[2]
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def load_env(path):
    """Tiny .env support; only known keys, no evaluation/interpolation or override."""
    if not path.is_file():
        return
    if path.stat().st_size > 16384:
        raise ValueError("本地 .env 文件过大。")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and key.strip() in {"AI_BASE_URL", "AI_MODEL", "AI_API_KEY"}:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


class Application:
    def __init__(self, data_dir, provider=None, root=ROOT):
        self.root = Path(root).resolve()
        self.library = Library(data_dir)
        self.quiz = QuizStore(self.library)
        self.generation = Generation(self.library, provider, self.root / "prompts/question-author.md")
        self.token = secrets.token_urlsafe(32)

    def import_demo(self, payload):
        if payload != {"confirmed": True}:
            raise ValueError("请确认导入项目原创、Apache-2.0 许可的示例。")
        example_dir = self.root / "examples"
        rows = json.loads((example_dir / "questions-demo.json").read_text(encoding="utf-8"))
        if not isinstance(rows, list) or not rows or len(rows) > 100:
            raise ValueError("示例题库结构无效。")
        files, material_rows, source_map, all_questions = {}, [], {}, []
        with self.library.lock, self.library.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for source_key in sorted({q["source_key"] for q in rows}):
                if not re.fullmatch(r"demo:[a-z0-9-]{1,80}", source_key):
                    raise ValueError("示例来源标识无效。")
                slug = source_key.split(":", 1)[1]
                content = (example_dir / "materials" / (slug + ".md")).read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                text = file_text(slug + ".md", content)
                existing = conn.execute("SELECT * FROM materials WHERE source_key=?", (source_key,)).fetchone()
                if existing:
                    if existing["sha256"] != digest:
                        raise ValueError("示例资料版本改变，请先备份并单独上传新版；原有个人元数据未覆盖。")
                    source = self.library.public(existing, True)
                else:
                    identity = uuid.uuid4().hex
                    title = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("# ")), slug)
                    material_row = (identity, source_key, title, slug + ".md", "面试准备", "[]", digest, len(content), text, 0, 0, 0, int(time.time()))
                    material_rows.append(material_row)
                    files[digest] = content
                    source = {"id": identity, "source_key": source_key, "sha256": digest, "text_content": text}
                source_map[source_key] = source
            for raw in rows:
                request = {"count": 1, "topic": raw["topic"], "keywords": raw["keywords"], "focus": [raw["focus"]]}
                q = validate_questions([raw], request, source_map[raw["source_key"]])[0]
                q["id"] = "demo-q-" + hashlib.sha256((q["source_key"] + q["prompt"]).encode()).hexdigest()[:24]
                q.pop("version")
                q["version"] = hashlib.sha256(dumps(q).encode()).hexdigest()
                all_questions.append(q)
            if len({q["id"] for q in all_questions}) != len(all_questions):
                raise ValueError("示例含重复题干。")
            # Validate all examples before files/rows become visible. Existing titles/favorites untouched.
            for digest, data in files.items():
                target = self.library.blobs / digest
                if not target.exists():
                    temp = self.library.blobs / (digest + ".demo.tmp")
                    temp.write_bytes(data)
                    os.replace(temp, target)
            for row in material_rows:
                conn.execute("INSERT INTO materials VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            written = 0
            for q in all_questions:
                previous = conn.execute("SELECT payload FROM questions WHERE id=?", (q["id"],)).fetchone()
                if previous:
                    if json.loads(previous["payload"])["version"] != q["version"]:
                        raise ValueError("示例题版本冲突，未部分写入。")
                    continue
                conn.execute("INSERT INTO questions VALUES (?,?)", (q["id"], dumps(q)))
                written += 1
            return {"materials": len(material_rows), "written": written,
                    "total": conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], "already_imported": written == 0}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "CodeSprintStudio"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass  # Do not log paths, uploaded text, prompts, tokens or upstream errors.

    def send_bytes(self, status, content, kind="application/json; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(content)))
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def respond(self, value, status=200):
        self.send_bytes(status, dumps(value).encode())

    def guard(self, write=False):
        port = self.server.server_address[1]
        hosts = {"127.0.0.1:" + str(port), "localhost:" + str(port)}
        if self.headers.get("Host", "").lower() not in hosts:
            return False
        allowed = {"http://" + host for host in hosts}
        origin = self.headers.get("Origin")
        referer = self.headers.get("Referer")
        if origin is not None and origin not in allowed:
            return False
        if referer and (urlsplit(referer).scheme + "://" + urlsplit(referer).netloc) not in allowed:
            return False
        if self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
            return False
        if write and not secrets.compare_digest(self.headers.get("X-Archive-Token", ""), self.server.app.token):
            return False
        return True

    def body(self, limit=MAX_UPLOAD):
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or len(length) > 10 or self.headers.get("Transfer-Encoding"):
            raise ValueError("请求必须提供有效 Content-Length，不能使用分块传输。")
        size = int(length)
        if size > limit:
            raise ValueError("请求内容超过大小限制。")
        data = self.rfile.read(size)
        if len(data) != size:
            raise ValueError("请求内容不完整。")
        return data

    def json_body(self):
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            raise ValueError("请使用 application/json 请求。")
        try:
            value = json.loads(self.body(128 * 1024))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("JSON 格式无效。") from None
        if not isinstance(value, dict):
            raise ValueError("请求必须是 JSON 对象。")
        return value

    def dispatch(self):
        if not self.guard(self.command in ("POST", "PATCH", "DELETE")):
            self.respond({"error": "请求来源或本地访问令牌无效。", "code": "FORBIDDEN"}, 403)
            return
        url = urlsplit(self.path)
        path = unquote(url.path)
        query = parse_qs(url.query, max_num_fields=20)
        app = self.server.app
        if self.command in ("GET", "HEAD"):
            if path == "/api/bootstrap":
                self.respond({"token": app.token, "categories": CATEGORIES, "aiConfigured": app.generation.provider.configured,
                              "topics": [{"id": key, "label": label} for key, label in TOPICS.items()]})
            elif path == "/api/materials":
                self.respond(app.library.listing(query.get("search", [""])[0], query.get("category", [""])[0],
                                                 query.get("trashed", ["false"])[0] == "true", query.get("favorite", ["false"])[0] == "true"))
            elif match := re.fullmatch(r"/api/materials/([a-f0-9]{32})(/file)?", path):
                if match[2]:
                    item, data = app.library.read_file(match[1])
                    self.send_bytes(200, data, "application/octet-stream", {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(item["filename"], safe="")})
                else:
                    self.respond(app.library.get(match[1], query.get("trashed", ["false"])[0] == "true"))
            elif path == "/api/quiz/catalog":
                self.respond(app.quiz.catalog(query.get("material", [""])[0]))
            elif match := re.fullmatch(r"/api/quiz/sessions/([a-f0-9]{32})", path):
                self.respond(app.quiz.get(match[1]))
            elif match := re.fullmatch(r"/api/generation/drafts/([a-f0-9]{32})", path):
                self.respond(app.generation.get(match[1]))
            elif path == "/api/backup":
                self.send_bytes(200, app.library.backup(), "application/zip", {"Content-Disposition": 'attachment; filename="codesprint-backup.zip"'})
            elif path.startswith("/api/"):
                raise KeyError(path)
            else:
                self.static(path)
        elif self.command == "PATCH" and (match := re.fullmatch(r"/api/materials/([a-f0-9]{32})", path)):
            self.respond(app.library.patch(match[1], self.json_body()))
        elif self.command == "POST":
            if path == "/api/upload":
                self.respond(app.library.upload(query.get("filename", [""])[0], self.body(), query.get("category", ["其他资料"])[0], query.get("tags", [""])[0]), 201)
            elif path == "/api/restore":
                if query.get("confirmed") != ["true"]:
                    raise ValueError("恢复会替换当前全部数据，请先备份并明确确认。")
                if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/zip":
                    raise ValueError("恢复需要 application/zip 内容。")
                self.respond(app.library.restore(self.body(128 * 1024 * 1024)))
            elif path == "/api/demo/import":
                self.respond(app.import_demo(self.json_body()))
            elif path == "/api/quiz/sessions":
                self.respond(app.quiz.start(self.json_body()), 201)
            elif match := re.fullmatch(r"/api/quiz/sessions/([a-f0-9]{32})/(answer|finish)", path):
                payload = self.json_body()
                self.respond(app.quiz.submit(match[1], payload) if match[2] == "answer" else app.quiz.finish(match[1]))
            elif path == "/api/generation/drafts":
                self.respond(app.generation.create(self.json_body()), 201)
            elif match := re.fullmatch(r"/api/generation/drafts/([a-f0-9]{32})/commit", path):
                self.respond(app.generation.commit(match[1], self.json_body()))
            else:
                raise KeyError(path)
        else:
            self.respond({"error": "此接口不支持该方法。"}, 405)

    def static(self, path):
        if path == "/":
            path = "/index.html"
        relative = Path(path.lstrip("/"))
        web = self.server.app.root / "web"
        target = (web / relative).resolve()
        if not target.is_relative_to(web.resolve()) or any(x.startswith(".") for x in relative.parts) or target.suffix.lower() not in {".html", ".js", ".css", ".svg", ".png", ".webp", ".ico", ".woff2", ".json"} or not target.is_file():
            raise KeyError(path)
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in {".html", ".js", ".css", ".json", ".svg"}:
            kind += "; charset=utf-8"
        self.send_bytes(200, target.read_bytes(), kind)

    def handle_safe(self):
        try:
            self.dispatch()
        except KeyError:
            self.respond({"error": "内容不存在或已移入回收站。", "code": "NOT_FOUND"}, 404)
        except ProviderError as error:
            self.respond({"error": str(error), "code": "AI_UNAVAILABLE"}, 502)
        except ValueError as error:
            self.respond({"error": str(error), "code": "INVALID_INPUT"}, 400)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception:
            self.respond({"error": "请求处理失败，未输出内部信息。请检查本地资料并重试。", "code": "INTERNAL_ERROR"}, 500)

    do_GET = handle_safe
    do_HEAD = handle_safe
    do_POST = handle_safe
    do_PATCH = handle_safe
    do_DELETE = handle_safe

    def do_OPTIONS(self):
        self.respond({"error": "不开放跨源访问。"}, 403)


def make_server(port=8768, data_dir=None, provider=None, root=ROOT):
    return Server(("127.0.0.1", port), Application(data_dir or Path(root) / "data", provider, root))


def main(argv=None):
    parser = argparse.ArgumentParser(description="CodeSprint Studio 本机学习站")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("端口范围为 1—65535")
    load_env(ROOT / ".env")
    try:
        server = make_server(args.port, args.data_dir)
    except OSError:
        parser.exit(1, "本地服务启动失败，端口可能占用。请用 --port 更换端口；不要关闭无关进程。\n")
    url = "http://127.0.0.1:" + str(args.port)
    print("CodeSprint Studio 已启动：" + url + "（按 Ctrl+C 停止）", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
