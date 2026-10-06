# 本机 API 与数据边界

默认启动地址 `http://127.0.0.1:8768`，仅监听回环接口；不提供公网部署或多用户权限。Python 3.12 标准库，SQLite 和原件保存在 `data/`（可用 `--data-dir` 指定）。仓库默认空库，启动不会复制作者个人资料或答题记录。

`/` 默认打开今日计划；`/index.html` 是资料页，`/interview.html` 是记忆练习页。三个页面使用同一个本机数据目录。

## 请求约定

先 `GET /api/bootstrap` 获取 `{token,categories,topics,aiConfigured}`。`categories` 是中文字符串数组；`topics` 是 `{id,label}` 数组。所有 POST/PATCH/DELETE 带 `X-Archive-Token`。JSON 接口使用 `Content-Type: application/json`；上传、恢复例外。

Host 必须是本服务端口的 `127.0.0.1` 或 `localhost`；非同源 Origin、Referer 和跨站 Fetch 被拒绝。不返回跨域 CORS 响应。令牌是每次进程启动随机生成的本地防跨站令牌，不是互联网账号认证。

错误只返回 `{error,code}`，不返回堆栈、供应商响应、模型密钥或请求正文。常见状态是 400 输入无效、403 来源/令牌不符、404 内容不存在、502 AI 不可用、500 固定内部错误消息。

## 资料

| 方法与路径 | 输入与结果 |
| --- | --- |
| `GET /api/materials` | `search`、`category`、`favorite=true`、`trashed=true` 可选，返回 `{items,total}` |
| `GET /api/materials/{id}` | 返回详情，包含 `text_content`、`source_key`、`sha256`、`filename`、`category`、`tags`、`favorite`、`file_url`；回收站详情需 `?trashed=true` |
| `GET /api/materials/{id}/file` | 下载原始字节，安全附件响应，不把用户 HTML/SVG 当网页执行 |
| `POST /api/upload?filename=...&category=...&tags=...` | 原始字节，请 URL 编码中文参数；非空、每份 ≤50 MB，返回新资料，201 |
| `PATCH /api/materials/{id}` | 只允许 `title/category/tags/favorite/trashed`，不能改原件版本或来源标识 |
| `POST /api/demo/import` | `{confirmed:true}`，显式导入 6 篇原创示例 / 18 题；幂等，保留已有个人标题、收藏 |

可读文本提取支持 MD/TXT/Java/JSON/CSV/XML/YAML/代码等，UTF-8 或 GB18030，原件 ≤2 MB；其他格式可以存放下载。PDF 不在服务端自动 OCR/提取，AI 出题请上传自己有权使用的文本摘录。资料默认属于用户自己的本机数据，不因上传而授予本项目作者内容权利。

## AI 草稿

`POST /api/generation/drafts`：

```json
{
  "material_id": "资料ID",
  "topic": "concurrency",
  "count": 5,
  "keywords": ["volatile", "原子性", "共享变量"],
  "focus": ["mechanism", "scenario", "boundary"],
  "audience": "java-intern",
  "extra_instructions": "多用计数器和请求并发的常见场景",
  "consent": true
}
```

`topic`：`java/concurrency/spring/data/project/algorithm`；`count`：1—20；关键词：1—12 项；考察角度：`mechanism/scenario/boundary/debug/design`。`consent:true` 明确同意将所选资料正文、关键词及补充要求发送到自己配置的供应商。AI 可读正文最多 6 万字符，不截断后假装覆盖整篇。

返回持久化草稿 `{id,questions,warnings,request,source,quality,committed}`。每题有稳定 ID、4 个唯一选项、单/多选答案、机制解析、`option_explanations:{a,b,c,d}`、逐字 `evidence_quote`、关键词、考察角度、来源 SHA-256、题目版本。`quality.requires_human_review` 永远为 true。

`GET /api/generation/drafts/{id}` 获取草稿；`POST /api/generation/drafts/{id}/commit`，正文 `{confirmed:true}`，在用户核对后原子写入。返回 `{written,total,already_committed}`。重复提交写入 0 道；资料进入回收站、原件被改动、版本不一致、题干重复等均拒绝且不部分写库。

