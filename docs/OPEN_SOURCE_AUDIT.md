# 开源范围、版权与隐私审计

本项目由 **chaosmakerw** 维护，是一次公开记录的 Vibe Coding 实践。公开版发布通用的资料上传、AI 出题草稿审核和记忆练习工具，以及为此编写的原创界面、说明、短讲义和演示题。个人学习站与公开版分开保存，公开仓库不导入个人资料库、做题记录或旧站素材。

本文记录本轮采用的发布边界与一手资料依据。它不是律师意见，不是“所有 AI 输出具有独占版权”或“绝对不会侵权”的保证；最终发布内容仍需核对实际 Git 提交与发布附件。

## 1. 项目许可证与作者权益

项目中可由维护者授权的原创代码和原创文档使用 **Apache License 2.0**，完整条款见根目录 `LICENSE`，署名见 `NOTICE`。开源授予使用、修改与分发许可，不转移作者现有版权。该许可允许商业使用，不能再同时要求所有人“禁止商用”或“修改必须闭源”；重新分发时应遵守许可证、保留适用署名，并标明修改。[Apache 官方条款第 2、4 节](https://www.apache.org/licenses/LICENSE-2.0)

Apache-2.0 不授予以项目名称、商标或作者身份为他人产品背书的许可。下游可以按实际情况说明来源，不应冒充本项目作者或官方发布。[Apache 官方条款第 6 节](https://www.apache.org/licenses/LICENSE-2.0)

公开仓库会被查看、Fork 与克隆，之后删除仓库不能收回已经分发的副本。需要保密的实现与材料应留在私有范围；“开源”与“别人无法复制”无法同时承诺。[GitHub 仓库许可说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)

公开发布也不意味着能够禁止所有抓取或模型训练。GitHub 当前服务条款包含对用户内容用于提供、开发与改进服务及 AI 的许可，Apache-2.0 本身没有“禁止 AI 训练”限制。不能在 README 中宣称本许可能够阻止这些使用。[GitHub 服务条款 D.4、D.8](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service)

提交历史、公开用户名和项目说明可以记录创作与发布过程，但不会自动完成商标注册、专利申请或司法意义上的权属认定。对确有争议的商业授权，应另行核实。

## 2. 原个人站内容的发布边界

| 内容 | 本次公开版处理 | 原因 |
| --- | --- | --- |
| 原站游戏背景、人物、美术、官方标识、音乐 | 不复制，不打包 | 没有取得公开再分发授权；参考风格或署名来源不等于获得素材许可 |
| 原站音乐文件与项目录屏 | 不复制，不作为 Release 附件 | 音乐授权、画面和元数据可能涉及他人权利或个人信息 |
| 原学习计划、课程、项目文档、进度 | 不导入 | 属于个人学习上下文，与通用公开工具分离 |
| 332 份原资料、1,400 道原题、个人 SQLite、备份 | 不导入 | 包含第三方原文、个人上传或来源约束，不能用项目许可证一并重新授权 |
| JavaGuide、Microsoft、LangChain4j 等外部课程原文 | 不随仓库发布 | 即使上游许可允许，仍需逐文件核对版权、NOTICE、图片和例外条款；本版不进行原文再分发 |
| LeetCode 原题、牛客面经、其他网站文章 | 不随仓库发布 | 网站可访问、星数高或适合教学，均不自动代表允许整篇再分发 |
| 原站字体、图标、Markdown 与图表 vendor | 本版不复制 | 新界面使用系统字体、原创 CSS 和原生 JavaScript，无需继承这些分发包 |
| 新写的 Java 短讲义与演示题 | 可纳入 | 为本项目编写，独立表述，不复制外部原题或文章 |

上述排除是公开版的发布策略，不删除或判断个人站已有内容的权利状态。公开 UI 使用原创构图与配色，不宣称与游戏公司存在授权、合作或官方关联。GitHub 要求上传者确认有权公开第三方内容，项目的 Apache-2.0 无法覆盖不属于作者的材料。[GitHub 服务条款 D.3–D.5](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service)

## 3. 第三方依赖检查

本版的目标实现使用 Python 标准库与浏览器原生 HTML/CSS/JavaScript，**不复制旧站的第三方前端分发包**。当前第三方声明见 `THIRD_PARTY_NOTICES.md`；若未来新增包、字体、图标或示例文章，应同时更新实际版本、源地址、适用许可证与必要许可全文。

本轮为判断旧资产是否应继承，检查了以下本地许可文件与上游许可。它们不属于本版已打包依赖清单：

| 旧站组件 | 核对到的许可 | 后续引入时应注意 |
| --- | --- | --- |
| markdown-it 15.0.2 | MIT | 保留版权与 MIT 许可全文，并核对实际浏览器 bundle 内包含的依赖 |
| DOMPurify 3.4.16 | MPL-2.0 或 Apache-2.0 | 按上游双许可实际选择一套并履行义务，保留 Cure53 与贡献者信息 |
| Mermaid 12.0.0 | 旧站随包 MIT 许可 | 本次不分发；重新引入时重新核验对应精确版本及其 bundle 内依赖 |
| Lucide 0.468.0 | ISC，含来源于 Feather 的 MIT 部分 | 保留两部分适用署名与许可，不能仅写“图标免费” |
| Barlow / 站酷庆科黄油体 | SIL OFL 1.1 | 字体文件仍按 OFL，修改或子集还应核对保留名称与再分发要求 |

依据：[markdown-it 15.0.2 LICENSE](https://github.com/markdown-it/markdown-it/blob/15.0.2/LICENSE)、[DOMPurify 3.4.16 LICENSE](https://github.com/cure53/DOMPurify/blob/3.4.16/LICENSE)、[Mermaid 上游 LICENSE](https://github.com/mermaid-js/mermaid/blob/develop/LICENSE)、[Lucide 上游 LICENSE](https://github.com/lucide-icons/lucide/blob/main/LICENSE)、[SIL OFL 官方条款](https://openfontlicense.org/open-font-license-official-text/)。Mermaid 与 Lucide 上游链接用于说明许可证来源，不代替对未来发布精确版本的核验。

Python、操作系统、浏览器、GitHub 和可选模型供应商是独立产品，它们有自己的条款。用户安装这些产品或调用远程服务，不意味着它们被重新授权为本项目的 Apache-2.0 代码。[Python 官方许可说明](https://docs.python.org/3/license.html)

## 4. 上传资料与 AI 出题的权利边界

使用者上传的资料、题库、答案、备份与模型输出**不会因为使用此工具自动被项目许可证授权或公开到 GitHub**。使用者应有权保存、处理和向所选模型供应商发送材料；公司内部资料、个人隐私、考试保密题目和未获授权内容不应直接提交给第三方模型。

出题流程应明确区分“模型草稿”和“人工审核写入”。生成约束至少包含：基于上传资料、标明证据片段或位置、独立改写、不照抄原题、不引入资料外断言、面试常见情境、适中难度、每个干扰项有理由、答案集合唯一、逐项解析、版本/环境前提、证据不足时不出题。结构校验能拦截格式错误，不能证明所有技术事实、版权来源与答案都正确。

维护者应在审核时检查出处、正确答案、歧义、原文相似度和难度，AI 生成后不直接把草稿作为已验证真题发布。AI 协助开发应如实展示人工需求、设计、审阅与验证过程；不声称所有代码均由人手写，也不把使用模型等同于获得全部独占权。AI 输出的可版权性取决于适用法与实际人类创作贡献，许可文本不能凭空制造版权。[美国版权局 AI 与版权专题及 Part 2 报告](https://www.copyright.gov/ai/)

上述美国资料用于说明“AI 输出不自动等于完全独占产权”的边界，不是对所有国家法律的推定。使用模型时还应检查所选供应商的输入、输出、数据保留与隐私条款。

## 5. 公开前的隐私检查

发布应从新公开版目录建立独立 Git 历史，避免把整个个人工作区的提交历史带入仓库。检查对象应包含**实际已暂存文件、提交历史、截图、压缩包和 Release 附件**，不能只检查当前源码目录。

发布前核对以下项目：

- 没有个人电脑用户名、绝对工作区路径、主机名、私人邮箱、手机号、内网地址或学习记录。
- 没有数据库、用户上传、JSON 备份、模型输入输出日志、浏览器配置或个人录屏。
- 没有 API Key、JWT、GitHub Token、环境变量实值、签名私钥、Android keystore 或 SDK 下载缓存。
- 示例环境文件只含占位符；运行数据、私密配置和测试临时产物有明确忽略规则。
- Git 作者只使用公开账号名和其 GitHub noreply 邮箱，不继承个人电脑上的私人邮箱。[GitHub 提交邮箱说明](https://docs.github.com/en/account-and-profile/how-tos/email-preferences/setting-your-commit-email-address)
- AI 模型密钥由本机服务端环境配置读取，不写进前端包、导出备份、日志或仓库。
- 截图只展示原创示例数据与应用区域，不展示桌面路径、浏览器账户或其他软件。
- 测试通过不代表隐私检测覆盖所有秘密格式；仍需人工查看最终文件清单。

泄露凭据时首先撤销或轮换，单纯删除文件或追加 `.gitignore` 无法消除旧提交、Fork 与克隆中的内容。GitHub Secret Scanning 可补充检测，但不能代替发布前检查。[GitHub 敏感数据清理说明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)、[GitHub Secret Scanning](https://docs.github.com/en/code-security/concepts/secret-security/secret-scanning)

## 6. 举报、贡献与后续维护

发现错误题目、出处遗漏或权利疑问时，请提供具体文件/题目 ID、来源与问题说明。公开 Issue 不应粘贴密钥、私人资料或他人个人信息。贡献者应确认有权提交其代码、文档和示例；外部原文和素材不能用一句“仅供学习”代替授权。

版权投诉应依据具体作品与授权事实处理。项目维护者可先移除或替换存在争议的材料，再核对原始许可；对已分发内容的处理不能只修改主页声明。GitHub 有专门的版权投诉流程，版权、商标与敏感数据问题使用不同程序。[GitHub DMCA 政策](https://docs.github.com/en/site-policy/content-removal-policies/dmca-takedown-policy)
