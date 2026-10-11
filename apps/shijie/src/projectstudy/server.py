"""Loopback-only HTTP application. No code execution and no external model requests."""
from __future__ import annotations

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
import urllib.request
import webbrowser

from . import __version__
from .analysis import InputError, LIMITS
from .contracts import SUMMARY_FIELDS, flow_contract
from .store import MAX_BACKUP_BYTES, MAX_BODY_BYTES, MAX_PACK_BYTES, MAX_BUNDLE_BYTES, Store, dumps

ROOT = Path(__file__).resolve().parents[2]
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/offline-store.js": ("offline-store.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/journey.css": ("journey.css", "text/css; charset=utf-8"),
          "/icon.svg": ("icon.svg", "image/svg+xml")}


def _read_instance_id(data_dir):
    """Keyed local identity; never publish the path, key or an unkeyed path hash."""
    try:
        value = (Path(data_dir) / ".instance-id").read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        raise InputError("本机实例标识无法读取，请检查数据目录权限。") from None
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise InputError("本机实例标识损坏，请先备份资料并检查数据目录。")
    canonical = os.path.normcase(str(Path(data_dir).resolve())).encode("utf-8")
    return hmac.new(bytes.fromhex(value), canonical, "sha256").hexdigest()


def _ensure_instance_id(data_dir):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    existing = _read_instance_id(data_dir)
    if existing:
        return existing
    value = secrets.token_hex(32)
    try:
        with (data_dir / ".instance-id").open("x", encoding="ascii") as stream:
            stream.write(value)
    except FileExistsError:
        return _read_instance_id(data_dir)
    return _read_instance_id(data_dir)