程序校验的是结构、长度、数量、关键词命中和引用原文存在，不证明答案语义正确、真实面试覆盖率或所有 AI 输出不存在第三方权利风险。资料中的命令仅作为不可信参考数据，系统提示不允许执行它们；这降低提示注入风险，不是对模型永不受影响的保证。

供应商仅通过服务端环境变量 `AI_BASE_URL/AI_MODEL/AI_API_KEY` 配置；支持 OpenAI 兼容 `POST /chat/completions` + JSON response format。实现按[官方 Chat Completions API 文档](https://developers.openai.com/api/reference/resources/chat)设计；其他兼容服务是否支持该参数须以自己的供应商文档为准。

连接仅允许公开 HTTPS/443：拒绝用户信息、query/fragment、内网/回环/保留地址，检查全部 DNS 返回地址，并将 TLS 连接固定到已验证 IP、保留 hostname 证书验证，避免重新解析。HTTP 重定向不跟随，API Key 不被转发到重定向站点。每实例最多同时 2 个生成任务；连接超时 45 秒、响应读取总截止约 60 秒、返回最多 2 MB。未配置模型时，仅出题禁用，资料和刷题照常工作。

## 刷题：保留原有规则

| 方法与路径 | 输入与结果 |
| --- | --- |
| `GET /api/quiz/catalog?material=...` | 可用题数、6 领域数量、已练/弱项/到期统计、未完轮、近期成绩 |
| `POST /api/quiz/sessions` | `{mode:"practice"|"exam",topic:""或领域,scope:"all"|"mixed"|"wrong"|"due",count:1..30,material:""或ID}` |
| `GET /api/quiz/sessions/{id}` | 恢复未完轮或历史结果 |
| `POST /api/quiz/sessions/{id}/answer` | `{question_id,selected:[稳定选项ID],guessed:false}` |
| `POST /api/quiz/sessions/{id}/finish` | `{}`，未答题按错误记入固定分母 |

单选和多选均要求答案集合完全相等；不能多选、少选换部分分。每轮题和显示选项随机排序，但选项 ID 保持不变。首答顺序不可跳过或改写，相同请求幂等。练习立即给解析，考试全部结束后再公布。解析编号按当前显示顺序生成，避免随机选项与 A/B/C/D 解析错位。

通过标准为 `correct * 100 >= total * 80`，分母是本轮抽取题数。猜对依旧进入弱项；错误/猜测 10 分钟后复习。到期答对按 1/3/7/14/30 天安排；提前答对不提升阶段。题目/来源版本过期时保留历史判分，但暂停该次复习更新；来源恢复后可继续练习。SQLite 写操作使用同实例锁，重启保留已提交首答与未完轮。

## 私有备份与恢复

`GET /api/backup` 下载 ZIP，包含资料原件、元数据、生成草稿、已写题库、轮次首答和复习状态；不包含 `.env` 或服务端配置。备份可能包含用户私密资料，请自己妥善保存，不应提交 GitHub。

`POST /api/restore?confirmed=true`，`Content-Type: application/zip`，原始 ZIP 字节、带 token。恢复替换当前全部库，应先下载备份。压缩包最多 128 MB，解压总量最多 256 MB。不解压任意路径：仅接受 `backup.json` 与 `blobs/<sha256>`。在临时目录完整验证所有原件 hash/正文、题库版本和来源、首答顺序和真实判分、复习计数、草稿要求及已写入关联后，再替换当前 SQLite；坏后段记录不会部分覆盖当前库。

本地数据默认不加密，备份文件同样不加密；这不是托管多人应用的访问控制方案。无真实模型密钥的测试只使用受控 Stub，不能证明任意供应商接入都成功。

## 学习计划：一个进度事实源

任务与资料、题库、练习轮次保存在同一 SQLite 中，不在浏览器建立第二套打卡数据。计划是自己的可执行安排；新任务和导入任务一律未完成，实际分钟为 0。只有用户明确勾选才更新完成状态；练习达到 80% 只说明本轮通过，不自动完成任务，也不代表已经独立掌握。

| 方法与路径 | 输入与结果 |
| --- | --- |
| `GET /api/planning/tasks` | 可选 `date=YYYY-MM-DD`、`status=all/pending/completed`、`search`，返回 `{items,total}`，日期为空查询全部 |
| `GET /api/planning/tasks/{id}` | 返回单个任务及真实练习聚合 |
| `POST /api/planning/tasks` | 至少 `{date,title}`；可带 `id/topic/material_id/notes/estimated_minutes`，201 |
| `PATCH /api/planning/tasks/{id}` | 修改 `date/title/topic/material_id/notes/estimated_minutes/actual_minutes/completed`；完成和恢复分别为 `{completed:true}`、`{completed:false}` |
| `GET /api/planning/summary?date=YYYY-MM-DD` | 日期缺省本机今日；返回 `{date,total,completed,pending,planned_minutes,actual_minutes,quiz}` |
| `POST /api/planning/import` | 下方计划 JSON；默认补充新任务，不覆盖已有编号，返回 `{added,skipped,total}` |

任务编号是 1—80 位小写字母、数字、短横线；不指定时新增接口生成随机编号。标题不超过 160 字、笔记不超过 6000 字；日期必须是有效的 `YYYY-MM-DD`；`topic` 为空或现有六领域之一；`material_id` 为空或已经上传、可用的资料 ID。预计分钟是 0—720 整数，实际分钟是 0—1440 整数；布尔值不能当作整数。最多保存 1000 项任务。

返回任务还包含 `created_at/updated_at/completed_at`、`material_title/material_available` 和 `quiz`。资料进入回收站或缺失时，计划仍保留，`material_available=false`；历史练习仍能查看。没有绑定资料时该字段为 true，用户可以自行选择资料。

`quiz` 为 `{sessions,finished,correct,total,percent,passed_sessions,recent}`，成绩分母只汇总已结束的真实关联轮次；无已结束轮次时 percent 为 null。`recent` 最多五条，包含原轮次公开状态和结果，不返回题目正文。当天 summary 汇总安排在该日期任务的关联练习，不把未关联的自由练习伪装成计划验收；`actual_minutes` 是用户填写值，不根据打开页面时间推算。

```json
{
  "schema": "shizhi-plan-v1",
  "confirmed": true,
  "tasks": [
    {
      "id": "day-01-java",
      "date": "2026-10-06",
      "title": "讲清集合的选择理由，再做一轮练习",
      "topic": "java",
      "material_id": "",
      "notes": "先脱离笔记口述；核对资料；写一个小例子；记录仍说不清的边界。",
      "estimated_minutes": 45
    }
  ]
}
```

每次导入 1—100 项，每项必须有稳定编号、日期和标题；禁止导入完成状态、实际分钟或练习成绩。兼容使用 `version:1` 代替 `schema`，两者不能同时提供。所有任务和引用先校验，再在同一事务中合并；重复编号跳过且保留原内容和进度；后段无效任务会拒绝整个文件，不写入前段。AI 只协助生成计划文件，不自动执行或标记完成，也无需配置站内 AI Key。

`POST /api/quiz/sessions` 可加 `task_id`：任务存在且已绑定领域/资料时，服务器采用它们作为本轮边界，冲突拒绝。未绑定资料的任务允许请求传 `material` 收窄本轮；未指定领域的任务也可传 `topic` 收窄。轮次公开结果附带 `task_id`。已有关联轮次的任务不能再修改领域或资料，其他字段仍可编辑；更换学习范围请新增任务，以保留可以核验的历史。任务当前资料不可用时拒绝开始关联练习，正常自由练习规则不变。

新备份使用 `codesprint-backup-v2`，包含 `study_tasks` 与轮次 `task_id`。恢复时完整检查每个任务字段、真实完成时间、资料引用、练习任务关联、领域/来源一致性，拒绝孤儿或伪造关联后才替换当前库。兼容旧 `codesprint-backup-v1` 的原五张表及八字段轮次，恢复后任务为空、原轮次关联为空；旧备份恢复同样替换全库，因此恢复前仍需先备份当前数据。旧 SQLite 数据目录启动时按列名迁移轮次新字段，不改变原首答和复习记录。
