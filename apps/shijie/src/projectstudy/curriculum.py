"""Source-backed exercises; static clues are never a mastery certificate.

Only narrowly recognised order-tool implementations receive objective answers.
All other activities ask the learner to explain the selected source and require
human or Agent review against a visible rubric. No source code is executed.
"""
from __future__ import annotations

import hashlib
import re


# Two complementary activities per detected concept keep the initial set small.
# The exact file, line and excerpt are added below rather than inventing a project.
_TOPICS = {
    "java-types": (
        "追踪一个变量的类型和值", "trace",
        "选择引用处实际存在的一个变量，写出声明类型、赋值来源、运行时可能类型，以及下一次转换的位置。遇到 Map<String, Object> 时，明确 get 的声明返回类型与值的真实类型；遇到 Long 时，区分 null、拆箱和值比较。请使用源码里的变量名与一组具体输入。",
        ["使用实际变量名和声明类型", "按赋值顺序记录值或运行时类型", "指出一次类型转换的前提与失败方式"],
        "独立复现一个类型边界", "reproduce",
        "在回答区写一个最小 Java 片段，复现引用处的一种类型转换：包含合法值、错误类型和一个边界值。注明哪些语句会编译、哪些在运行时失败，并写出预期结果。不必重写业务，也不要直接修改原项目。",
        ["片段对应源码真实使用的类型", "区分编译期类型检查与运行时转换", "至少一个边界输入及预期结果"],
        ["先找到变量声明，再跟踪第一次赋值。", "列一张表：表达式、声明类型、具体值、下一步调用。不要把 Object 当成实际数据类型。", "表达形式可以是：变量 x 声明为某类型，来自表达式 y；输入 z 时运行时类型为某类型；转换必须先满足条件 c。这只是格式，变量和结论需由源码补全。"],
    ),
    "control-flow": (
        "预测一次提前返回", "predict",
        "在引用处附近选择一个实际 if、return 或 throw，给出能进入该分支的具体输入。先不执行，逐行预测返回值或异常，并列出因此没有执行的下一次调用。再给出一个只改变一个条件的成功输入。",
        ["输入能命中引用处真实分支", "停止位置及返回值或异常准确", "同时解释另一侧输入会继续执行什么"],
        "复现分支两侧", "reproduce",
        "在回答区写出该分支的最小复现或两个测试草案：一侧进入提前返回，另一侧继续到后续调用。保留原有条件含义，记录输入、预期输出和下游是否调用；若尚未运行测试，请明确标为预测。",
        ["两侧输入只改变关键条件", "提前返回后不调用下游", "区分测试草案与实际运行证据"],
        ["找到第一处改变流程的语句。", "只按源码顺序往下走，遇到 return 或 throw 就停止，不能跨过去讨论后面的权限检查。", "可以按『条件为真 → 返回 R → 下游 D 未调用；条件为假 → 下一行 N』作答，R、D、N 需要填写实际内容。"],
    ),
    "interfaces-di": (
        "跟踪依赖的真实实现", "trace",
        "选择引用处实际依赖的一个接口或构造器参数，列出调用方、声明类型和当前可能的实现。查找谁创建或装配它，区分手动 new、Spring Bean 与测试替身。缺少配置文件时，写出不能确定的部分，不猜当前 Profile。",
        ["接口和实现来自本次源码", "创建或注入位置有文件依据", "注明无法确认的运行配置和替身"],
        "替换实现而不改调用方", "reproduce",
        "在回答区写一个极小的替身实现和手动组装草案，用它复现一次成功和一次失败。指出调用方依赖接口的哪一个方法，以及替身未覆盖的真实 HTTP、数据库或模型行为。没有接口时，先解释当前类可替换的边界。",
        ["替身方法与真实接口签名一致", "调用方不依赖某一个具体供应商", "列出替身不能证明的外部行为"],
        ["先看字段类型和构造器签名。", "搜索 implements、@Bean 或测试里的 new 和 mock；注解存在不等于 Spring 已经启动。", "说明形式：A 的构造器接收接口 I；配置或测试提供实现 B；调用 A.m 时实际进入 B.n。没有装配证据时应标为待确认。"],
    ),
    "http-validation": (
        "跟踪请求进入 Java 的过程", "trace",
        "以引用处的实际 HTTP 方法、路径或请求对象为起点，写一组合法输入和非法输入。依次跟踪 JSON 映射、参数校验、可信身份和业务调用。只写本次源码能确认的阶段；没有控制器或映射配置时，不把 HTTP 客户端误认成服务入口。",
        ["入口方法和输入字段有源码依据", "说明输入在哪一层被转换或拒绝", "区分 HTTP 层与直接 Java 调用"],
        "定位一个请求失败", "debug",
        "假设该请求没有得到预期成功结果，请提出两个能区分的根因，并各写一种最小验证方法。例如解析或校验失败与业务失败需要不同证据。写出正常成功输入，不能只验证拒绝场景；不要实际发送包含凭据的请求。",
        ["假设可以被观测或测试推翻", "输入失败与业务失败分开验证", "保留正常请求可以通过的验收"],
        ["从参数类型和注解开始，不先猜数据库。", "记录请求体如何成为对象，以及调用 Service 之前是否存在校验和身份检查。", "可用『非法字段 → 边界拒绝 → Service 未调用；合法字段 → Service 被调用』组织测试，具体状态码要由当前异常映射核实。"],
    ),
    "identity-permission": (
        "跟踪身份如何决定数据范围", "trace",
        "从引用处实际出现的身份字段或方法开始，跟踪身份最早从哪里得到、由谁携带、在哪里与资源归属比较。为两个不同用户提供具体输入并预测各自可见范围。缺少认证实现时明确区分『已有上下文接口』与『已验证的真实认证』。",
        ["身份来源与模型或客户端字段分开", "对象归属校验位置准确", "不把管理员或已登录自动等同于可查看全部数据"],
        "复现成功与越权拒绝", "reproduce",
        "在回答区写一个最小权限复现或测试草案，覆盖本人的合法数据、另一个人的数据、未登录和伪造身份字段。注明哪个失败会先发生以及是否调用下游。若原代码暂未阻止某种访问，应指出缺口，不把建议当成已实现。",
        ["合法访问和拒绝访问两侧都有输入", "失败顺序由源码支撑", "失败后下游调用次数与边界准确"],
        ["找到身份第一次进入 Java 业务代码的位置。", "分别给模型参数、服务器上下文和数据里的归属 ID 命名；不要把三个来源合成一个 userId。", "回答可以用：服务器身份 A、资源属于 B，访问判定发生在 C。A 为空、A 等于 B、A 不等于 B 要分别解释。"],
    ),
    "collections-state": (
        "跟踪集合引用与修改", "trace",
        "选择引用处的一次集合创建、读取或返回，跟踪键、值、列表元素及引用是否共享。构造『调用方拿到结果后尝试修改』的具体情境，预测是否影响原状态或抛异常。若源码只返回副本，说明副本是浅拷贝还是元素本身也隔离。",
        ["使用实际集合和返回语句", "引用共享与内容相等区别准确", "解释空结果和非空结果的修改语义"],
        "复现状态隔离边界", "reproduce",
        "在回答区写一个最小片段或测试草案，先写入一个元素，读取结果后修改，再重新读取。记录预期值与失败方式，并增加两个不同键的情境。这里只验证集合语义，不宣称已经证明多线程或多实例安全。",
        ["两次读取之间的变化可验证", "不同键或用户不会被无意混合", "不把单线程测试当成并发证明"],
        ["关注 return 的对象是内部集合还是新集合。", "画两个引用箭头：内部字段到对象，调用方变量到对象；判断是否指向同一个可变对象。", "最小实验结构：append → load → 修改返回值 → 再次 load。结果必须根据真实返回策略判断，不能统一回答不可修改。"],
    ),
    "concurrency": (
        "预测两个任务交错", "predict",
        "选择引用处的一段共享状态或异步代码，写出两个任务交错的四到六个步骤。明确每一步读写哪个值，预测一个可能结果。分别说明可见性、原子性和任务结束由什么保证；仅有并发容器时，不默认整段业务操作原子。",
        ["交错步骤使用当前实际字段和操作", "原子性与可见性不混淆", "给出可以复现或反驳预测的控制方式"],
        "设计并发故障的最小实验", "debug",
        "为上一条交错设计一个最小并发测试草案：说明如何协调开始、等待完成、传播异常以及避免用 sleep 当唯一同步条件。写清正常场景、冲突场景和实验局限；没有实际执行就不提供性能数字。",
        ["开始与完成有确定的协调方案", "异常和资源关闭有处理", "结果只能证明所覆盖的并发情境"],
        ["先列共享字段，再找线程之间的协调方式。", "将『读取 → 计算 → 写入』拆开；即使单次 get 和 put 安全，组合操作也可能交错。", "可以用受控的屏障或闩锁让两个任务停在同一阶段，之后 await 所有结果；具体 API 必须适配当前代码，不凭空说已经使用。"],
    ),
    "database-transaction": (
        "跟踪 SQL 参数与资源", "trace",
        "从引用处实际 SQL 或数据库接口开始，跟踪参数来源、占位符绑定、返回行映射以及 Connection、Statement、ResultSet 的关闭顺序。说明本次资料能否确认事务边界；没有 @Transactional 不等于一定没有事务，也不等于多条写入原子。",
        ["参数编号与来源具体且准确", "返回字段映射有代码对应", "资源和事务边界分别说明"],
        "定位持久化失败与验证范围", "debug",
        "为这段持久化逻辑写出一个成功输入、一个边界输入和一个依赖失败情境。指出异常在哪里转换、下游是否继续，以及要用真实数据库、H2 还是 Mock 才能验证各项。不要把 MySQL 兼容模式等同于真实 MySQL 的锁与性能。",
        ["正常和失败均有明确预期", "错误传播或安全响应位置有依据", "数据库替身与生产行为的区别明确"],
        ["先看参数如何进入 SQL，而不是猜查询结果。", "逐项记录 setLong 或其他绑定、execute、映射和关闭；事务与 try-with-resources 是不同问题。", "说明可以是：替身验证调用参数，H2 验证此 SQL 与行映射，真实数据库实验再验证锁和性能。每项都要标清本次是否执行。"],
    ),
    "timeouts-reliability": (
        "手算时间预算与停止条件", "predict",
        "选择引用处实际的超时、轮次或重试条件，写出初始值和三步执行过程。给出一次正常完成和一次边界停止情境。区分调用前后检查预算与真正中断阻塞调用；源码没有重试或熔断时明确写尚未看到。",
        ["计数或时间变量初值准确", "边界判断与更新顺序准确", "预算检查不自动等于中断外部调用"],
        "设计一个可控失败实验", "debug",
        "用替身时钟或本地替身依赖设计一个超时或失败测试草案，写出输入、触发点、异常或返回结果和调用次数。解释为什么需要此边界，以及该实验不能证明的真实网络、供应商和生产行为。不要真的请求模型。",
        ["故障触发可控而非碰运气", "结果与调用次数可验证", "替身实验的证据范围准确"],
        ["先标出所有检查位置与计数更新位置。", "把外部调用视作可能阻塞的单独一步；前后检查预算不会凭空为其设置网络超时。", "时间表可以写为：t0 检查、t1 调用、t2 再检查。受控时钟决定 t2 越界，但真实阻塞如何终止还需适配器证据。"],
    ),
    "tool-calling": (
        "跟踪一组原始工具参数", "trace",
        "选引用处实际支持的工具名，给出一组合法原始参数。逐层跟踪参数 Map、校验、转换后的类型、工具门面、业务服务和稳定结果。确认是否有不需要参数转换的支路；没有协议注册证据时，不把 Dispatcher 能分发说成真实模型已调用。",
        ["工具名与允许字段来自源码", "原始类型与转换后类型明确", "Java 分发、协议注册和真实模型调用分开"],
        "设计失败后不调用下游的验证", "debug",
        "为同一工具设计错误类型、未知字段和依赖异常三个输入或故障。逐一预测最先失败的位置、稳定结果及下游调用情况，再提供合法成功对照。对于整数，说明小数、范围和精度如何处理，不能靠截断制造成功。",
        ["校验、转换和业务异常分层准确", "非法输入不绕过校验访问下游", "合法成功和安全失败同时验证"],
        ["首先确认工具白名单和字段白名单。", "列链路，但按实际分支删去未调用的层；空参数的工具未必经过 Converter。", "用一行一层记录：Map 中的值 → Validator 判定 → Converter 结果 → Service 入参 → ToolResult。未执行的层必须标为未调用。"],
    ),
    "rag-citations": (
        "追踪一条回答引用的来源", "trace",
        "以引用处实际出现的 chunk、检索结果或引用校验为起点，跟踪文档到片段 ID，再到本次召回与最终引用。列出哪些元数据由程序提供、哪些内容来自模型。若只看到一段实现，不把整条 RAG 链都视为已验证。",
        ["引用 ID 能回到本次实际检索片段", "程序可信元数据与模型文本分开", "资料不足与引用非法的处理有依据"],
        "复现缺失证据与伪造引用", "reproduce",
        "在回答区写一个最小测试草案，包含合法引用、无召回和模型给出未召回 ID 三种情况。说明预期拒答或拒绝发生在哪层，以及使用 Fake Embedding 或本地片段时不能证明真实向量检索与模型效果的原因。",
        ["正常引用与两个拒绝场景均覆盖", "未召回 ID 不能被接受为可信来源", "检索替身和真实模型效果边界准确"],
        ["先找到片段 ID 是在哪里产生和携带的。", "比较 retrieved IDs 与 output IDs；仅格式正确或数据库存在不表示本次引用可信。", "可以构造 retrieved={c1}，模型输出 c1、c2 和无结果三组；如何拒绝须由当前校验实现决定，不能凭空承诺。"],
    ),
    "tests-evidence": (
        "拆解一个测试能证明什么", "trace",
        "选择引用处的一个实际测试方法，指出被测对象、真实对象、Mock/Fake、是否启动 Spring、经过 HTTP、连接数据库和调用真实模型。按测试执行顺序解释设置、调用和断言。普通 verify 不证明调用顺序，必须检查是否使用 InOrder。",
        ["真实对象与替身逐项列清", "每个断言对应真实被验证的行为", "HTTP、Spring、数据库与模型边界准确"],
        "补足一个缺失的对照场景", "reproduce",
        "针对该测试选一个尚未覆盖的合法成功、非法输入或边界，写出最小新增测试草案和预期结果。解释它能发现哪种具体错误。若源码中已有同等测试，请选别的缺口；未运行时不写 BUILD SUCCESS。",
        ["新增场景有明确故障价值", "不重复已有同等断言", "区分草案、当前运行结果与历史报告"],
        ["从 new、mock 和测试注解判断依赖，而不是看方法名里是否有 integration。", "把 arrange、act、assert 分开，mock 的 thenReturn 是预设，不是验证外部依赖。", "证据表达可以是：真实 A 与 B 串联，C 是 Mock；断言覆盖拒绝后 C 未调用；不能证明真实 C 或生产全链路。"],
    ),
    "observability": (
        "跟踪一次请求的诊断信号", "trace",
        "选择引用处的一条实际日志、指标或追踪，说明它记录什么、何时记录、如何关联一次请求和在哪里清理上下文。列出能看见的失败信号与被刻意隐藏的敏感内容，不能把统一错误响应当成根因修复。",
        ["实际字段和记录位置有引用", "成功与失败都保留必要诊断信号", "正文、凭据和内部信息不泄露"],
        "设计可验证的安全诊断", "debug",
        "为一次依赖失败写出安全诊断实验草案：对外应看到什么、内部日志或指标应看到什么、绝不能出现什么。加入正常成功对照，说明是否需要真实监控系统；没有运行环境时不提供虚构 QPS 或 P99。",
        ["外部响应与内部信号职责区分", "泄露检查和成功对照均有设计", "指标定义与实际运行数据分开"],
        ["先看日志参数，不看自然语言描述。", "检查异常类型、结果码、延迟与 requestId 是否足够定位，同时检查 finally 是否清理线程上下文。", "草案可以对日志断言安全字段存在、敏感测试标记不存在，并对计数器断言一次增量；不能据此宣称生产告警已工作。"],
    ),
    "redis-messaging": (
        "跟踪缓存或消息的一次状态变化", "trace",
        "以引用处实际出现的 Redis 或消息 API 为起点，列出缓存键或 Topic、值结构、TTL 或确认方式以及失败后的状态。只讲当前源码确实使用的技术；文档提到 Kafka 不等于已实现 Producer 和 Consumer。",
        ["键或 Topic 及数据来源具体", "过期、确认或重复处理规则有依据", "已实现与尚未证明的机制分开"],
        "设计重复与失效的验证", "debug",
        "为该实际操作选择一个重复请求、消息重复消费、过期或依赖故障情境，写出最小测试草案与预期最终状态。说明单实例替身不能证明的多实例原子性或真实消息投递行为；未实现的 Lua、幂等或死信应明确标为缺口。",
        ["选择与源码相符的故障情境", "最终状态和重复影响可验证", "多实例与真实基础设施边界准确"],
        ["先确认当前是缓存还是异步消息，避免把所有词都套进来。", "列出同一数据处理两次的状态，以及过期或未确认后会如何处理。", "如果源码仅有 Redis set，则先验证键与过期策略；如果是消费处理，则验证重复投递与幂等。不要把希望拥有的机制写成已经存在。"],
    ),
}


