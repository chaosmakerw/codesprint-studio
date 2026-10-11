"""Data-only teaching contracts. No imported content is evaluated or fetched."""
from __future__ import annotations

from urllib.parse import urlsplit

from .analysis import InputError, relative_path


def validate_evidence(evidence, files):
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 8:
        raise InputError("每个知识点或活动必须绑定 1 至 8 条当前源码或资料证据。")
    sources = {f["path"]: f for f in files}
    checked = []
    for anchor in evidence:
        if not isinstance(anchor, dict) or set(anchor) != {"path", "line", "quote", "sha256"}:
            raise InputError("源码或资料证据格式不正确。")
        path = relative_path(anchor["path"])
        src, line, quote = sources.get(path), anchor["line"], anchor["quote"]
        if not src or type(line) is not int or not 1 <= line <= len(src["text"].splitlines()) or not isinstance(quote, str) or not quote or len(quote) > 800:
            raise InputError("证据无法匹配当前源码或资料快照。")
        if anchor["sha256"] != src["sha256"] or quote not in src["text"].splitlines()[line - 1]:
            raise InputError("证据或文件哈希不匹配。")
        checked.append({**anchor, "path": path})
    return checked


def validate_concepts(raw_concepts, files, identity, text, existing=(), *, legacy_static=False):
    if not isinstance(raw_concepts, list) or len(raw_concepts) + len(existing) > 80:
        raise InputError("一个项目最多保存 80 个知识点。")
    fields = {"id", "title", "group", "explanation", "why", "prerequisites", "evidence", "detection", "limitations",
              "mechanism", "failures", "lesson", "references"}
    ids, result = {c["id"] for c in existing}, []
    for raw in raw_concepts:
        if not isinstance(raw, dict) or set(raw) - fields:
            raise InputError("知识点字段不符合导入协议。")
        cid = identity(raw.get("id"))
        if cid in ids:
            raise InputError("知识点标识已存在或重复；导入只能新增知识点。")
        ids.add(cid)
        prerequisites = raw.get("prerequisites", [])
        failures, references = raw.get("failures", []), raw.get("references", [])
        if not isinstance(prerequisites, list) or len(prerequisites) > 80 or len(set(identity(p) for p in prerequisites)) != len(prerequisites):
            raise InputError("前置知识点列表无效。")
        if not isinstance(failures, list) or len(failures) > 20 or not isinstance(references, list) or len(references) > 12:
            raise InputError("常见错误或参考出处数量无效。")
        checked_refs = []
        for ref in references:
            if not isinstance(ref, dict) or set(ref) != {"title", "url", "note"}:
                raise InputError("参考出处应包含 title、url 与 note。")
            url = text(ref["url"], 2000)
            try:
                parsed = urlsplit(url)
                valid = parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password
                parsed.port
            except ValueError:
                valid = False
            if not valid or "\\" in url or any(ch.isspace() or ord(ch) < 32 for ch in url):
                raise InputError("参考出处只支持安全的 http 或 https 绝对地址。")
            checked_refs.append({"title": text(ref["title"], 200), "url": url, "note": text(ref["note"], 3000, empty=True)})
        concept = {"id": cid, "title": text(raw.get("title"), 200), "group": text(raw.get("group"), 100),
                   "explanation": text(raw.get("explanation"), 12000), "why": text(raw.get("why"), 12000),
                   "prerequisites": prerequisites, "evidence": validate_evidence(raw.get("evidence"), files),
                   "detection": text(raw.get("detection", "agent_proposed"), 100),
                   "limitations": text(raw.get("limitations", "教学内容待本人审阅；静态证据不证明运行正确或独立掌握。"), 5000)}
        # Keep schema1 static records byte-for-byte comparable to historical exports.
        for key, limit in (("mechanism", 16000), ("lesson", 48000)):
            if key in raw:
                concept[key] = text(raw[key], limit, empty=True)
        if "failures" in raw:
            concept["failures"] = [text(f, 5000) for f in failures]
        if "references" in raw:
            concept["references"] = checked_refs
        result.append(concept)
    all_concepts = list(existing) + result
    if len({c["group"] for c in all_concepts}) > 8:
        raise InputError("一个项目最多包含 8 个知识分类。")
    graph = {c["id"]: c.get("prerequisites", []) for c in all_concepts}
    for concept in all_concepts:
        if set(concept.get("prerequisites", [])) - ids:
            if (legacy_static or concept in existing) and concept.get("detection") == "static_candidate":
                graph[concept["id"]] = [p for p in graph[concept["id"]] if p in ids]
            else:
                raise InputError("前置知识点必须存在于同批或当前项目中。")
    visiting, visited = set(), set()
    def visit(cid):
        if cid in visiting:
            raise InputError("前置知识点形成循环，不能导入。")
        if cid in visited:
            return
        visiting.add(cid)
        for prerequisite in graph[cid]:
            visit(prerequisite)
        visiting.remove(cid)
        visited.add(cid)
    for cid in graph:
        visit(cid)
    return result


