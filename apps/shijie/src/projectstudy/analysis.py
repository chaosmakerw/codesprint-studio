"""Bounded, read-only project/material collection; Java discovery is an optional pilot."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath

MAX_FILES = 640
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
LIMITS = {"max_files": MAX_FILES, "max_file_bytes": MAX_FILE_BYTES,
          "max_total_bytes": MAX_TOTAL_BYTES}
SKIP_DIRS = {".git", ".idea", ".gradle", ".mvn", "target", "build", "dist",
             "node_modules", "data", "__pycache__", ".venv", "venv", "vendor", "bin", "obj", "coverage"}
TEXT_SUFFIXES = {".java", ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs",
                 ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".cs", ".sql", ".html", ".htm",
                 ".css", ".scss", ".md", ".txt"}
SENSITIVE = re.compile(r"(?:^|[./_-])(?:secret|secrets|credential|credentials|private|token|key)(?:[./_-]|$)|^\.env|^application.*\.(?:yml|yaml|properties)$", re.I)
SECRET_TEXT = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}|"
    r"(?im:^\s*(?:[\w.\-]*(?:password|secret|api[_-]?key|access[_-]?token))\s*[:=]\s*[\"']?(?!\$\{|example\b|dummy\b|changeme\b|test\b|\"\"|''|$)[^\s\"']{4,})|"
    r"(?i:\b(?:password|secret|api[_-]?key|access[_-]?token)\s*=\s*[\"'](?!\$\{|example\b|dummy\b|changeme\b|test\b|fake\b)[^\"'\r\n]{4,}[\"'])"
    r"|(?i:\b(?:password|api[_-]?key|access[_-]?token)[\"']?\s*[:=]\s*[\"']?(?!\$\{|example\b|dummy\b|changeme\b|test\b|fake\b)[A-Za-z0-9_./+-]{6,})"
)


class InputError(ValueError):
    """A public, path-free validation failure."""


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 300 or "\\" in value or ":" in value or "\x00" in value:
        raise InputError("文件路径必须是安全的相对路径。")
    p = PurePosixPath(value)
    if p.is_absolute() or any(part in {".", "..", ""} for part in value.split("/")):
        raise InputError("文件路径必须是安全的相对路径。")
    return p.as_posix()


def eligible(path: str) -> bool:
    p = PurePosixPath(path)
    if any(part.lower() in SKIP_DIRS | {"secrets", "private", "credentials", "keys"} or part.startswith(".") for part in p.parts[:-1]):
        return False
    if SENSITIVE.search(p.name) or p.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}:
        return False
    # Build descriptors remain readable for compatibility; generated build trees,
    # binaries, configuration and secrets are excluded before this allowlist.
    return p.suffix.lower() in TEXT_SUFFIXES or p.name in {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}


def collect_upload(items: list) -> tuple[list[dict], dict]:
    if not isinstance(items, list) or not items or len(items) > MAX_FILES * 4:
        raise InputError("请提供有效的文本文件列表，单次最多 2560 个候选文件。")
    result, seen, total, skipped = [], set(), 0, 0
    for item in items:
        if not isinstance(item, dict) or set(item) - {"path", "text"}:
            raise InputError("文件格式应包含 path 和 text。")
        path = relative_path(item.get("path"))
        if path in seen:
            raise InputError("文件相对路径不能重复。")
        seen.add(path)
        if not eligible(path):
            skipped += 1
            continue
        content = item.get("text")
        if not isinstance(content, str) or "\x00" in content:
            raise InputError("只支持 UTF-8 文本源码。")
        try:
            size = len(content.encode("utf-8"))
        except UnicodeError:
            raise InputError("只支持有效的 UTF-8 文本源码或资料。") from None
        if size > MAX_FILE_BYTES:
            skipped += 1
            continue
        if SECRET_TEXT.search(content):
            skipped += 1
            continue
        if len(result) >= MAX_FILES or total + size > MAX_TOTAL_BYTES:
            raise InputError("项目超过扫描上限，请选择更小的源码目录。")
        total += size
        result.append({"path": path, "text": content,
                       "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()})
    if not result:
        raise InputError("没有可分析的源码。配置、密钥和构建产物会自动排除。")
    return sorted(result, key=lambda f: f["path"]), {"included": len(result), "skipped": skipped, "bytes": total}


def scan_directory(raw_path: str) -> tuple[list[dict], dict, str]:
    if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > 2000:
        raise InputError("请输入项目文件夹。")
    root = Path(raw_path).expanduser()
    try:
        if root.is_symlink() or root.is_junction() or not root.is_dir():
            raise InputError("项目文件夹不可读取。")
        root = root.resolve(strict=True)
        items, skipped, candidates = [], 0, 0
        # Manual traversal bounds candidate discovery and never follows symlinks.
        todo, visited, seen_entries = [root], 0, 0
        while todo:
            visited += 1
            if visited > 4096:
                raise InputError("项目目录过多，请选择更小的源码目录。")
            directory = todo.pop()
            for p in sorted(directory.iterdir(), key=lambda x: x.name.lower()):
                seen_entries += 1
                if seen_entries > 20000:
                    raise InputError("项目文件过多，请选择更小的源码目录。")
                if p.is_symlink() or p.is_junction():
                    skipped += 1
                    continue
                if p.is_dir():
                    if p.name.lower() not in SKIP_DIRS and not p.name.startswith("."):
                        todo.append(p)
                    continue
                if not p.is_file():
                    continue
                path = p.relative_to(root).as_posix()
                if not eligible(path):
                    continue
                candidates += 1
                if candidates > MAX_FILES * 4:
                    raise InputError("候选源码过多，请选择更小的源码目录。")
                if p.stat().st_size > MAX_FILE_BYTES:
                    skipped += 1
                    continue
                try:
                    data = p.read_bytes()
                    if len(data) > MAX_FILE_BYTES:
                        skipped += 1
                        continue
                    content = data.decode("utf-8-sig")
                except (UnicodeError, OSError):
                    skipped += 1
                    continue
                items.append({"path": path, "text": content})
        files, report = collect_upload(items)
        report["skipped"] += skipped
        return files, report, str(root)
    except InputError:
        raise
    except (OSError, RuntimeError):
        raise InputError("项目文件夹不可读取。") from None


# Each detection is an implementation clue, never proof of user mastery or runtime correctness.
FEATURES = [
    ("java-types", "类型与数值边界", "Java 基础", r"\b(?:Map|List|Optional|Long|Integer|Number|BigDecimal|BigInteger|record|enum|instanceof|intValueExact|longValueExact|intValue|longValue)\b", "变量有声明类型和运行时类型，转换还受整数范围与精度限制。", "防止参数转换时截断、溢出或把错误类型送入业务层。", []),
    ("control-flow", "分支与执行顺序", "Java 基础", r"\b(?:if|switch|for|while|return|throw)\b", "分支、提前返回、异常和循环共同决定哪些代码会实际执行。", "在阅读调用链前先预测停止位置和状态变化。", ["java-types"]),
    ("interfaces-di", "接口与依赖注入", "后端工程", r"\binterface\b|implements\b|@(?:Component|Service|Bean|Autowired)|private\s+final\s+\w+", "调用方依赖接口，运行时由具体实现承担职责；构造器注入显式传递依赖。", "把业务与基础设施隔开，才能替换实现并隔离测试。", ["java-types"]),
    ("http-validation", "HTTP 与输入校验", "后端工程", r"@(?:RestController|RequestMapping|PostMapping|GetMapping|RequestBody|Valid)|\bHttpClient\b", "HTTP 输入经过解析、类型映射与校验后才进入业务处理。", "错误输入应在边界处拒绝，避免污染后续服务。", ["control-flow"]),
    ("identity-permission", "可信身份与数据权限", "安全边界", r"CurrentUser|AuthenticatedPrincipal|\b(?:currentUserId|currentUserContext|getUserId|userId|buyerId|sellerId|buyer_id|seller_id|ownerId|principal|Authentication|SecurityContext|listMyOrders|list_my_orders)\b|\bprovider\.current\(", "身份来源必须可信，对象权限还需结合资源归属检查。", "模型和客户端的参数不能代替服务器确认的访问身份。", ["control-flow", "interfaces-di"]),
    ("collections-state", "集合与可变状态", "Java 基础", r"\b(?:HashMap|ArrayList|ConcurrentHashMap|CopyOnWriteArrayList|List\.copyOf|Map\.copyOf)\b", "集合持有对象引用，复制、共享、修改和线程访问会改变可观察状态。", "防止返回内部可变集合造成意外写入，理解隔离和并发边界。", ["java-types"]),
    ("concurrency", "并发与任务控制", "后端工程", r"\b(?:ExecutorService|CompletableFuture|Semaphore|synchronized|volatile|AtomicInteger|Thread|ConcurrentHashMap)\b", "线程间的原子性、可见性、顺序性和任务生命周期分别需要保证。", "线程安全容器不能自动保证一整段业务操作的原子性。", ["collections-state"]),
    ("database-transaction", "持久化与事务", "后端工程", r"\b(?:JdbcTemplate|PreparedStatement|Connection|EntityManager)\b|@(?:Transactional|Mapper|Entity)|\b(?:SELECT|INSERT|UPDATE|DELETE)\b", "持久化调用受事务、参数绑定、隔离级别和资源释放约束。", "区分内存模拟与真实数据库，避免把单条语句成功等同于整个业务提交。", ["interfaces-di"]),
    ("timeouts-reliability", "超时与失败边界", "后端工程", r"\b(?:timeout|TimeoutException|Duration|retry|CircuitBreaker|Fallback|maxRounds|deadline)\b", "总时间预算、单次超时、重试与降级共同决定失败如何终止。", "重试会增加负载，必须考虑幂等性与预算。", ["control-flow"]),
    ("tool-calling", "工具分发与安全调用", "Java 与 Agent", r"ToolDispatcher|ToolArgumentValidator|ToolArgumentConverter|ToolResult|tool_calls|ToolCalling|\bvalidator\.validate\(|\bconverter\.to\w+\(", "模型工具名和原始参数必须经白名单、校验与安全转换才进入业务服务。", "普通 Java 方法存在，不代表真实模型已选择并调用该工具。", ["identity-permission", "java-types"]),
    ("rag-citations", "检索与引用边界", "Java 与 Agent", r"\b(?:Embedding\w*|TextEmbeddingModel|Retrieval\w*|RetrievedChunk|Rag\w*|RAG|Chunk\w*|Citation\w*|rerank|reranker|embedding|embeddingModel|vectorSearchPort|chunkId|citations)\b", "文档处理、检索与答案引用是不同阶段，可信引用来自实际召回片段。", "避免模型伪造来源，并在证据不足时拒答。", ["collections-state", "interfaces-di"]),
    ("tests-evidence", "测试与证据范围", "工程验证", r"@(?:Test|SpringBootTest|WebMvcTest)|\b(?:Mockito|MockMvc|assertEquals|assertThrows|verifyNoInteractions)\b", "被测对象、替身、容器与真实依赖决定测试可以证明的范围。", "Mock 返回正确不代表真实依赖组合正确，测试通过也不代表学习者能独立复现。", ["interfaces-di", "control-flow"]),
    ("observability", "日志与可观测性", "工程验证", r"\b(?:MeterRegistry|Counter|Timer|MDC|Logger|Trace|traceId|Micrometer)\b", "日志、指标和追踪分别提供事件、聚合状态和调用链证据。", "安全诊断需保留根因信号，同时避免泄露正文和凭据。", ["timeouts-reliability"]),
    ("redis-messaging", "缓存与异步消息", "后端工程", r"\b(?:RedisTemplate|StringRedisTemplate|KafkaTemplate|KafkaListener|Redis|Kafka|Rabbit)\b", "缓存需要容量、TTL 和一致性策略；消息需要重复消费、确认与幂等边界。", "异步链路与缓存能够改善性能，也引入故障与多实例一致性问题。", ["concurrency", "database-transaction"]),
]


def _evidence_score(cid: str, path: str, line: str) -> int:
    """Rank real operations ahead of imports without claiming semantic correctness."""
    stripped = line.strip()
    is_test = "/test/" in "/" + path.lower() or Path(path).stem.endswith("Test")
    score = 15 if is_test else 70
    if "/demo/" in "/" + path.lower() or "HealthConfiguration" in Path(path).stem:
        score -= 30
    if stripped.startswith(("import ", "package ")):
        return score - 180
    if stripped.startswith("@"):
        score += 8
    if re.search(r"\b(?:return|throw|if|while|for|try|switch)\b", line):
        score += 24
    if re.search(r"\b\w+\.\w+\s*\(", line):
        score += 26
    if "=" in line and "==" not in line:
        score += 12
    if re.search(r"\b(?:class|interface|record|enum)\b", line):
        score += 8
    # Concept-specific patterns prefer the point at which behavior actually changes.
    patterns = {
        "java-types": r"\b(?:instanceof|intValueExact|longValueExact|intValue|longValue)\b|\b(?:Map|List|Optional|Long|Integer|Number|BigDecimal)\s*(?:<[^;]+>)?\s+\w+\s*=|\b(?:record|enum)\b",
        "control-flow": r"\b(?:if|while|for|switch)\s*\(|\b(?:return|throw)\b",
        "interfaces-di": r"this\.\w+\s*=|\b(?:implements|interface)\b|@Bean\b",
        "http-validation": r"@(?:RequestBody|Valid|PostMapping|GetMapping)|\bHttpClient\.(?:newHttpClient|newBuilder)\b",
        "collections-state": r"new\s+(?:HashMap|ArrayList|ConcurrentHashMap)|(?:List|Map)\.copyOf\(|\.computeIfAbsent\(",
        "concurrency": r"\b(?:synchronized|volatile)\b|(?:CompletableFuture|Thread)\.\w+\(|new\s+(?:Semaphore|AtomicInteger|ConcurrentHashMap)",
        "database-transaction": r"\b(?:SELECT|INSERT|UPDATE|DELETE)\b|\.prepareStatement\(|\.getConnection\(|@Transactional\b",
        "timeouts-reliability": r"\b(?:if|throw|return)\b.*(?:timeout|deadline|maxRounds)|\.timeout\(|Duration\.of|\.retry\(",
        "tool-calling": r"\b(?:validator|converter|dispatcher)\.\w+\(|ToolResult\.(?:failure|success)\(|\bToolArgument(?:Validator|Converter)\b.*new",
        "rag-citations": r"\b(?:return|if|for)\b.*(?:chunkId|citations|embedding|RetrievalResult)|\bembeddingModel\.embed\(|\bvectorSearchPort\.search\(|\.retrieve\(|\.rerank\(|new\s+(?:Citation|Chunk)",
        "tests-evidence": r"\b(?:assertEquals|assertThrows|verifyNoInteractions|verify)\s*\(",
        "observability": r"MDC\.(?:put|remove|get|clear)\(|\b(?:Counter|Timer)\.builder\(|\.increment\(|\.record\(",
        "redis-messaging": r"(?:RedisTemplate|StringRedisTemplate|KafkaTemplate)\s*<|@KafkaListener\b|\b(?:redis|redisTemplate|kafkaTemplate)\.\w+\(",
    }
    if cid == "identity-permission":
        if re.search(r"\b(?:currentUserContext|userContext)\.(?:getUserId|isAdmin)\(|\bprovider\.current\(", line):
            score += 90
        elif re.search(r"\bWHERE\s+(?:buyer_id|seller_id|user_id|owner_id)\s*=|(?:currentUserId|userId|buyerId|sellerId)\.equals\(|\.equals\([^)]*(?:buyerId|sellerId|userId)\(", line):
            score += 100
        elif re.search(r"\bif\s*\([^)]*(?:currentUserId|userId|buyerId|sellerId|ownerId)\b", line):
            score += 70
        elif re.search(r"\.map\(AuthenticatedPrincipal::(?:userId|admin)\)", line):
            score += 65
    elif re.search(patterns.get(cid, r"(?!x)x"), line, re.I if cid == "timeouts-reliability" else 0):
        score += 55
    # Test concept itself is best explained by an assertion; for all other concepts
    # a test is supporting evidence rather than the primary implementation anchor.
    if cid == "tests-evidence" and is_test:
        score += 50
    return score


def analyze(files: list[dict]) -> list[dict]:
    result = []
    for cid, title, group, pattern, explanation, why, prerequisites in FEATURES:
        regex = re.compile(pattern, re.I if cid == "timeouts-reliability" else 0)
        candidates = []
        for f in files:
            if not f["path"].endswith(".java"):
                continue
            best = None
            for n, line in enumerate(f["text"].splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("//", "*", "/*")):
                    continue
                if regex.search(line):
                    score = _evidence_score(cid, f["path"], line)
                    if best is None or score > best[0]:
                        best = (score, {"path": f["path"], "line": n,
                                        "quote": line[:400], "sha256": f["sha256"]})
            if best is not None:
                candidates.append(best)
        candidates.sort(key=lambda candidate: (-candidate[0], candidate[1]["path"], candidate[1]["line"]))
        evidence = [candidate[1] for candidate in candidates[:8]]
        if evidence:
            result.append({"id": cid, "title": title, "group": group,
                           "explanation": explanation, "why": why,
                           "prerequisites": prerequisites, "evidence": evidence,
                           "detection": "static_candidate",
                           "limitations": "静态标记只说明相关代码出现，不证明真实接入、正确运行或学习者独立掌握。"})
    return result


def source_hash(files: list[dict]) -> str:
    joined = "\n".join(f["path"] + ":" + f["sha256"] for f in files)
    return hashlib.sha256(joined.encode()).hexdigest()