def _evidence(files, concept):
    """Reject stale/invented evidence even when a caller supplies concepts."""
    index = {f["path"]: f for f in files}
    valid = []
    for e in concept.get("evidence", []):
        f = index.get(e.get("path"))
        if not f or f.get("sha256") != e.get("sha256"):
            continue
        n = e.get("line")
        lines = f["text"].splitlines()
        quote = e.get("quote")
        if isinstance(n, int) and 1 <= n <= len(lines) and isinstance(quote, str) and quote and quote in lines[n - 1]:
            valid.append(dict(e))
    # Imports are weak teaching anchors; prefer the implementation when present.
    valid.sort(key=lambda e: (e["quote"].strip().startswith(("import ", "package ")),
                              ("/test/" in e["path"]) != (concept["id"] == "tests-evidence")))
    return valid[:4]


def _anchor(files, evidence):
    e = evidence[0]
    f = next(f for f in files if f["path"] == e["path"])
    lines = f["text"].splitlines()
    start, end = max(0, e["line"] - 3), min(len(lines), e["line"] + 5)
    excerpt = "\n".join(f"{i + 1:>4}  {lines[i]}" for i in range(start, end))[:1800]
    return f"本次源码：{e['path']} 第 {e['line']} 行。\n\n```java\n{excerpt}\n```\n\n"