def _running_instance(url):
    """Bounded loopback probe, bypassing any configured HTTP proxy."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/api/health", timeout=.75) as response:
            raw = response.read(4097)
        if len(raw) > 4096:
            return None
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def _try_open_application(url, data_dir):
    try:
        open_application(url, data_dir)
    except (OSError, webbrowser.Error):
        print(f"浏览器未能自动打开，本机服务仍在运行。请手动访问：{url}", flush=True)


class Application:
    def __init__(self, root: Path, data_dir: Path, curriculum_builder=None):
        self.root = Path(root)
        self.store = Store(data_dir, curriculum_builder)
        self.instance_id = _ensure_instance_id(data_dir)
        self.token = secrets.token_urlsafe(32)

    def route(self, method, path, query, body):
        parts = path.strip("/").split("/")
        if method == "GET" and path == "/api/health":
            return {"ok": True, "app": "project-study", "version": __version__, "instance_id": self.instance_id}
        if method == "GET" and path == "/api/agent-manifest":
            return {"app": "project-study", "name": "拾阶", "version": __version__, "schema_version": 2,
                    "workflow": ["project_summary", "functional_call_chains", "periodic_reflection", "choice_review"],
                    "prompts": {"onboarding": "/api/coach-prompt?mode=onboarding", "learning": "/api/coach-prompt?mode=learning", "reference": "/api/coach-prompt?mode=reference"},
                    "bootstrap": "/api/bootstrap", "write_token": {"from": "bootstrap.token", "header": "X-Study-Token"},
                    "intake": {"preview": "POST /api/bundles/preview", "body": {"bundle": "schema_version=2 学习包"},
                               "commit": "POST /api/bundles/{draft_id}/commit", "commit_body": {"confirmed": True}},
                    "project": {"detail": "GET /api/projects/{project_id}", "handoff": "GET /api/projects/{project_id}/handoff",
                                "preview": "POST /api/projects/{project_id}/packs/preview",
                                "commit": "POST /api/projects/{project_id}/packs/{draft_id}/commit"},
                    "authorization": "用户明确授权 Agent 导入整理后的项目内容时，Agent 可自行校验 preview 并以 confirmed=true 写入；没有用户授权不得导入或替换数据。",
                    "security": "仅限本机 loopback，同源与写入令牌校验；仅保存声明式 JSON 与文本，不执行上传项目，不运行包内代码，不自动请求互联网或模型。",
                    "contract": {"project_summary": sorted(SUMMARY_FIELDS), "flows": flow_contract(),
                                 "concept_updates": {"schema_version": 2, "format": "完整现存知识点对象",
                                                     "mutable_fields": ["lesson", "mechanism", "failures", "references"],
                                                     "rule": "仅追加四个内容字段，保留原标识、证据与其他全部字段；预览后发生修改则拒绝提交。"}},
                    "limits": {**LIMITS, "max_bundle_bytes": MAX_BUNDLE_BYTES, "max_pack_bytes": MAX_PACK_BYTES}}
        if method == "GET" and path == "/api/bootstrap":
            return {"token": self.token, "projects": self.store.list_projects(),
                    "limits": {**LIMITS, "max_backup_bytes": MAX_BACKUP_BYTES, "max_pack_bytes": MAX_PACK_BYTES, "max_bundle_bytes": MAX_BUNDLE_BYTES},
                    "demo_available": (self.root / "examples" / "java-demo").is_dir(),
                    "version": __version__}
        if method == "GET" and path == "/api/backup":
            return self.store.backup()
        if method == "GET" and path == "/api/coach-prompt":
            mode = query.get("mode", ["learning"])[0]
            if mode not in {"learning", "onboarding", "reference"}:
                raise InputError("提示词模式应为 learning、onboarding 或 reference。")
            filename = {"onboarding": "first-run.md", "learning": "project-coach.md", "reference": "reference.md"}[mode]
            try:
                return {"prompt": (self.root / "prompts" / filename).read_text(encoding="utf-8")}
            except OSError:
                raise InputError("Agent 提示词文件尚未就绪。") from None
        if method == "POST" and path == "/api/restore":
            return self.store.restore(body.get("backup"), body.get("confirmed"))
        if method == "POST" and path == "/api/projects/scan":
            return self.store.scan(body.get("path"), body.get("name"), body.get("project_id"))
        if method == "POST" and path == "/api/projects/upload":
            return self.store.upload(body.get("files"), body.get("name"), body.get("project_id"))
        if method == "POST" and path == "/api/projects/demo":
            demo_root = self.root / "examples" / "java-demo"
            bundle_path = demo_root / "learning-bundle.json"
            if bundle_path.is_file():
                try:
                    if bundle_path.stat().st_size > MAX_BUNDLE_BYTES:
                        raise InputError("示例学习包超过安全容量上限。")
                    bundle = json.loads(bundle_path.read_text(encoding="utf-8-sig"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    raise InputError("示例学习包无法读取，请检查安装文件。") from None
                return self.store.import_builtin_bundle(bundle, "java-demo")
            return self.store.scan(str(demo_root), "我的订单示例")
        if method == "POST" and path == "/api/bundles/preview":
            return self.store.preview_bundle(body.get("bundle"))
        if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "bundles"] and parts[3] == "commit":
            return self.store.commit_bundle(parts[2], body.get("confirmed"))
        if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
            pid = parts[2]
            if len(parts) == 3:
                if method == "GET":
                    return self.store.project_detail(pid)
                if method == "DELETE":
                    return self.store.delete_project(pid, query.get("confirmed") == ["true"])
            if len(parts) == 4:
                feature = parts[3]
                if method == "GET" and feature == "source":
                    return self.store.source(pid, query.get("path", [None])[0])
                if method == "GET" and feature == "handoff":
                    return self.store.handoff(pid)
                if method == "POST" and feature == "attempts":
                    return self.store.submit(pid, body.get("activity_id"), body.get("answer"), body.get("assistance", "none"))
                if method == "POST" and feature == "hints":
                    return self.store.hint(pid, body.get("activity_id"), body.get("level"))
                if method == "POST" and feature == "evidence":
                    return self.store.submit_evidence(pid, body)
                if method == "POST" and feature == "materials":
                    return self.store.add_materials(pid, body.get("files"))
                if method == "GET" and feature == "checks":
                    return self.store.checks(pid)
                if method == "GET" and feature == "reviews":
                    return self.store.reviews(pid)
                if method == "POST" and feature == "reviews":
                    return self.store.submit_review(pid, body.get("answers"), body.get("assistance", "none"))
            if len(parts) == 5:
                if method == "GET" and parts[3] == "activities":
                    return self.store.activity_detail(pid, parts[4])
                if method == "POST" and parts[3:] == ["packs", "preview"]:
                    return self.store.preview_pack(pid, body.get("pack"))
                if method == "POST" and parts[3:] == ["checks", "start"]:
                    return self.store.start_check(pid, body.get("count", 5), body.get("concept_id"), body.get("scope", "all"), body.get("mode", "exam"))
                if method == "GET" and parts[3:] == ["checks", "inventory"]:
                    return self.store.quiz_inventory(pid)
                if method == "GET" and parts[3] == "checks":
                    return self.store.check_detail(pid, parts[4])
                if method == "POST" and parts[3:] == ["reviews", "settings"]:
                    return self.store.set_review_interval(pid, body.get("interval_days"))
            if len(parts) == 6 and method == "POST" and parts[3] == "packs" and parts[5] == "commit":
                return self.store.commit_pack(pid, parts[4], body.get("confirmed"))
            if len(parts) == 6 and method == "POST" and parts[3] == "checks" and parts[5] == "submit":
                return self.store.submit_check(pid, parts[4], body.get("answers"))
            if len(parts) == 6 and method == "POST" and parts[3] == "checks" and parts[5] == "answer":
                return self.store.answer_check(pid, parts[4], body.get("activity_id"), body.get("answer"), body.get("guessed", False))
        raise RouteMissing()


class RouteMissing(Exception):
    pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # SO_REUSEADDR on Windows can allow a second process to steal a live port.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, Handler)
        port = self.server_address[1]
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{host}" for host in self.hosts}


class Handler(BaseHTTPRequestHandler):
    server_version = "ProjectStudy"
    sys_version = ""

    def log_message(self, *_):
        # Request URLs and submitted project paths never enter a request log.
        pass

    def _response(self, status, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _trusted(self, mutation):
        if self.headers.get("Host") not in self.server.hosts:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.origins:
            return False
        if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
            return False
        if mutation:
            token = self.headers.get("X-Study-Token", "")
            if not hmac.compare_digest(token, self.server.app.token):
                return False
        return True

    def _handle(self):
        mutation = self.command in {"POST", "DELETE"}
        if not self._trusted(mutation):
            self._response(403, {"error": "请求来源或本机操作令牌无效。"})
            return
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc or len(self.path) > 4000:
            self._response(400, {"error": "请求地址无效。"})
            return
        if self.command == "GET" and parsed.path in STATIC:
            name, mime = STATIC[parsed.path]
            try:
                self._response(200, (self.server.app.root / "web" / name).read_bytes(), mime)
            except OSError:
                self._response(503, {"error": "前端文件尚未就绪。"})
            return
        body = {}
        if self.command == "POST":
            try:
                if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    raise InputError("请求必须使用 JSON。")
                size = int(self.headers.get("Content-Length", "-1"))
                if not 0 < size <= MAX_BODY_BYTES:
                    raise InputError("请求容量超过上限。")
                body = json.loads(self.rfile.read(size).decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(body, dict):
                    raise InputError("请求应为 JSON 对象。")
            except (ValueError, UnicodeError, RecursionError):
                self._response(400, {"error": "JSON 内容或请求容量无效。"})
                return
        try:
            result = self.server.app.route(self.command, parsed.path, parse_qs(parsed.query), body)
            self._response(200, result)
        except InputError as exc:
            self._response(400, {"error": str(exc)})
        except RouteMissing:
            self._response(404, {"error": "页面或接口不存在。"})
        except Exception:
            self._response(500, {"error": "本机处理失败，数据未完成写入。请重新操作。"})

    do_GET = _handle
    do_POST = _handle
    do_DELETE = _handle

    def do_OPTIONS(self):
        self._response(403, {"error": "不允许跨站访问。"})


def make_server(port=8769, data_dir=None, root=ROOT, curriculum_builder=None):
    root = Path(root)
    app = Application(root, Path(data_dir) if data_dir else root / "data", curriculum_builder)
    return Server(("127.0.0.1", port), app)


def open_application(url, data_dir):
    if sys.platform == "win32":
        candidates = [Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
                      Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe"]
        for edge in candidates:
            if edge.is_file():
                profile = Path(data_dir) / "browser-profile"
                subprocess.Popen([str(edge), f"--app={url}", f"--user-data-dir={profile}", "--no-first-run"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return
    if not webbrowser.open(url):
        raise webbrowser.Error("No browser opened")


def main(argv=None):
    parser = argparse.ArgumentParser(description="拾阶本机学习应用")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--data-dir", "--data", dest="data", type=Path, default=ROOT / "data")
    parser.add_argument("--project", help="仅导入明确选中的项目文件夹")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("端口应为 1 至 65535。")
    url = f"http://127.0.0.1:{args.port}"
    server = None
    try:
        current = _running_instance(url)
        instance_id = _read_instance_id(args.data)
        if current:
            matched = current.get("ok") is True and current.get("app") == "project-study" and current.get("version") == __version__ and instance_id and current.get("instance_id") == instance_id
            if matched and not args.project:
                print(f"拾阶已在运行，继续使用：{url}\n关闭本机服务请回到原启动窗口按 Ctrl+C。", flush=True)
                if args.open:
                    _try_open_application(url, args.data)
                return 0
            if matched and args.project:
                print(f"拾阶已在运行：{url}。此次 --project 尚未导入；请在应用内通过 Agent 导入，或关闭原启动窗口后重新启动。", file=sys.stderr)
                return 1
            print(f"端口 {args.port} 已由其他程序、其他数据目录或不同版本占用；未复用该服务。\n当前地址：{url}\n请运行：START.cmd --port {args.port + 1 if args.port < 65535 else 8771}", file=sys.stderr)
            return 1
        server = make_server(args.port, args.data)
        if args.project:
            # Launching twice must not silently replace the learner's source version.
            server.app.store.import_selected_once(args.project)
    except InputError as exc:
        if server:
            server.server_close()
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        if server:
            server.server_close()
        if exc.errno in {98, 48, 10048} or getattr(exc, "winerror", None) == 10048:
            print(f"端口 {args.port} 已被占用，未关闭其他程序。\n当前地址：{url}\n请运行：START.cmd --port {args.port + 1 if args.port < 65535 else 8771}", file=sys.stderr)
        else:
            print("无法启动本机服务，请检查数据目录是否可写。", file=sys.stderr)
        return 1
    print(f"拾阶已启动：{url}\n按 Ctrl+C 关闭本机服务。", flush=True)
    if args.open:
        _try_open_application(url, args.data)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
