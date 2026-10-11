# 学习包字段与本机协议

这份文件供生成包和写入时读取。教学顺序只在 `project-coach.md` 维护。使用UTF-8 JSON，输出声明式数据；包内路径是相对路径，保留实际源码和锚点。

## 完整包与补充包

完整包顶层：`schema_version:2`、`title`、`project:{name}`、`files:[{path,text}]`、`project_summary`、`concepts`、`activities`、`flows`。

已有项目补充包顶层：`schema_version:2`、`project_version`、`source_hash`、`title`、`project_summary`、`concepts`、`activities`、`flows`、`flow_updates`、`concept_updates`。版本和集合哈希原样取当前handoff；数组按需为空，新概念和活动用未占用ID。`flow_updates` 是已有完整flow对象的增强版，保留flow ID、原步骤及顺序；新功能写到flows。旧包省略新增可选字段仍兼容。

`concept_updates` 用于补齐已有知识讲义。每项提交该知识的完整对象，只能追加 `lesson/mechanism/failures/references`：前两项字符串保留原文作为前缀，后两项数组保留原列表作为前缀；ID、标题、分类、定义、设计理由、前置关系和证据原样保留。不要另建同名知识点或覆盖旧活动。预览绑定当时的知识原文；提交前原文已变化时整包拒绝，重新读取 handoff 后重新预览。

`project_summary` 字段：

```json
{"purpose":"人话项目用途","audience":"谁使用和适用前提","scenario":"一次具体输入到结果",
 "capabilities":["按用户目标归类的实际功能"],"learning_value":"适合学习哪些原理及原因",
 "limitations":"实际未覆盖范围、推演与实测界限","evidence":["实际锚点对象"]}
```

上面的字符串仅为格式示意，不能原样导入。

## 功能链

flow字段：`id/title/summary/scenario/input/expected/steps/branches/trace_cases/resources`。每链2–20步，最多12条链；冗长路径拆成互相指向的子流程，人话说明跨流程连接。

step字段：`id/title/actor/action/input/output/why/concept_ids/evidence`，可附 `methods`。每一步至少一个真实知识ID和一条当前锚点。

method字段：`id/signature/input/output/relation/why/evidence`。`signature`用真实方法签名或准确的接口→实际实现绑定；`relation` 为 `call`、`return`、`stop`，分别表示调用、返回、停止/异常。input/output解释类型与数据而不是整段源码，why写业务作用与边界。方法ID只需在当前步骤唯一，每方法至少一条实际锚点。

branch字段：`from_step_id/condition/outcome/skipped_step_ids`，写实际成立条件和跳过步骤。

trace_case字段：`id/title/input/result/frames`，可附 `skipped_step_ids`。frame字段：`step_id/explanation/values`，可附 `method_id` 引用同一步的真实方法ID。`values`每项严格为`{name,type,value,source}`，全部使用人类可读文本。Java Long可写`1001L`，未知值写“未知”，尚未正常返回的局部量写“尚未产生”，不当可执行表达式。帧按实际发生顺序；已返回对象与调用者接收分开，停止后不描写下游执行。

case显式skips只指真实未调用的步骤，不得同时出现这些步骤的frames；应与已经过步骤的branch一致，或由case里实际relation=stop的方法证明。正常案例不因缺帧填写skips。尚未补全的调用细节应在人话result中明说。

resource字段：`name/ownership/lifecycle/failure`。只记录源码存在的资源，无资源用空数组；包括集合、身份上下文、线程或连接等的所有者、共享、释放和失败后状态。

## 知识与题目

concept字段：`id/title/group/explanation/mechanism/why/failures/lesson/prerequisites/evidence/references`。lesson是完整人话讲义，不是代码目录；prerequisites指向当前或新增知识，无自引用与环。外部references为`[{title,url,note}]`，note记录实际访问日期、版本及使用范围；没有外部资料用空数组。

activity字段：`id/concept_id/kind/title/prompt/options/answer_ids/rubric/hints/explanation/evidence`，可附迁移等待的`due_after_hours`。kind仅为`predict|trace|debug|reproduce|transfer`。客观题options为2–8个`{id,text}`，正确集合存answer_ids；多个正确项按集合精确判分，至少一项干扰选项。开放题options与answer_ids均为空，结果待复核。hints是三个由浅到深的提示，rubric是可人工核对的条件。

最多80知识、8知识分类；完整包最多150活动，单次补充最多80活动，实际限额读manifest。用不同知识与情境保证题质量，避免仅替换数字和变量名制造重复。新生成的客观题通常至少4个选项；少于4项须有知识本身的理由，旧题不因此失效。解析逐项引用选项原文解释对错，不能依赖洗牌前的字母。先核对旧题已经覆盖的机制，再补追踪、排错、迁移取舍等缺口，不以题数作为合格标准。预览的质量提示逐项检查；新题有空解析、重复选项或全正确选项时先修复再提交。首次答案、提示与看过解释的记录分开，重答不能洗掉首答。

每个知识、活动、功能步骤和方法都绑定锚点：

```json
{"path":"project/Example.java","line":1,"quote":"这一行的实际原文","sha256":"实际UTF-8文本的64位SHA256"}
```

line从1开始，quote与这一行原文一致，sha256按包内实际text的UTF-8字节计算；换行改变后重新计算。引用结构通过只证明文本一致，语义答案仍应核对。

## 本机读写

1. `GET /api/agent-manifest` 发现当前协议；`GET /api/bootstrap` 取得项目列表与token。写请求用`X-Study-Token: <bootstrap.token>`。仅使用这次应用的`127.0.0.1`或`localhost`回环地址，token不写进学习包。
2. 已有项目先`GET /api/projects/<id>/handoff`，所有学习数据先`GET /api/backup`备份到应用data目录。本机路径不出现在分发学习包内。
3. 完整包`POST /api/bundles/preview`，body为`{"bundle":实际完整对象}`；读回draft_id，核对后`POST /api/bundles/<draft_id>/commit`，body为`{"confirmed":true}`。
4. 补充包`POST /api/projects/<id>/packs/preview`，body为`{"pack":实际补充对象}`；核对后`POST /api/projects/<id>/packs/<draft_id>/commit`，body为`{"confirmed":true}`。
5. confirmed表示本次用户已经授权这一步写入；它不是额外界面点击要求。未授权写入时停在可预览交付，授权自动准备时完成提交并核对项目detail及保留的旧记录。

接口与容量以当前manifest和server实际实现为准，错误应保持可见。升级准备失败可从备份恢复学习数据；不覆盖用户原项目。协议不自动建立模型账号或外部Agent网络连接。

## 补充资料来源模板

材料写明：来源类型、实际标题、URL、访问事实与日期、适用版本、原文使用范围，然后给原创概念/机制/项目对应/设计/错误/练习验收。没有联网时写未访问；无法访问的网页不称为已核实。短摘录与原创总结分开，不整篇搬运未获授权内容。