def _activity(cid, key, kind, title, prompt, rubric, explanation, hints, evidence,
              options=None, answer_ids=None, due=None):
    fingerprint = "\n".join(e["path"] + ":" + e["sha256"] for e in evidence)
    ident = f"{cid}-{key}-" + hashlib.sha256(fingerprint.encode()).hexdigest()[:10]
    item = {"id": ident, "concept_id": cid, "kind": kind, "title": title,
            "prompt": prompt, "options": options or [], "answer_ids": answer_ids or [],
            "rubric": rubric, "explanation": explanation, "hints": hints, "evidence": evidence}
    if due is not None:
        item["due_after_hours"] = due
    return item


def _masked(text):
    # Preserve offsets so braces inside comments/literals cannot close a method.
    pattern = r'"""[\s\S]*?"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/'
    return re.sub(pattern, lambda m: "".join("\n" if c == "\n" else " " for c in m[0]), text)


def _without_comments(text):
    pattern = r'"""[\s\S]*?"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/'
    return re.sub(pattern, lambda m: "".join("\n" if c == "\n" else " " for c in m[0])
                  if m[0].startswith("/") else m[0], text)


def _block(text, brace):
    masked, level = _masked(text), 0
    for i in range(brace, len(masked)):
        if masked[i] == "{":
            level += 1
        elif masked[i] == "}":
            level -= 1
            if level == 0:
                return text[brace + 1:i], brace + 1
    return None, None


