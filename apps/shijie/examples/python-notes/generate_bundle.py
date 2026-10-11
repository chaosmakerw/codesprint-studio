"""Build a source-backed learning bundle from these fixed original texts.

The generator reads text only. It never imports or executes the sample project.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PATHS = (
    "project/job_queue.py",
    "project/test_job_queue.py",
    "materials/01-inputs.md",
    "materials/02-dedup.md",
    "materials/03-lifecycle.md",
    "materials/04-snapshot.md",
)


def build_flows(anchor) -> list[dict]:
    """Explain these fixed source calls; frames are predictions, not run logs."""
    def step(sid, title, actor, action, incoming, output, why, cids, needle):
        return {"id": sid, "title": title, "actor": actor, "action": action,
                "input": incoming, "output": output, "why": why,
                "concept_ids": cids, "evidence": [anchor(needle)]}

    def frame(sid, explanation, *values):
        return {"step_id": sid, "explanation": explanation,
                "values": [{"name": n, "type": t, "value": v, "source": s} for n, t, v, s in values]}

    pending = ("self._pending", "dict[str, dict[str, str]]", "{}", "构造器为当前实例创建")
    full = '{"a": {"key": "a", "text": "First"}}'
    queue_resource = {"name": "当前 NoteQueue 的 _pending 与 _capacity",
                      "ownership": "构造器对每个 self 单独创建字典；容量属于同一实例。这里没有线程、数据库或网络连接。",
                      "lifecycle": "队列创建时为空；enqueue 写入任务，take 用 pop 移除，实例不再被引用时可回收。没有持久化。",
                      "failure": "输入或容量异常发生在写入前，既有任务保留；取出后没有处理中集合，工作者失败时无法靠本例恢复任务。"}
    enqueue_steps = [
        step("normalize", "先把任务键统一成内部标识", "队列入口：NoteQueue.enqueue → NoteQueue._key",
             "enqueue 直接调用 self._key(raw_key)。_key 检查 str，依次 strip、casefold，再拒绝空键并返回。",
             'raw_key=" A "，str；队列初始为空', '局部 key="a"，str',
             "相同含义的键先统一，再谈去重；类型提示不会自动校验调用输入。", ["note-inputs"], "key = self._key(raw_key)"),
        step("body", "正文是否合法，先在这里决定", "队列入口：NoteQueue.enqueue",
             "检查正文类型与 strip 后是否为空。非法则 raise ValueError，后续不执行。",
             'text=" First "，str；key="a"', '有效正文继续；当前 text 仍是原字符串，存储时才 strip',
             "重复请求也应满足输入约束，不能先去重而跳过非法正文。", ["note-inputs", "note-dedup"], "if not isinstance(text, str) or not text.strip():"),
        step("duplicate", "查看此键是否已经在等待", "队列入口：NoteQueue.enqueue",
             "在当前 _pending 字典查 key；存在就直接 return False，保留旧任务。",
             'key="a"；当前 _pending={}', '本次不存在，继续；存在时返回 bool False',
             "去重只覆盖待处理键；满队列中的有效重复仍得到 False。", ["note-dedup"], "if key in self._pending:"),
        step("capacity", "新任务还放得下吗", "队列入口：NoteQueue.enqueue",
             "比较 len(_pending) 与 _capacity；已满则 raise OverflowError。只有新键能走到这里。",
             "len(_pending)=0，int；_capacity=2，int", "0 >= 2 为 False，继续写入",
             "在写入之前约束容量，异常不会插入一半任务。", ["note-dedup"], "if len(self._pending) >= self._capacity:"),
        step("store", "写入规范化任务并报告成功", "队列入口：NoteQueue.enqueue",
             "创建任务字典，text.strip() 得到存储正文；赋给 _pending[key]，然后 return True。",
             'key="a"；text=" First "；_pending={}', f"_pending={full}；返回 bool True",
             "统一键与正文保存形式；前面的拒绝条件都通过后才改变状态。", ["note-inputs", "note-dedup"], 'self._pending[key] = {"key": key, "text": text.strip()}'),
        step("read", "调用者读取副本核对结果", "调用者 → NoteQueue.snapshot",
             "enqueue 已返回后，示例调用者再直接调用 snapshot；列表推导式创建新列表，dict(job) 复制每个任务字典。",
             f"当前 _pending={full}", '[{"key": "a", "text": "First"}]，list[dict[str, str]]',
             "这个读取是调用者的下一次方法调用，enqueue 本身不调用 snapshot；副本避免外部修改内部任务。", ["note-snapshot"], "return [dict(job) for job in self._pending.values()]"),
    ]
    enqueue_steps[0]["evidence"] += [anchor("if not isinstance(raw, str):"),
                                     anchor("normalized = raw.strip().casefold()")]
    enqueue_steps[3]["evidence"] += [anchor('raise OverflowError("pending queue is full")')]
    enqueue_steps[4]["evidence"] += [anchor("return True")]
    enqueue = {"id": "note-enqueue", "title": "任务入队", "summary": "把原始文本变为待处理任务。以下值依据源码手算；单线程真实内存对象，没有框架、HTTP、数据库或模型。",
               "scenario": "学习者提交一条待复习笔记，希望重复键保留旧任务，新键在容量允许时入队。",
               "input": '已创建 NoteQueue(2)，依次调用 enqueue(" A ", " First ")、snapshot()。',
               "expected": 'enqueue 返回 True，snapshot 为 [{"key":"a","text":"First"}]；非法、重复和容量满均在写入前停止。',
               "steps": enqueue_steps,
               "branches": [
                   {"from_step_id": "normalize", "condition": "raw_key=42 或空白键", "outcome": "ValueError；没有局部 key 返回给 enqueue，_pending 保留。", "skipped_step_ids": ["body", "duplicate", "capacity", "store", "read"]},
                   {"from_step_id": "body", "condition": 'text=" " 或非字符串', "outcome": "ValueError；非法重复也在去重之前停止。", "skipped_step_ids": ["duplicate", "capacity", "store", "read"]},
                   {"from_step_id": "duplicate", "condition": 'key="a" 已在 _pending', "outcome": "return False；旧正文不覆盖，不检查容量。", "skipped_step_ids": ["capacity", "store", "read"]},
                   {"from_step_id": "capacity", "condition": "len(_pending)=1，_capacity=1，key=b 是新键", "outcome": "OverflowError；旧任务保留，新任务未写入。", "skipped_step_ids": ["store", "read"]},
               ], "trace_cases": [
                   {"id": "success", "title": "带空格和大写键首次入队", "input": 'NoteQueue(2) 为空；enqueue(" A ", " First ") 后调用 snapshot()',
                    "result": '静态预测：True；[{"key":"a","text":"First"}]。未将预测帧当运行记录。', "frames": [
                        frame("normalize", "_key 直接返回 a，绑定到 enqueue 的 key。", ("raw_key", "str", '" A "', "调用者参数"), ("key", "str", '"a"', "_key 返回"), pending),
                        frame("body", "正文有效；检查时不改 text 变量。", ("text", "str", '" First "', "调用者参数")),
                        frame("duplicate", "a 不在空字典，未提前返回。", ("key in self._pending", "bool", "False", "当前字典查键")),
                        frame("capacity", "容量检查通过。", ("len(self._pending)", "int", "0", "当前待处理条数"), ("self._capacity", "int", "2", "构造器参数")),
                        frame("store", "写入 a，正文去两端空格，返回 True。", ("self._pending", "dict[str, dict[str, str]]", full, "enqueue 赋值"), ("return", "bool", "True", "enqueue 返回")),
                        frame("read", "调用者再读 snapshot，元素是任务副本。", ("return", "list[dict[str, str]]", '[{"key":"a","text":"First"}]', "snapshot 返回")),
                    ]},
                   {"id": "duplicate-full", "title": "满队列中的有效重复", "input": 'NoteQueue(1) 已存 a/First；enqueue(" A ", "Replacement")',
                    "result": "静态预测：False，a 的正文仍为 First；容量检查未执行。", "frames": [
                        frame("normalize", "原始键统一为 a。", ("key", "str", '"a"', "_key 返回")),
                        frame("body", "Replacement 非空，先通过输入检查。", ("text", "str", '"Replacement"', "调用者参数")),
                        frame("duplicate", "字典中有 a，直接 False，不覆盖旧值。", ("return", "bool", "False", "去重分支"), ("self._pending", "dict[str, dict[str, str]]", full, "原有状态保留")),
                    ]},
                   {"id": "invalid-key", "title": "类型提示挡不住整数键", "input": 'NoteQueue(2) 为空；enqueue(42, "Note")',
                    "result": "静态预测：ValueError(key must be text)；_pending 仍为空。", "frames": [
                        frame("normalize", "isinstance(42, str) 为 False，_key 抛异常，key 还未绑定。", ("raw_key", "int", "42", "调用者实际参数"), pending),
                    ]},
                   {"id": "invalid-duplicate-body", "title": "重复键配空正文仍然非法", "input": 'NoteQueue(1) 已存 a/First；enqueue("a", " ")',
                    "result": "静态预测：ValueError(text must not be blank)，旧任务不变；不是 False。", "frames": [
                        frame("normalize", "键 a 有效。", ("key", "str", '"a"', "_key 返回")),
                        frame("body", "strip 得到空字符串；正文检查提前抛异常。", ("text", "str", '" "', "参数"), ("text.strip()", "str", '""', "表达式结果"), ("self._pending", "dict[str, dict[str, str]]", full, "原有状态保留")),
                    ]},
                   {"id": "new-key-full", "title": "容量已满时拒绝新键", "input": 'NoteQueue(1) 已存 a/First；enqueue("b", "New")',
                    "result": "静态预测：OverflowError(pending queue is full)，未写入 b。", "frames": [
                        frame("normalize", "合法键得到 b。", ("key", "str", '"b"', "_key 返回")),
                        frame("body", "New 有效。", ("text", "str", '"New"', "参数")),
                        frame("duplicate", "b 不是现有待处理键。", ("key in self._pending", "bool", "False", "查键")),
                        frame("capacity", "1 >= 1 成立，在写入前异常。", ("len(self._pending)", "int", "1", "当前条数"), ("self._capacity", "int", "1", "构造器"), ("self._pending", "dict[str, dict[str, str]]", full, "旧任务保留")),
                    ]},
               ], "resources": [queue_resource]}
    lifecycle_steps = [
        step("empty", "先确认有没有任务可取", "队列入口：NoteQueue.take", "检查 _pending 是否为空；为空直接 return None。", "按 b、a 顺序已有两个任务", "非空则继续；空则 None", "避免对空迭代器 next 触发 StopIteration。", ["note-lifecycle"], "if not self._pending:"),
        step("oldest", "找到最早进入的任务键", "队列入口：NoteQueue.take", "iter 字典得到按插入顺序的键迭代器，next 取第一个。", "当前键顺序 b → a", 'oldest_key="b"，str', "FIFO 按插入顺序，不按字母排序。", ["note-lifecycle"], "oldest_key = next(iter(self._pending))"),
        step("remove", "删除它，返回任务副本", "队列入口：NoteQueue.take", "pop 移除 b 并返回任务，dict 再建副本，take 返回。", 'oldest_key="b"', '返回 {"key":"b","text":"First"}；内部只剩 a', "取出释放键和容量，尚未说明任务业务处理是否成功。", ["note-lifecycle", "note-snapshot"], "return dict(self._pending.pop(oldest_key))"),
        *[{**s, "id": "re-" + s["id"], "input": s["input"].replace('" A "', '" B "').replace('"a"', '"b"').replace('" First "', '"Again"')} for s in enqueue_steps[:5]],
        step("snapshot", "再看新的待处理顺序", "调用者 → NoteQueue.snapshot", "第二次 enqueue 返回后，调用者再取 snapshot；新 b 排到 a 后。", '键顺序 a → b', '[{"key":"a","text":"Second"},{"key":"b","text":"Again"}]', "观察的是当前待处理队列，不含已取出任务或处理历史。", ["note-lifecycle", "note-snapshot"], "return [dict(job) for job in self._pending.values()]"),
    ]
    lifecycle_steps[3]["input"] = 'take 已返回，调用者再 enqueue(" B ", "Again")；内部只有 a'
    lifecycle_steps[3]["output"] = 'key="b"，str'
    lifecycle_steps[4]["input"] = 'text="Again"，str；key="b"'
    lifecycle_steps[5]["input"] = 'key="b"；内部只有 a'
    lifecycle_steps[6]["input"] = 'len(_pending)=1，_capacity=2'
    lifecycle_steps[6]["output"] = '1 >= 2 为 False，继续写入'
    lifecycle_steps[7]["input"] = 'key="b"；text="Again"；内部已有 a/Second'
    lifecycle_steps[7]["output"] = '内部键顺序 a → b；返回 bool True'
    lifecycle = {"id": "note-take-requeue", "title": "取出并再次入队", "summary": "沿 take 返回到调用者，再进入 enqueue；这两个方法之间是调用者安排的顺序。以下是源码预测，没有工作者或可靠处理保证。",
                 "scenario": "先复习最早加入的笔记，随后以同一个键提交新版笔记，观察去重范围和容量释放。",
                 "input": 'NoteQueue(2) 先存 b/First、a/Second；依次 take()、enqueue(" B ", "Again")、snapshot()。',
                 "expected": '取 b/First；重新入队 True；快照为 a/Second、b/Again。空队列 take 则 None。', "steps": lifecycle_steps,
                 "branches": [{"from_step_id": "empty", "condition": "_pending={}", "outcome": "take 返回 None；本案例调用者在此结束，不调用 enqueue。", "skipped_step_ids": [s["id"] for s in lifecycle_steps[1:]]}],
                 "trace_cases": [
                     {"id": "release-requeue", "title": "满队列取出后，同键在末尾重入", "input": '容量2，内部按 b/First → a/Second；take 后 enqueue(" B ","Again") 后 snapshot。', "result": '静态预测：take 返回 b/First，enqueue 返回 True，快照键序 ["a","b"]。', "frames": [
                         frame("empty", "非空，继续。", ("self._pending", "dict[str, dict[str, str]]", '{"b":{"key":"b","text":"First"},"a":{"key":"a","text":"Second"}}', "先前两次入队")),
                         frame("oldest", "按插入顺序取 b。", ("oldest_key", "str", '"b"', "next(iter(_pending))")),
                         frame("remove", "pop 删除 b；返回的是该任务的副本。", ("return", "dict[str, str]", '{"key":"b","text":"First"}', "take 返回"), ("self._pending", "dict[str, dict[str, str]]", '{"a":{"key":"a","text":"Second"}}', "pop 后状态")),
                         frame("re-normalize", "take 返回以后，调用者再调用 enqueue；B 变为 b。", ("raw_key", "str", '" B "', "第二次参数"), ("key", "str", '"b"', "_key 返回")),
                         frame("re-body", "Again 通过正文检查。", ("text", "str", '"Again"', "第二次参数")),
                         frame("re-duplicate", "当前只有 a，b 已释放，查键 False。", ("key in self._pending", "bool", "False", "此时字典查键")),
                         frame("re-capacity", "只剩1项，小于容量2。", ("len(self._pending)", "int", "1", "pop 后条数"), ("self._capacity", "int", "2", "构造器")),
                         frame("re-store", "把新 b 插在 a 后，返回 True。", ("self._pending", "dict[str, dict[str, str]]", '{"a":{"key":"a","text":"Second"},"b":{"key":"b","text":"Again"}}', "新的字典插入"), ("return", "bool", "True", "enqueue 返回")),
                         frame("snapshot", "再次调用读取，保留当前插入序。", ("return", "list[dict[str, str]]", '[{"key":"a","text":"Second"},{"key":"b","text":"Again"}]', "snapshot 返回副本")),
                     ]},
                     {"id": "empty-stop", "title": "没有任务时先停在空判断", "input": "新的 NoteQueue(2)；仅调用 take()。", "result": "静态预测：None；没有 oldest_key，没有执行 pop 或 enqueue。", "frames": [
                         frame("empty", "空字典为假，直接 None。这个调用者没有安排再次入队。", pending, ("return", "NoneType", "None", "空分支返回")),
                     ]},
                 ], "resources": [queue_resource,
                     {"name": "take 返回的任务副本与 snapshot 返回副本", "ownership": "调用者拥有返回字典或列表；内部 a/b 任务仍由队列拥有。字符串字段不可变。", "lifecycle": "dict 与列表推导式在读取时创建；本例没有显式 close，引用消失后可回收。", "failure": "修改返回字典不改变内部对应字典；浅拷贝保护仅适用于本例字符串字段，未证明嵌套可变字段隔离。"}]}
    return [enqueue, lifecycle]


def build_bundle() -> dict:
    files = [{"path": path, "text": (ROOT / path).read_text(encoding="utf-8")} for path in PATHS]
    by_path = {file["path"]: file["text"] for file in files}

    def anchor(needle: str, path="project/job_queue.py") -> dict:
        matches = [(index, line) for index, line in enumerate(by_path[path].splitlines(), 1) if needle in line]
        if len(matches) != 1:
            raise ValueError(f"Expected one source anchor for {path}: {needle!r}; found {len(matches)}")
        line_number, quote = matches[0]
        return {"path": path, "line": line_number, "quote": quote,
                "sha256": hashlib.sha256(by_path[path].encode("utf-8")).hexdigest()}

    definitions = [
        ("note-inputs", "输入校验与键规范化", "输入与数据", "materials/01-inputs.md", [],
         "类型提示描述期望，不替代运行时校验；任务键用 strip 与 casefold 得到一致表示。",
         "构造器先拒绝非整数或非正容量。enqueue 先规范化键，再检查正文，之后才进入状态判断。",
         "把不合法输入挡在写入之前；规范化能合并大小写与边界空格差异，但会改变键的等价关系。",
         ["把 True 当作合法整数容量", "以为 : str 会自动拒绝整数", "误把正文内部空格也删掉"],
         [anchor("type(capacity) is not int"), anchor("normalized = raw.strip().casefold()")]),
        ("note-dedup", "待处理去重与容量顺序", "状态与流程", "materials/02-dedup.md", ["note-inputs"],
         "字典键只代表当前待处理任务，重复返回 False，不覆盖原正文；容量约束仅计算待处理条数。",
         "合法输入先检查重复键，再检查容量；两个条件都未阻止时才写入并返回 True。",
         "让满队列中的有效重复请求仍得到稳定的去重结果；只用内存与单线程，边界容易观察。",
         ["先检查容量导致重复请求在满队列时抛异常", "先赋值再去重覆盖旧任务", "把待处理去重误认为历史永久去重"],
         [anchor("if key in self._pending:"), anchor("if len(self._pending) >= self._capacity:")]),
        ("note-lifecycle", "FIFO 与取出后的状态", "状态与流程", "materials/03-lifecycle.md", ["note-dedup"],
         "取出顺序按插入顺序。take 会删除待处理任务，释放键和容量；空队列返回 None。",
         "next(iter(...)) 取首个键，pop 删除并返回数据；取出后同键可以再次入队并排在末尾。",
         "短状态链便于手算；取出后不保留处理中状态，因此无法保证处理成功或失败重试。",
         ["误按字母排序预测取出结果", "将取出等同于处理成功", "只读取而不移除导致容量不能释放"],
         [anchor("oldest_key = next(iter(self._pending))"), anchor("self._pending.pop(oldest_key)")]),
        ("note-snapshot", "副本、引用与读取隔离", "对象与隔离", "materials/04-snapshot.md", ["note-inputs"],
         "列表与字典可变。快照复制外层列表和每个任务字典，调用者修改当前返回值不会修改内部任务。",
         "列表推导式生成新列表；dict(job) 生成新字典。当前字符串字段不可变，浅拷贝足以隔离本例。",
         "保护内部状态所有权；若新增嵌套可变字段，需要重新验证浅拷贝的边界。",
         ["只复制列表导致元素仍共享", "将内容相等误当成对象相同", "以为浅拷贝能递归复制 tags 列表"],
         [anchor("return [dict(job) for job in self._pending.values()]"), anchor("self._pending: dict")]),
    ]
    concepts = []
    for cid, title, group, material, prereqs, explanation, mechanism, why, failures, evidence in definitions:
        concepts.append({"id": cid, "title": title, "group": group, "explanation": explanation,
                         "mechanism": mechanism, "why": why, "failures": failures,
                         "lesson": by_path[material], "prerequisites": prereqs,
                         "evidence": evidence + [anchor("# ", material)], "references": []})

    activities = []

    def choice(aid, cid, title, prompt, options, answer, explanation, hints, needles):
        activities.append({"id": aid, "concept_id": cid, "kind": "predict", "title": title,
                           "prompt": prompt, "options": [{"id": letter, "text": text} for letter, text in zip("abcd", options)],
                           "answer_ids": [answer], "rubric": ["按当前实现准确预测结果", "说明实际停止位置与状态变化"],
                           "hints": hints, "explanation": explanation + " 本题只证明该输入的预测，不证明独立实现或生产能力。",
                           "evidence": [anchor(needle) for needle in needles]})

    choice("note-q01", "note-inputs", "从原始键到内部键",
           '新建 NoteQueue(2)，执行 enqueue(" A ", " First ")，然后 snapshot()。返回值与快照是什么？',
           ['True；[{"key":" A ","text":"First"}]', 'True；[{"key":"a","text":"First"}]',
            'False；[]', 'True；[{"key":"a","text":" First "}]'], "b",
           "B 正确：strip 去键与正文两端空格，键再 casefold 成 a，首次合法入队返回 True。A 忽略键规范化；C 把首次入队当重复；D 忽略正文的 strip。",
           ["分别跟踪键与正文。", "_key 的结果用于字典键，写入行又处理正文。", "先写 raw_key → normalized 的值，再写存储的 text；两个转换并非同一个变量。"],
           ["normalized = raw.strip().casefold()", 'self._pending[key] = {"key": key, "text": text.strip()}'])

    choice("note-q02", "note-inputs", "整数容量的边界",
           "分别新建 NoteQueue(True)、NoteQueue(0)、NoteQueue(1)、NoteQueue(2.0)。哪组创建结果符合当前构造器？",
           ["只有 1 成功，其余 ValueError", "True 和 1 成功，其余 ValueError", "1 和 2.0 成功，其余 ValueError", "四种都成功"], "a",
           "A 正确：type(capacity) 必须恰好为 int 且值大于零。True 的实际类型是 bool；0 非正；2.0 是 float。B 把布尔子类型关系套到 type 检查；C 把数值相等套到类型判断；D 忽略全部校验。",
           ["查看构造器的两项条件。", "先分类型，再判断 <= 0；不要只比较数值。", "可列两列：type 是否正好 int、值是否大于零。必须两列同时满足。"],
           ["type(capacity) is not int"])

    choice("note-q03", "note-inputs", "类型提示之外的真实校验",
           '新建 NoteQueue(2)，调用 enqueue(42, "Note")。假定调用直接来自 Python，而非外部解析器，结果是什么？',
           ["键自动变成字符串 42，入队 True", "因 : str 注解而在进入方法前抛 TypeError", "返回 False，队列保持空", "_key 抛 ValueError，队列保持空"], "d",
           "D 正确：注解不做自动运行时阻拦；_key 的 isinstance 检查发现 raw 为整数并抛 ValueError，尚未写入。A 虚构字符串转换；B 把注解当校验器；C 将非法输入混同重复键。",
           ["从 enqueue 第一句开始。", "函数参数注解与函数体内检查是两件事。", "找 _key 第一个可能抛出的异常，并确认写入行是否已执行。"],
           ["key = self._key(raw_key)", "if not isinstance(raw, str):"])

    choice("note-q04", "note-dedup", "满队列中的有效重复",
           'NoteQueue(1) 已成功加入 ("a", "First")。再次 enqueue(" A ", "Replacement")，哪种结果正确？',
           ["抛 OverflowError，原任务保留", "返回 True，正文替换为 Replacement", "返回 False，正文仍为 First", "返回 False，正文替换为 Replacement"], "c",
           "C 正确：规范化后仍为 a，重复判断先于容量，提前返回 False 且未覆盖。A 交换了判断顺序；B 虚构更新操作；D 忽略赋值在提前返回之后。",
           ["找到最先成立的 if。", "比较重复条件与容量条件的先后。", "遇到 return False 后停止，后面的容量和赋值均不执行。"],
           ["if key in self._pending:", "if len(self._pending) >= self._capacity:"])

    choice("note-q05", "note-dedup", "满队列中的新任务",
           'NoteQueue(1) 已成功加入 ("a", "First")。enqueue("b", "New") 的结果与状态是什么？',
           ["返回 False，保留 a", "抛 OverflowError，仍只保留 a", "抛 OverflowError，但 a 和 b 均保留", "返回 True，移除 a 后保留 b"], "b",
           "B 正确：b 不是重复键，但待处理长度已达到容量，异常发生在写入前。A 混淆去重与满队列；C 误认为异常之后继续赋值；D 虚构自动淘汰。",
           ["分别判断重复与长度。", "写入行位于容量检查之后。", "初始 len=1、capacity=1，代入 >=；异常后没有新的字典元素。"],
           ["if len(self._pending) >= self._capacity:", 'raise OverflowError("pending queue is full")'])

    choice("note-q06", "note-dedup", "非法重复请求先在哪里停止",
           'NoteQueue(1) 已成功加入 ("a", "First")。enqueue("a", " ") 会发生什么？',
           ["抛 ValueError，原任务保留", "返回 False，原任务保留", "抛 OverflowError，原任务保留", "返回 True，正文变为空字符串"], "a",
           "A 正确：正文先校验，空白经 strip 后为空，立即 ValueError。B 跳过了正文检查；C 跳到更晚的容量判断；D 虚构写入非法正文。有效重复返回 False 不意味着无效重复也绕过校验。",
           ["按源码顺序检查每个条件。", "正文检查发生在重复检查之前。", "把空白字符串执行 strip 的结果代入 not 条件，标出第一处 raise。"],
           ["if not isinstance(text, str) or not text.strip():", "if key in self._pending:"])

    choice("note-q07", "note-lifecycle", "字母顺序与入队顺序",
           'NoteQueue(2) 依次加入 ("b", "First")、("a", "Second")。第一次 take() 的键与剩余键是什么？',
           ["取 a，剩 b", "取 b，仍剩 b、a", "取 b，剩 a", "取 a，队列清空"], "c",
           "C 正确：字典按插入顺序迭代，b 最早；pop 删除 b，内部剩 a。A 按字母排序；B 忽略 pop 删除；D 同时误解顺序与删除范围。",
           ["写下入队的时间顺序。", "iter 字典产出键，next 取首键，pop 删除这一键。", "顺序是 b→a；不要先排序。只对 oldest_key 执行一次 pop。"],
           ["oldest_key = next(iter(self._pending))", "self._pending.pop(oldest_key)"])

    choice("note-q08", "note-lifecycle", "取出后重复键能否重新入队",
           'NoteQueue(2) 加入 ("b", "First")、("a", "Second")，take() 一次，随后 enqueue("B", "Again")。返回与快照键顺序是什么？',
           ["False；[a]", "True；[b,a]", "OverflowError；[a]", "True；[a,b]"], "d",
           "D 正确：take 已删除 b，释放键和一个容量；B 规范化为 b，重新插入到 a 后。A 把待处理去重当永久历史去重；B 虚构保留旧位置；C 忽略容量释放。",
           ["先完成 take，再计算下一次入队。", "重复检查只查询此刻 _pending，没有历史集合。", "取出后键列表只有 a，插入新 b 时排在末尾。"],
           ["self._pending.pop(oldest_key)", 'self._pending[key] = {"key": key, "text": text.strip()}'])

    choice("note-q09", "note-lifecycle", "空队列的停止位置",
           "新建 NoteQueue(1)，尚未入队。take() 与 snapshot() 分别返回什么？",
           ["None；[]", "{}；[]", "抛 StopIteration；[]", "None；None"], "a",
           "A 正确：take 在空检查处提前 None；snapshot 对空 values 构造空列表。B 虚构空字典；C 忽略 next 前的空检查；D 混淆两个方法的返回类型。",
           ["分别阅读两个方法的返回语句。", "take 空分支早于 next，snapshot 始终构造列表。", "先确认 if not _pending 成立，再观察空迭代的列表推导式。"],
           ["if not self._pending:", "return [dict(job) for job in self._pending.values()]"])

    choice("note-q10", "note-snapshot", "清空返回列表的影响",
           '队列已加入 ("a", "First")。执行 view=queue.snapshot(); view.clear() 后，再次 snapshot() 得到什么？',
           ["[]", '[{"key":"a","text":"First"}]', "抛 OverflowError", '[{"key":"a","text":""}]'], "b",
           "B 正确：列表推导式创建新列表，清空 view 只清空返回容器。A 误认它是内部容器；C 将读取与容量检查混合；D 将列表 clear 当作修改元素正文。",
           ["确认返回列表如何创建。", "画外部 view 与内部 _pending 两个引用。", "clear 操作的是新列表；源码没有调用 _pending.clear。"],
           ["return [dict(job) for job in self._pending.values()]"])

    choice("note-q11", "note-snapshot", "修改快照中的元素",
           '队列已加入 ("a", "First")。执行 view=queue.snapshot(); view[0]["text"]="Outside"，再 snapshot() 的正文是什么？',
           ["Outside，因为浅拷贝不复制任何对象", "Outside，因为字典都是同一个引用", "抛 TypeError，字典不可变", "First，因为任务字典也复制了"], "d",
           "D 正确：dict(job) 创建每个任务的新字典，修改副本的 text 不影响原字典。A 对浅拷贝定义错误：它复制当前字典但不递归复制嵌套可变值；B 忽略 dict(job)；C 将字典当不可变对象。当前字符串值满足隔离边界。",
           ["除了新列表，还看元素如何得到。", "dict(job) 和直接返回 job 的对象身份不同。", "分别标两个任务字典，它们最初内容相等，但改一个键值不会改另一个。"],
           ["return [dict(job) for job in self._pending.values()]"])

    choice("note-q12", "note-snapshot", "两个实例的状态是否共享",
           'left=NoteQueue(1)、right=NoteQueue(1)。left 加入 ("same", "Left") 后，right.enqueue("same", "Right") 与 left.take()["text"] 的结果是什么？',
           ["False；Right", "OverflowError；Left", "True；Left", "True；Right"], "c",
           "C 正确：构造器为每个 self 建立自己的 _pending 字典，right 初始为空，因此成功；left 保留自己的正文。A 把实例状态当全局去重并覆盖；B 把另一实例长度带入容量判断；D 虚构共享存储。多实例各自独立也不等于提供全局去重。",
           ["找 _pending 是在哪里创建的。", "两次构造分别作用于两个 self。", "画 left→字典1、right→字典2，两个同名键仍属于不同容器。"],
           ["self._pending: dict"])

    def open_task(aid, cid, kind, title, prompt, rubric, explanation, hints, needles, due=None):
        task = {"id": aid, "concept_id": cid, "kind": kind, "title": title, "prompt": prompt,
                "options": [], "answer_ids": [], "rubric": rubric, "explanation": explanation,
                "hints": hints, "evidence": [anchor(needle) for needle in needles]}
        if due is not None:
            task["due_after_hours"] = due
        activities.append(task)

    open_task("note-trace", "note-lifecycle", "trace", "逐行手算一个完整状态链",
              '不运行。NoteQueue(2) 依次 enqueue("B","First")、enqueue(" a ","Second")、take()、enqueue(" b ","Again")、snapshot()。提交每一步返回值、key 的值、_pending 的键顺序和停止位置；说明去重覆盖哪个阶段。',
              ["键规范化值准确", "各操作返回值与异常准确", "每一步键顺序与长度准确", "说明取出释放键但不证明处理成功"],
              "需要人工或 Agent 逐项复核执行表。应能跟踪未写入、待处理、取出三种状态，按真实条件而非最终答案猜过程。静态手算不证明线程、外部服务、持久化或独立实现。",
              ["为每一次操作画单独一行。", "先规范化，再校验，再看字典；take 要把移除记录进下一步。", "表头可用：操作｜返回｜局部 key｜待处理键顺序｜当前长度。只给表头，实际值由你填。"],
              ["normalized = raw.strip().casefold()", "self._pending.pop(oldest_key)"])

    open_task("note-debug", "note-snapshot", "debug", "为什么清空列表测试漏掉共享元素",
              '有人把 snapshot 改成 return list(self._pending.values())，仍用 view.clear() 后再次读取作为唯一测试并通过。解释这个测试为什么没命中缺陷；写一个会失败的最小测试草案，说明期望与实际，并提出最小修复。保持原项目只读，在自己的练习区操作。',
              ["区分外层列表与任务字典的引用", "用修改返回元素正文触发共享问题", "再次读取原正文作为正确期望", "修复复制任务字典而非放宽断言", "未运行时明确标为草案"],
              "外层 list 已复制，clear 不能暴露内层字典共享。需要改 view[0] 的 text 再读取，以保持原状态不变为期望；当前源码逐个 dict(job) 是相应的保护。评阅需检查真实执行或静态草案的证据，不能自动给关键词分数。",
              ["找出仍然是原对象的那一层。", "分别准备清空列表与修改列表元素两组对照。", '测试形状：入队 → snapshot → view[0]["text"] 改值 → 再 snapshot 断言原正文。具体断言与运行记录由你写。'],
              ["return [dict(job) for job in self._pending.values()]"])

    open_task("note-reproduce", "note-dedup", "reproduce", "独立复现小型待处理队列",
              "脱离源码，在自己的练习区实现容量固定的待处理队列：规范化文本键；非法输入提前停止；重复合法键返回 False 且不覆盖；满队列新键拒绝；取出最早键后允许同键重入；快照修改不影响内部。可以换方法名，但要保留这些契约。提交实现、6组以上输入的预期、实际测试命令与结果，注明是否看过提示。",
              ["合法新增与规范化有对照", "非法输入不改变内部状态", "满队列中重复和新键不同结果", "FIFO 与取出后重入正确", "快照容器与元素隔离", "实际测试或尚未运行明确区分", "辅助程度与独立性证据如实记录"],
              "这是独立复现验收，开放回答保持待复核。逐项核对设计与测试，不能用随机检测80%代替复现。若只提交片段或预期，不能宣布已运行。用真实队列对象的单元测试仍不覆盖HTTP、数据库、模型、并发或可靠任务处理。",
              ["先写允许状态与方法的输入输出。", "按校验→去重→容量→写入排序，再单独安排取出和快照。", "只给验收输入类别：首次成功、重复、无效、容量边界、FIFO重入、快照修改。实现和断言由你补全。"],
              ["if key in self._pending:", "if len(self._pending) >= self._capacity:", "return [dict(job) for job in self._pending.values()]"])

    open_task("note-transfer", "note-lifecycle", "transfer", "延迟迁移：取出不再释放去重键",
              "建议首次练习48小时后再做，先不看旧答案。新要求：同键在待处理或处理中都去重；工作者成功确认后释放键，失败时可重回队尾。请先说明现有 take 为什么不满足，再设计 pending/inflight 两种状态及 take、confirm、retry 的最小职责。给出处理中重复、成功后重入、失败重试与未知键确认四组验收；说明容量是否包含处理中，并保持定义一致。不要直接修改原实例。",
              ["指出现有pop立即释放键与新需求冲突", "待处理与处理中状态明确且迁移一致", "处理中重复与成功后重入各有对照", "失败重试不丢失或重复任务", "容量与未知键策略明确", "记录实际间隔、提示与源码版本"],
              "评阅关注原理迁移与定义一致性。现有实现只有pending，取出就pop；新需求需要保留inflight键并明确确认/重试。不同合理容量或未知键策略可接受，但必须有两侧测试。设置48小时仅是建议，未实际延迟与独立完成不构成迁移证据；内存方案仍无崩溃恢复保证。",
              ["先画当前状态，再加入处理中状态。", "给每个方法标注从哪个集合移出、加入哪个集合，以及去重查询哪些集合。", "状态图可起步为 pending→inflight→成功释放；失败返回pending。你还需自己定义键、容量和异常契约。"],
              ["self._pending.pop(oldest_key)", "if key in self._pending:"], due=48)

    return complete_framework({"schema_version": 2, "title": "原创 Python 笔记任务队列：从输入到隔离",
            "project": {"name": "Python 笔记任务队列"}, "files": files,
            "concepts": concepts, "activities": activities, "flows": build_flows(anchor)}, anchor)


def complete_framework(bundle, anchor):
    bundle["project_summary"] = {
        "purpose": "这是一个小型笔记复习任务队列：提交笔记时统一任务键、检查正文、阻止当前重复和容量溢出；取出时按入队先后删除一项；读取时给调用者一个隔离副本。它本身没有笔记界面、定时工作者或模型。",
        "audience": "需要看懂一个项目如何接收输入、改变状态、交还结果的学习者；程序调用者用enqueue提交任务、take取任务、snapshot检查当前状态。",
        "scenario": '调用者新建容量为2的队列，提交(" A "," First ")，内部保存a/First。再次提交同键不会覆盖；新键遇到满容量会拒绝；取出最早任务后同键可以再提交。',
        "capabilities": ["创建具有正整数容量的独立实例", "规范化文本键并拒绝非法输入", "对当前待处理键去重与限制容量", "按插入顺序取出并释放任务键", "复制列表和任务字典供外部读取"],
        "learning_value": "可以用几个短字符串手算一个完整状态机，同时学习类型提示与真实校验的区别、判断顺序、提前返回与异常、字典插入顺序、对象所有权和浅拷贝边界。项目虽小，已足以练习说明设计取舍；无需先学习某个特定语言框架。",
        "limitations": "流程帧是依据当前源码的静态预测。开发者可另行运行本示例自己的单元测试，但这里不把帧当执行日志。本例只适用于单线程、单进程内存：无HTTP、数据库、后台线程、ACK、持久化、故障恢复或真实模型。取出就释放键不等于工作完成；浅拷贝只隔离当前字符串字段。选择题通过不等于独立复现。",
        "evidence": [anchor("class NoteQueue:"), anchor("key = self._key(raw_key)"), anchor("return [dict(job) for job in self._pending.values()]")],
    }

    def method(mid, signature, relation, incoming, output, why, needle):
        return {"id": mid, "signature": signature, "relation": relation, "input": incoming,
                "output": output, "why": why, "evidence": [anchor(needle)]}

    definitions = {
        "normalize": [
            method("enqueue-enter", "NoteQueue.enqueue(raw_key: str, text: str) -> bool", "call", "调用者原始key与正文；注解不是自动校验", "进入后第一句调用_key", "保留原始值，再进行规范化。", "def enqueue("),
            method("key-enter", "NoteQueue._key(raw: str) -> str", "call", "raw是同一原始键", "先检查实际运行时类型", "静态方法只处理一个值，不访问队列状态。", "def _key("),
            method("key-type", "isinstance(raw, str)", "return", "raw真实对象", "bool：是否字符串", "整数42必须在字符串方法之前被拒绝。", "if not isinstance(raw, str):"),
            method("key-strip", "str.strip()", "return", '原始键例如" A "', '中间结果"A"', "先去两端空白，原始字符串没有原地修改。", "normalized = raw.strip().casefold()"),
            method("key-casefold", "str.casefold()", "return", 'strip结果例如"A"', 'normalized="a"', "统一大小写后再作为字典键。", "normalized = raw.strip().casefold()"),
            method("key-return", "NoteQueue._key(raw: str) -> str", "return", "规范化非空文本", "返回str，enqueue局部key随后完成绑定", "调用者只有正常返回后才有新局部key。", "return normalized"),
            method("key-stop", "ValueError(str)，由NoteQueue._key抛出", "stop", "非文本或规范化为空", "异常传播到enqueue，未进入正文校验或写入", "原代码不自动转字符串或吞掉异常。", 'raise ValueError("key must be text")'),
        ],
        "body": [
            method("body-type", "isinstance(text, str)", "return", "原始正文对象", "bool：是否str", "or短路：非文本时不调用strip。", "if not isinstance(text, str) or not text.strip():"),
            method("body-strip", "str.strip()", "return", "已确认是字符串的text", "仅用于空白检查的str；text局部仍保存原值", "检查与最终存储是两次表达式求值。", "if not isinstance(text, str) or not text.strip():"),
            method("body-stop", "ValueError(str)，由NoteQueue.enqueue抛出", "stop", "非字符串或仅空白正文", "异常，去重与容量未执行；状态保留", "非法重复请求也要先拒绝。", 'raise ValueError("text must not be blank")'),
        ],
        "duplicate": [
            method("duplicate-result", "dict.__contains__(key)（in 成员检查）", "return", "key与当前_pending", "bool：键是否正在待处理", "这是字典成员查询，不是数据库访问。", "if key in self._pending:"),
            method("duplicate-return", "NoteQueue.enqueue(raw_key: str, text: str) -> bool", "stop", "key确实已在_pending", "提前False；不检查容量，不覆盖旧正文", "有效重复与非法输入是不同结果。", "return False"),
        ],
        "capacity": [
            method("pending-length", "len(self._pending)", "return", "当前待处理字典", "int条数，与_capacity比较", "只对新键检查容量。", "if len(self._pending) >= self._capacity:"),
            method("capacity-stop", "OverflowError(str)，由NoteQueue.enqueue抛出", "stop", "当前条数已经达到容量", "写入前抛异常，不插入新键", "本例不自动淘汰旧任务。", 'raise OverflowError("pending queue is full")'),
        ],
        "store": [
            method("stored-strip", "str.strip()", "return", "已通过检查的原始正文", "保存时去两端空白后的正文", "这次结果会写入任务字典。", 'self._pending[key] = {"key": key, "text": text.strip()}'),
            method("enqueue-return", "NoteQueue.enqueue(raw_key: str, text: str) -> bool", "return", "任务字典已写入_pending", "bool True交还调用者", "只有全部前置条件通过才改变状态。", "return True"),
        ],
        "read": [
            method("snapshot-enter", "NoteQueue.snapshot() -> list[dict[str,str]]", "call", "调用者另发起读取；enqueue已返回", "开始构造新列表", "snapshot不是enqueue内部调用。", "def snapshot("),
            method("pending-values", "dict.values()", "return", "当前_pending", "按插入顺序的任务值视图", "视图供列表推导式读取，不返回给外部。", "return [dict(job) for job in self._pending.values()]"),
            method("job-copy", "dict(job)", "return", "每条任务字典", "新任务字典；字符串值可共享", "逐个复制元素容器；不保证嵌套可变对象隔离。", "return [dict(job) for job in self._pending.values()]"),
            method("snapshot-return", "NoteQueue.snapshot() -> list[dict[str,str]]", "return", "新列表内装新字典", "副本列表交还调用者", "读取不删除待处理任务。", "return [dict(job) for job in self._pending.values()]"),
        ],
        "empty": [
            method("take-enter", "NoteQueue.take() -> dict[str,str] | None", "call", "此刻_pending状态", "先判断是否为空", "空检查安排在next之前。", "def take("),
            method("empty-return", "NoteQueue.take() -> dict[str,str] | None", "stop", "空队列", "None提前返回，iter/next/pop均未调用", "正常无任务，不是异常或工作完成。", "if not self._pending:"),
        ],
        "oldest": [
            method("key-iterator", "iter(self._pending)", "return", "非空待处理字典", "按插入顺序遍历键的迭代器", "顺序是加入时间，不是字母排序。", "oldest_key = next(iter(self._pending))"),
            method("first-key", "next(iterator)", "return", "上一步的键迭代器", "首个键绑定oldest_key", "先判断非空以免StopIteration。", "oldest_key = next(iter(self._pending))"),
        ],
        "remove": [
            method("pending-pop", "dict.pop(oldest_key)", "return", "当前首键", "删除该键并返回内部任务字典", "这一步已经释放键与一个容量。", "return dict(self._pending.pop(oldest_key))"),
            method("taken-copy", "dict(job)", "return", "pop刚返回的任务字典", "为外部新建一份字典副本", "复制发生在删除之后；没有处理中存储。", "return dict(self._pending.pop(oldest_key))"),
            method("take-return", "NoteQueue.take() -> dict[str,str] | None", "return", "新任务字典副本", "交还调用者，内部已无该键", "取出不是成功确认，工作者未在此实现。", "return dict(self._pending.pop(oldest_key))"),
        ],
    }
    for flow in bundle["flows"]:
        for step in flow["steps"]:
            base = step["id"].removeprefix("re-")
            base = "read" if base == "snapshot" else base
            step["methods"] = [dict(m) for m in definitions[base]]
        by_step = {s["id"]: s for s in flow["steps"]}
        for case in flow["trace_cases"]:
            expanded = []
            for old in case["frames"]:
                sid = old["step_id"]
                base = sid.removeprefix("re-")
                base = "read" if base == "snapshot" else base
                methods = by_step[sid]["methods"]
                if base == "normalize":
                    ids = ["enqueue-enter", "key-enter", "key-type", "key-strip", "key-casefold", "key-return"]
                    if case["id"] == "invalid-key":
                        ids = ["enqueue-enter", "key-enter", "key-type", "key-stop"]
                elif base == "body":
                    ids = ["body-type", "body-strip"] + (["body-stop"] if case["id"] == "invalid-duplicate-body" else [])
                elif base == "capacity":
                    ids = ["pending-length"] + (["capacity-stop"] if case["id"] == "new-key-full" else [])
                elif base == "empty":
                    ids = ["take-enter"] + (["empty-return"] if case["id"] == "empty" else [])
                elif base == "duplicate":
                    ids = ["duplicate-result"] + (["duplicate-return"] if case["id"] == "duplicate-full" else [])
                else:
                    ids = [m["id"] for m in methods]
                for mid in ids:
                    m = next(m for m in methods if m["id"] == mid)
                    frame = {**old, "method_id": mid, "explanation": m["why"] + " " + old["explanation"]}
                    if base == "normalize" and mid in {"enqueue-enter", "key-enter", "key-type", "key-strip", "key-casefold"}:
                        raw = "42" if case["id"] == "invalid-key" else ('" B "' if sid.startswith("re-") else '" A "' if case["id"] in {"success", "duplicate-full"} else '"b"' if case["id"] == "new-key-full" else '"a"')
                        frame["values"] = [{"name": "raw_key", "type": "int" if raw == "42" else "str", "value": raw, "source": "本案例入口原始参数"}]
                        if mid == "key-type":
                            frame["values"].append({"name": "isinstance(raw,str)", "type": "bool", "value": "False" if raw == "42" else "True", "source": "类型检查返回"})
                        elif mid == "key-strip":
                            frame["values"].append({"name": "strip_result", "type": "str", "value": repr(raw.strip('"').strip()), "source": "str.strip返回"})
                        elif mid == "key-casefold":
                            frame["values"].append({"name": "normalized", "type": "str", "value": repr(raw.strip('"').strip().casefold()), "source": "str.casefold返回；尚未回到enqueue赋值"})
                    elif mid == "duplicate-result":
                        frame["values"] = [{"name": "key in self._pending", "type": "bool", "value": "True" if case["id"] == "duplicate-full" else "False", "source": "字典成员检查返回"}]
                    elif mid == "take-enter":
                        frame["values"] = [{"name": "self._pending", "type": "dict[str,dict[str,str]]", "value": "{}" if case["id"] == "empty" else '{"b":{"key":"b","text":"First"},"a":{"key":"a","text":"Second"}}', "source": "进入take前已给定的实例状态"}]
                    elif base == "read" and mid != "snapshot-return":
                        result_value = next(v["value"] for v in old["values"] if v["name"] == "return")
                        result_items = json.loads(result_value)
                        if mid == "job-copy" and not result_items:
                            continue
                        if mid == "snapshot-enter":
                            frame["values"] = [{"name": "self._pending", "type": "dict[str,dict[str,str]]", "value": json.dumps({v["key"]: v for v in result_items}, ensure_ascii=False), "source": "调用snapshot之前的内部状态"}]
                        elif mid == "pending-values":
                            frame["values"] = [{"name": "jobs_view", "type": "dict_values", "value": result_value, "source": "_pending.values返回的任务值视图，尚未复制"}]
                        elif mid == "job-copy":
                            frame["values"] = [{"name": "job_copy", "type": "dict[str,str]", "value": json.dumps(result_items[0], ensure_ascii=False), "source": "dict(job)为第一条任务新建的字典；其他任务也逐个复制"}]
                    expanded.append(frame)
            case["frames"] = expanded
            skips = {"invalid-key": ["body", "duplicate", "capacity", "store", "read"],
                     "invalid-duplicate-body": ["duplicate", "capacity", "store", "read"],
                     "duplicate-full": ["capacity", "store", "read"], "new-key-full": ["store", "read"],
                     "empty": [s["id"] for s in flow["steps"] if s["id"] != "empty"]}
            if case["id"] in skips:
                case["skipped_step_ids"] = skips[case["id"]]

    def question(aid, cid, title, prompt, texts, answers, explanation, needle):
        bundle["activities"].append({"id": aid, "concept_id": cid, "kind": "predict", "title": title, "prompt": prompt,
                                     "options": [{"id": letter, "text": text} for letter, text in zip("abcdefgh", texts)], "answer_ids": answers,
                                     "rubric": ["依据当前实现选择完整正确集合"], "hints": ["先确认输入与当前_pending。", "按检查、状态变化、返回逐步判断。", "用合法成功和停止示例排除没有执行的过程。"],
                                     "explanation": explanation + " 这是该机制的回忆题，不证明独立实现。", "evidence": [anchor(needle)]})
    question("note-q13", "note-inputs", "类型提示与运行时键校验", '直接调用enqueue(42,"Note")。哪些判断正确？',
             ["参数注解不会在进入函数之前自动拦截整数", "_key的真实类型检查会拒绝整数并抛ValueError", "整数自动变成字符串并入队", "因为非法输入所以仍需要先写入再回滚"], ["a", "b"],
             "“注解不自动拦截”和“_key类型检查拒绝”正确。源码没有str转换；异常在任何状态写入之前发生，不需要虚构回滚。", "if not isinstance(raw, str):")
    question("note-q14", "note-dedup", "非法重复与有效重复的区别", '容量1已存a/First，调用enqueue("a"," ")。哪些判断正确？',
             ["键规范化成功", "正文检查先于去重并抛ValueError", "既有任务与容量占用保持不变", "重复检查返回False，所以正文不必校验"], ["a", "b", "c"],
             "键合法，随后空白正文在去重之前被拒绝，且写入没有发生。前三项正确；“重复先返回False”交换了源码检查顺序。", "if not isinstance(text, str) or not text.strip():")
    question("note-q15", "note-lifecycle", "当前队列的保证边界", "哪些能力能从当前源码得到依据？",
             ["待处理键去重", "固定容量写入前检查", "按插入顺序取出并释放键", "为当前字符串字段提供副本读取隔离", "崩溃后从数据库恢复任务并自动重试"], ["a", "b", "c", "d"],
             "前三项可从enqueue与take得到依据，副本隔离由snapshot的新列表和dict复制提供，当前字段是不可变字符串。“数据库恢复并重试”没有代码、状态或运行证据支持。", "class NoteQueue:")
    return bundle


if __name__ == "__main__":
    bundle = build_bundle()
    destination = ROOT / "learning-bundle.json"
    destination.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Created {destination.name}: {len(bundle['files'])} files, "
          f"{len(bundle['concepts'])} concepts, {len(bundle['activities'])} activities, {len(bundle['flows'])} flows.")