FLOW_FIELDS = {"id", "title", "summary", "scenario", "input", "expected", "steps", "branches", "trace_cases", "resources"}
FLOW_STEP_FIELDS = {"id", "title", "actor", "action", "input", "output", "why", "concept_ids", "evidence"}
METHOD_FIELDS = {"id", "signature", "input", "output", "relation", "why", "evidence"}
SUMMARY_FIELDS = {"purpose", "audience", "scenario", "capabilities", "learning_value", "limitations", "evidence"}
MAX_TRACE_FRAMES = 120


def validate_project_summary(raw, files, text):
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != SUMMARY_FIELDS:
        raise InputError("项目总结字段不符合协议。")
    capabilities = raw["capabilities"]
    if not isinstance(capabilities, list) or not 1 <= len(capabilities) <= 20:
        raise InputError("项目总结需列出 1 至 20 项实际功能。")
    return {**{k: text(raw[k], 8000) for k in SUMMARY_FIELDS - {"evidence", "capabilities"}},
            "capabilities": [text(c, 1000) for c in capabilities],
            "evidence": validate_evidence(raw["evidence"], files)}


def validate_flows(raw_flows, concepts, files, identity, text, existing=()):
    """Validate a teaching narrative against current concepts and source anchors.

    Traces are author-proposed examples, never execution or production evidence.
    Values deliberately stay strings: no imported expression is evaluated.
    """
    if not isinstance(raw_flows, list) or len(raw_flows) + len(existing) > 12:
        raise InputError("一个项目最多保存 12 条场景调用链。")
    concept_ids = {c["id"] for c in concepts}
    flow_ids, result = {f["id"] for f in existing}, []

    def structure(raw, fields, label):
        if not isinstance(raw, dict) or set(raw) != fields:
            raise InputError(f"{label}字段不符合场景调用链协议。")

    def items(raw, minimum, maximum, label):
        if not isinstance(raw, list) or not minimum <= len(raw) <= maximum:
            raise InputError(f"{label}数量应为 {minimum} 至 {maximum}。")
        return raw

    def references(raw, valid, minimum, maximum, label):
        values = [identity(v) for v in items(raw, minimum, maximum, label)]
        if len(set(values)) != len(values) or set(values) - valid:
            raise InputError(f"{label}必须唯一且引用当前有效标识。")
        return values

    for raw in raw_flows:
        structure(raw, FLOW_FIELDS, "场景调用链")
        fid = identity(raw["id"])
        if fid in flow_ids:
            raise InputError("场景调用链标识已存在或重复；导入只能新增调用链。")
        flow_ids.add(fid)
        flow = {"id": fid, "title": text(raw["title"], 200), "summary": text(raw["summary"], 4000),
                "scenario": text(raw["scenario"], 8000), "input": text(raw["input"], 8000),
                "expected": text(raw["expected"], 8000)}
        steps, step_ids = [], set()
        for step in items(raw["steps"], 2, 20, "调用链步骤"):
            if not isinstance(step, dict) or set(step) - (FLOW_STEP_FIELDS | {"methods"}) or not FLOW_STEP_FIELDS <= set(step):
                raise InputError("调用链步骤字段不符合场景调用链协议。")
            sid = identity(step["id"])
            if sid in step_ids:
                raise InputError("同一调用链的步骤标识不能重复。")
            step_ids.add(sid)
            checked_step = {"id": sid, "title": text(step["title"], 200), "actor": text(step["actor"], 200),
                          **{key: text(step[key], 5000) for key in ("action", "input", "output", "why")},
                          "concept_ids": references(step["concept_ids"], concept_ids, 1, 80, "步骤知识点"),
                          "evidence": validate_evidence(step["evidence"], files)}
            if "methods" in step:
                methods, method_ids = [], set()
                for method in items(step["methods"], 1, 30, "方法调用"):
                    structure(method, METHOD_FIELDS, "方法调用")
                    mid = identity(method["id"])
                    if mid in method_ids or method["relation"] not in {"call", "return", "stop"}:
                        raise InputError("方法标识重复或调用关系无效。")
                    method_ids.add(mid)
                    methods.append({"id": mid, "signature": text(method["signature"], 1000),
                                    "relation": method["relation"],
                                    **{k: text(method[k], 5000) for k in ("input", "output", "why")},
                                    "evidence": validate_evidence(method["evidence"], files)})
                checked_step["methods"] = methods
            steps.append(checked_step)
        branches = []
        for branch in items(raw["branches"], 0, 20, "分支"):
            structure(branch, {"from_step_id", "condition", "outcome", "skipped_step_ids"}, "分支")
            from_step = identity(branch["from_step_id"])
            if from_step not in step_ids:
                raise InputError("分支起点必须引用当前调用链步骤。")
            branches.append({"from_step_id": from_step, "condition": text(branch["condition"], 5000),
                             "outcome": text(branch["outcome"], 5000),
                             "skipped_step_ids": references(branch["skipped_step_ids"], step_ids, 0, 20, "跳过步骤")})
        cases, case_ids = [], set()
        for case in items(raw["trace_cases"], 0, 12, "参数追踪案例"):
            case_fields = {"id", "title", "input", "result", "frames"}
            if not isinstance(case, dict) or not case_fields <= set(case) or set(case) - (case_fields | {"skipped_step_ids"}):
                raise InputError("参数追踪案例字段不符合场景调用链协议。")
            cid = identity(case["id"])
            if cid in case_ids:
                raise InputError("同一调用链的参数追踪案例标识不能重复。")
            case_ids.add(cid)
            frames = []
            for frame in items(case["frames"], 1, MAX_TRACE_FRAMES, "参数追踪帧"):
                frame_fields = {"step_id", "explanation", "values"}
                if not isinstance(frame, dict) or not frame_fields <= set(frame) or set(frame) - (frame_fields | {"method_id"}):
                    raise InputError("参数追踪帧字段不符合场景调用链协议。")
                sid = identity(frame["step_id"])
                if sid not in step_ids:
                    raise InputError("追踪帧必须引用当前调用链步骤。")
                values = []
                for value in items(frame["values"], 1, 24, "追踪变量"):
                    structure(value, {"name", "type", "value", "source"}, "追踪变量")
                    values.append({"name": text(value["name"], 200), "type": text(value["type"], 200),
                                   "value": text(value["value"], 4000, empty=True), "source": text(value["source"], 1000)})
                checked_frame = {"step_id": sid, "explanation": text(frame["explanation"], 5000), "values": values}
                if "method_id" in frame:
                    mid = identity(frame["method_id"])
                    current_step = next(s for s in steps if s["id"] == sid)
                    if mid not in {m["id"] for m in current_step.get("methods", [])}:
                        raise InputError("追踪帧方法必须存在于当前步骤。")
                    checked_frame["method_id"] = mid
                frames.append(checked_frame)
            checked_case = {"id": cid, "title": text(case["title"], 200), "input": text(case["input"], 8000),
                            "result": text(case["result"], 8000), "frames": frames}
            if "skipped_step_ids" in case:
                skipped = references(case["skipped_step_ids"], step_ids, 0, 20, "案例未调用步骤")
                traced = {f["step_id"] for f in frames}
                if traced & set(skipped):
                    raise InputError("案例未调用步骤不能同时出现在追踪帧中。")
                allowed_skips = {s for b in branches if b["from_step_id"] in traced for s in b["skipped_step_ids"]}
                order = {s["id"]: n for n, s in enumerate(steps)}
                for frame in frames:
                    method = next((m for step in steps if step["id"] == frame["step_id"] for m in step.get("methods", [])
                                   if m["id"] == frame.get("method_id")), None)
                    if method and method["relation"] == "stop":
                        allowed_skips.update(s for s in step_ids if order[s] > order[frame["step_id"]])
                if set(skipped) - allowed_skips:
                    raise InputError("案例未调用步骤必须对应已追踪分支或明确停止方法。")
                checked_case["skipped_step_ids"] = skipped
            cases.append(checked_case)
        resources = []
        for resource in items(raw["resources"], 0, 20, "资源说明"):
            structure(resource, {"name", "ownership", "lifecycle", "failure"}, "资源说明")
            resources.append({"name": text(resource["name"], 200),
                              **{key: text(resource[key], 3000) for key in ("ownership", "lifecycle", "failure")}})
        flow.update(steps=steps, branches=branches, trace_cases=cases, resources=resources)
        result.append(flow)
    return result