def _method(file, name):
    match = re.search(r"\b(?:public|private|protected)\s+(?:static\s+)?[\w<>?, \[\].]+\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", _masked(file["text"]))
    if not match:
        return None
    return _block(file["text"], match.end() - 1)


def _named(files, name):
    matches = [f for f in files if f["path"].endswith("/" + name + ".java") or f["path"] == name + ".java"]
    matches = [f for f in matches if re.search(r"\bclass\s+" + name + r"\b", _masked(f["text"]))]
    return matches[0] if len(matches) == 1 else None


def _find_reference(file, pattern):
    match = re.search(pattern, _without_comments(file["text"]), re.S)
    if not match:
        return None
    line = file["text"].count("\n", 0, match.start()) + 1
    return {"path": file["path"], "line": line,
            "quote": file["text"].splitlines()[line - 1][:400], "sha256": file["sha256"]}


def _if_block(text, pattern):
    match = re.search(pattern + r"\s*\{", text, re.S)
    if not match:
        return None, None
    body, _ = _block(text, match.end() - 1)
    return body, match.start()


def _recognise_orders(files):
    """A conservative pattern, not a Java parser or a runtime security audit."""
    names = ("ToolDispatcher", "ToolArgumentValidator", "OrderTools", "FakeOrderService")
    fs = {name: _named(files, name) for name in names}
    if not all(fs.values()):
        return None
    found = {name: _method(fs[name], method) for name, method in zip(names, ("dispatch", "validate", "listMyOrders", "listMyOrders"))}
    if not all(found.values()):
        return None
    dispatch, validate, tools, fake = (_without_comments(found[n][0]) for n in names)
    early, early_pos = _if_block(dispatch, r'if\s*\(\s*!validation\.success\(\)\s*\)')
    order_branch, order_pos = _if_block(dispatch, r'if\s*\(\s*"list_my_orders"\.equals\(toolName\)\s*\)')
    validation_call = re.search(r'validator\.validate\(\s*toolName\s*,\s*arguments\s*\)', dispatch)
    rule, _ = _if_block(validate, r'if\s*\(\s*"list_my_orders"\.equals\(toolName\)\s*\)')
    if not all((early, order_branch, validation_call, rule)):
        return None
    null_args, _ = _if_block(rule, r'if\s*\(\s*arguments\s*==\s*null\s*\)')
    not_empty, _ = _if_block(rule, r'if\s*\(\s*!arguments\.isEmpty\(\)\s*\)')
    missing_id, _ = _if_block(tools, r'if\s*\(\s*currentUserId\s*==\s*null\s*\)')
    filter_branch, _ = _if_block(fake, r'if\s*\(\s*currentUserId\.equals\(order\.buyerId\(\)\)\s*\)')
    allowed_rule = re.compile(
        r'if\s*\(arguments\s*==\s*null\)\s*\{\s*return\s+ToolResult\.failure\(\s*"MISSING_ARGUMENT"\s*,\s*"[^"\\]*"\s*\)\s*;\s*\}'
        r'\s*if\s*\(!arguments\.isEmpty\(\)\)\s*\{\s*return\s+ToolResult\.failure\(\s*"UNKNOWN_ARGUMENT"\s*,\s*"[^"\\]*"\s*\)\s*;\s*\}'
        r'\s*return\s+ToolResult\.success\(null\)\s*;\s*', re.S)
    seed_field = re.search(r'private\s+final\s+List<OrderSummary>\s+orders\s*=\s*List\.of\((.*?)\)\s*;',
                           _without_comments(fs["FakeOrderService"]["text"]), re.S)
    checks = [
        validation_call.start() < early_pos < order_pos,
        "return" in early and "validation" in early,
        re.search(r'orderTools\.listMyOrders\(\)', order_branch) is not None,
        "converter." not in order_branch,
        bool(null_args and re.search(r'return\s+ToolResult\.failure\(\s*"MISSING_ARGUMENT"', null_args)),
        bool(not_empty and re.search(r'return\s+ToolResult\.failure\(\s*"UNKNOWN_ARGUMENT"', not_empty)),
        allowed_rule.fullmatch(rule.strip()) is not None,
        re.search(r'return\s+ToolResult\.success\(null\)', rule) is not None,
        re.search(r'Long\s+currentUserId\s*=\s*currentUserContext\.getUserId\(\)', tools) is not None,
        bool(missing_id and re.search(r'return\s+ToolResult\.failure\(\s*"UNAUTHORIZED"', missing_id)),
        re.search(r'orderService\.listMyOrders\(\s*currentUserId\s*\)', tools) is not None,
        re.search(r'return\s+ToolResult\.success\(orders\)', tools) is not None,
        "isAdmin" not in tools and "isAdmin" not in fake,
        re.search(r'for\s*\(\s*OrderSummary\s+order\s*:\s*orders\s*\)', fake) is not None,
        bool(filter_branch and re.search(r'result\.add\(order\)', filter_branch)),
        len(re.findall(r'result\.add\(', fake)) == 1,
        len(re.findall(r'\breturn\b', fake)) == 1,
        re.search(r'return\s+result\s*;', fake) is not None,
    ]
    seeds = re.findall(r'new\s+OrderSummary\(\s*(\d+)L\s*,\s*(\d+)L\s*,', seed_field[1]) if seed_field else []
    if not all(checks) or not seeds or len(seeds) > 30:
        return None
    ids = sorted({int(buyer) for _, buyer in seeds})
    if len(ids) < 2:
        return None
    evidence = [
        _find_reference(fs["ToolDispatcher"], r'validator\.validate\('),
        _find_reference(fs["ToolArgumentValidator"], r'if\s*\(\s*!arguments\.isEmpty\('),
        _find_reference(fs["OrderTools"], r'currentUserContext\.getUserId\('),
        _find_reference(fs["FakeOrderService"], r'currentUserId\.equals\(order\.buyerId\('),
    ]
    return {"files": fs, "evidence": evidence, "buyer": ids[0], "other": ids[1],
            "absent": max(ids) + 17, "count": sum(int(b) == ids[0] for _, b in seeds)}


def _order_activities(info):
    buyer, other, absent, count = (info[k] for k in ("buyer", "other", "absent", "count"))
    evidence = info["evidence"]
    cid = "identity-permission"
    intro = ("本题基于本次导入源码中已识别的订单工具分支，使用 FakeOrderService 的固定数据；"
             "不调用真实模型、不验证生产认证或数据库。若重新导入的源码版本变化，应重新作答。\n\n")
    why = "身份与对象权限必须同时成立；拒绝顺序决定下游是否会被调用。选择题判定只证明这一次预测，不证明能够独立实现。"
    return [
        _activity(cid, "order-predict", "predict", "预测三种订单请求的结果",
                  intro + f"A：服务器用户 {buyer}，参数 {{}}。\nB：未登录，参数 {{\"userId\":{buyer}}}。\nC：服务器管理员 {absent}，参数 {{}}；固定数据中没有其订单。\n\n哪一组结果符合当前实现？",
                  ["三种结果全部预测正确", "解释 B 的校验先于身份检查", "解释 C 的管理员身份不扩大本工具的数据范围"],
                  f"A 成功，返回 {buyer} 的 {count} 条订单。B 先被 Validator 判定为 UNKNOWN_ARGUMENT，未进入 OrderTools，不会调用 Service。C 成功返回空列表；这条『我的订单』链不读取管理员标记。{why}",
                  ["先看 Dispatcher 的第一步，再看哪个分支最先 return。", "B 有非空参数，会先命中不接受任何参数的校验；C 的服务只按 buyerId 过滤。", "三个情境的关键分别是：合法空对象、非空对象、已登录但无本人订单。按这三个条件回到源码核对。"], evidence,
                  options=[{"id": "a", "text": f"A 成功返回本人 {count} 条；B UNKNOWN_ARGUMENT；C 成功返回空列表"},
                           {"id": "b", "text": f"A 成功返回本人 {count} 条；B UNAUTHORIZED；C 成功返回空列表"},
                           {"id": "c", "text": f"A 成功返回本人 {count} 条；B UNKNOWN_ARGUMENT；C 返回全部订单"},
                           {"id": "d", "text": "A UNKNOWN_ARGUMENT；B UNAUTHORIZED；C 返回全部订单"}], answer_ids=["a"]),
        _activity(cid, "order-trace", "trace", "记录订单链的变量与停止位置",
                  intro + f"输入工具名 list_my_orders、空参数 Map，服务器身份为 {buyer}。请写一张执行表：方法、关键变量的声明类型与值、下一步。包括 validation、currentUserId、result 或 orders，并解释这条支路为什么没有调用 Converter。然后只将服务器身份改为 null，标出停止位置和未调用的层。",
                  ["Dispatcher → Validator → OrderTools → OrderService 的实际顺序", "Map<String,Object>、ToolResult<Void>、Long 和 List 的类型跟踪", "空参数支路不调用 Converter", "身份 null 时 UNAUTHORIZED 且不调用 Service"],
                  "合法空 Map 在 Validator 返回 success；Dispatcher 直接调用 OrderTools.listMyOrders，没有需要转换的模型业务参数。OrderTools 读取可信 Long 身份，再将其交给 Service；null 在 Tools 提前拒绝。为何这样设计：让工具不能从模型参数中选择查询对象，同时避免为无参数分支安排虚构转换步骤。口述需要人工或 Agent 核对，不能由关键词命中判独立掌握。",
                  ["先列进入每层时已有的变量，别把还没产生的变量写进去。", "Validator 的结果是 ToolResult<Void>；订单成功结果持有 List；currentUserId 来自上下文而非 Map。", "表格格式：Dispatcher 输入 → Validator 返回 → Tools 读取身份 → Service 筛选。身份 null 的表在 Tools 结束。请填实际类型和值。"], evidence),
        _activity(cid, "order-debug", "debug", "找出 Long 比较的隐患",
                  intro + "有人把 FakeOrderService 中 currentUserId.equals(order.buyerId()) 改成 currentUserId == order.buyerId()，两侧仍为 Long。哪个修复理由最准确？这里 currentUserId 已在进入过滤前检查非 null。",
                  ["Long 与 Long 的 == 是引用比较", "不能依赖包装类型缓存让权限判断碰巧成立", "说明 null 检查和 equals 的适用边界"],
                  "两侧都是 Long 时 == 比较引用，相同数值不保证相同对象。保留 equals 进行值比较；也可在明确空值策略下使用 Objects.equals。当前调用链先拒绝 null，因此 currentUserId.equals(...) 的接收者非空。为何这样设计：对象归属需要比较 ID 值，不能让权限由缓存或对象构造方式决定。此题不证明已排查所有数值精度问题。",
                  ["分别问『数值相同』与『两个变量指向同一对象』。", "Long 缓存可能让某些测试通过，但不会成为权限设计保证。", "本行比较的是用户编号的值；先确认接收者非空，再选择按值比较的方法。"], evidence,
                  options=[{"id": "a", "text": "改成字符串比较，所有数字类型会自然拥有相同语义"},
                           {"id": "b", "text": "保留 == 并只使用较小 ID，缓存会保证权限判断"},
                           {"id": "c", "text": "保留 equals 比较值，接收者的 null 必须先处理"},
                           {"id": "d", "text": "将 Long 转成 double 后比较，所有 long 值都能精确保留"}], answer_ids=["c"]),
        _activity(cid, "order-reproduce", "reproduce", "独立复现最小安全订单工具",
                  intro + "请在回答区独立写出一个最小 Java 实现草案，可自行简化结果类型：输入工具名与原始参数，先拒绝 list_my_orders 的 null 和非空 Map；从传入的可信身份上下文读 ID，未登录拒绝；服务只返回该 ID 的订单。至少给出合法本人、另一用户、未登录与伪造字段四组验证输入。不要修改原项目，不必搭建 Spring 或数据库。",
                  ["身份不从原始参数取得", "参数拒绝先于身份和业务调用", "空对象与 null 不混同", "未登录与伪造身份都不会调用 Service", "合法本人和另一用户各有成功对照", "不能用管理员身份绕过本人范围"],
                  "验收按各条安全条件分别核对，关键边界不能用平均 80% 抵消。为何这样设计：最小手写剥离框架装配，能够显示你是否真正理解提前返回、类型和身份传递。需提供实际片段及预期；若只提交草案标为待评阅，若运行则附测试命令、结果及真实/替身依赖。测试通过也不自动证明脱离提示独立复现。",
                  ["先写方法签名，把模型参数和可信身份作为两个不同来源。", "依次放置：工具名和参数校验 → 身份读取与 null 检查 → Service → 结果；拒绝后立即 return。", "可拆成 dispatch、listMyOrders、filter 三个小方法。示范仅给职责：dispatch 管顺序，Tools 取身份，Service 按归属筛选；实现和测试仍由你补全。"], evidence),
        _activity(cid, "order-transfer", "transfer", "延迟变式 检验能否迁移权限规则",
                  intro + f"变式：业务希望提供『查看所有订单』。服务器普通用户 {other} 在 list_my_orders 参数中加入 {{\"role\":\"ADMIN\"}}；服务器管理员 {absent} 仍调用 list_my_orders 和 {{}}。先预测现有代码两次调用的结果，再提出一个独立管理工具的最小边界设计。说明可信管理员判断在哪层、为什么不能只放宽原工具参数，并写出合法管理员与普通用户拒绝的验收草案。建议首次练习至少 48 小时后再做，不先看原答案。",
                  ["伪造 role 被 UNKNOWN_ARGUMENT 拒绝", "现有管理员的本人订单仍为空", "新增能力单独授权并从服务器取得角色", "管理成功和普通用户拒绝同时验证", "明确本次是否使用提示及与上次的间隔"],
                  "现有两个结果分别为 UNKNOWN_ARGUMENT 和成功空列表。管理能力应另设明确入口和可信权限判定，不能通过模型传入 role 或把『我的订单』扩大为全量查询。为何这样设计：旧调用方仍保有最小权限，新能力可独立测试与审计。设计题待人工或 Agent 评阅；延迟时间与独立性需要实际记录，不能仅因设置了 48 小时就视为迁移成功。",
                  ["先回答旧实现会怎样，再讨论新增需求。", "角色与身份都必须来自可信服务器上下文，模型只是提出工具调用。", "可以分别定义本人查询和管理查询的边界；为每个入口列允许人群、返回范围和失败后不调用下游条件。不要直接粘贴旧答案。"], evidence, due=48),
    ]