def flow_contract():
    """Publish the same data contract used by validation for Agent handoffs."""
    return {"fields": sorted(FLOW_FIELDS), "step_fields": sorted(FLOW_STEP_FIELDS),
            "optional_step_fields": ["methods"], "method_fields": sorted(METHOD_FIELDS),
            "method_relations": ["call", "return", "stop"], "optional_frame_fields": ["method_id"],
            "branch_fields": ["from_step_id", "condition", "outcome", "skipped_step_ids"],
            "trace_case_fields": ["id", "title", "input", "result", "frames"],
            "optional_trace_case_fields": ["skipped_step_ids"],
            "frame_fields": ["step_id", "explanation", "values"],
            "value_fields": ["name", "type", "value", "source"],
            "resource_fields": ["name", "ownership", "lifecycle", "failure"],
            "limits": {"flows": 12, "steps_min": 2, "steps_max": 20, "step_concepts": 80,
                       "branches": 20, "skipped_steps": 20, "trace_cases": 12, "frames": MAX_TRACE_FRAMES,
                       "values_per_frame": 24, "resources": 20},
            "text_limits": {"title_actor_name_type": 200, "summary": 4000,
                            "scenario_input_expected_case_input_result": 8000,
                            "step_text_branch_text_frame_explanation": 5000,
                            "value": 4000, "value_source": 1000, "resource_text": 3000},
            "rules": ["必填字段完整，只允许协议声明的可选字段；所有叙述字段和变量value均为字符串。",
                      "每步必须有人话说明 action、input、output、why，至少引用一个有效 concept_id 和当前文件证据。",
                      "同一调用链的步骤和案例 id 唯一；分支和追踪帧只引用同一调用链步骤。",
                      "scenario 和 input 提供具体输入；trace_cases 是教学推演，不表示实际执行或生产验收。",
                      "新 Agent 包应提供完整 trace_cases；旧包可省略 flows，trace_cases 和 resources 可为空。",
                      "methods 依次列出真实方法签名、输入、输出及 call/return/stop 关系，每个方法绑定当前文件证据。",
                      "frame.method_id 只引用同一步methods；case.skipped_step_ids 只引用已追踪分支或明确停止后的未调用步骤，不能同时出现在frames。",
                      "资源说明须对应源码中的资源，写清归属、创建释放时机和失败行为，不虚构数据库或真实模型。"]}