def _numeric_activity(files):
    f = _named(files, "ToolArgumentValidator")
    if not f:
        return None
    pair = _method(f, "isPositiveIntegerWithin")
    validation = _method(f, "validate")
    if not pair or not validation:
        return None
    body = re.sub(r"\s+", "", _without_comments(pair[0]))
    rules = _without_comments(validation[0])
    if not all(s in body for s in ("bigDecimal.toBigIntegerExact()", "valueinstanceofByte", "valueinstanceofShort", "valueinstanceofInteger", "valueinstanceofLong", "integer.signum()>0", "integer.compareTo(BigInteger.valueOf(maximum))<=0")):
        return None
    if not re.search(r'else\s*\{\s*return\s+false\s*;', pair[0]) or not re.search(r'isPositiveIntegerWithin\(productId,\s*Long\.MAX_VALUE\)', rules):
        return None
    e = [_find_reference(f, r'bigDecimal\.toBigIntegerExact\('),
         _find_reference(f, r'isPositiveIntegerWithin\(productId,\s*Long\.MAX_VALUE\)')]
    if not all(e):
        return None
    return _activity("java-types", "exact-integer", "predict", "预测精确整数参数的校验",
                     "只依据本次 ToolArgumentValidator 的实际实现：get_product_detail 参数只含 productId。下面哪一种 Java 值通过校验？不要把 Number 等同于可安全转换的整数。",
                     ["小数与精确整数区分", "目标 long 范围区分", "Double 即便写成 9.0 也不属于本实现接受类型"],
                     "new BigDecimal(\"9.0\") 可以经 toBigIntegerExact 得到 9，正值且在 long 范围内，校验成功。9.5 不是整数；Long.MAX_VALUE 加一越界；Double 的 9.0 虽实现 Number，仍被这段类型白名单拒绝。为何这样设计：先验证整数性和范围，才能避免静默截断或溢出。这里评测的是指定 Validator，不代表所有 Java 项目都应使用同一类型白名单。",
                     ["先看接受哪些 Number 子类型，再检查值。", "BigDecimal 的 scale 大于零不一定表示有小数值，toBigIntegerExact 判断是否能精确转整数。", "分两步：类型是否接受；数值是否正整数且在 Long.MAX_VALUE 内。四个输入都逐项核对。"], e,
                     options=[{"id": "a", "text": "Double.valueOf(9.0)"},
                              {"id": "b", "text": 'new BigDecimal("9.0")'},
                              {"id": "c", "text": 'new BigDecimal("9.5")'},
                              {"id": "d", "text": "BigInteger.valueOf(Long.MAX_VALUE).add(BigInteger.ONE)"}], answer_ids=["b"])


def build_curriculum(files: list[dict], concepts: list[dict]) -> list[dict]:
    """Produce at most forty version-bound, evidence-backed activities."""
    activities, ids = [], {c["id"] for c in concepts}
    chain = _recognise_orders(files) if "identity-permission" in ids else None
    if chain:
        activities.extend(_order_activities(chain))
    for concept in concepts:
        # Exact five-part pilot replaces the broad identity template.
        if concept["id"] == "identity-permission" and chain:
            continue
        spec, evidence = _TOPICS.get(concept["id"]), _evidence(files, concept)
        if not spec or not evidence:
            continue
        prefix = _anchor(files, evidence)
        explanation = (concept.get("explanation", "") + "\n为什么需要：" + concept.get("why", "")
                       + "\n这是一项基于静态源码线索的练习，未提供统一标准答案。请逐项对照验收条件，由本人或现有 Agent 评阅；没有实际运行或独立复现证据时，结果仍为待评阅，不能由关键词命中自动判定掌握。")
        for i in (0, 4):
            title, kind, prompt, rubric = spec[i:i + 4]
            activities.append(_activity(concept["id"], f"source-{kind}-{i}", kind, title,
                                        prefix + prompt, rubric, explanation, spec[8], evidence))
    if "java-types" in ids:
        numeric = _numeric_activity(files)
        if numeric:
            # Preserve the two-activity bound for the general type concept.
            activities = [a for a in activities if not (a["concept_id"] == "java-types" and a["kind"] == "reproduce")]
            activities.append(numeric)
    return activities[:40]
