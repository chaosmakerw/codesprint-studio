# Third-Party Notices / 第三方声明

拾知与拾阶（CodeSprint Studio）的原创代码和原创文档由 chaosmakerw 及后续贡献者按根目录 `LICENSE` 中的 Apache License 2.0 授权。

## 本版分发范围

本版使用 Python 标准库、浏览器原生 HTML/CSS/JavaScript 与系统字体，未复制或分发第三方前端包、图标集、字体文件、图片、音乐、游戏素材或外部课程原文。资料阅读展示文本及 Markdown 原文，不执行上传内容中的 HTML。

仓库中的演示讲义和演示题为本项目独立编写。个人学习站的资料库、既有题库、答题记录、课程、音乐与录屏不属于此公开仓库，也不属于本项目许可证的授权范围。

## 独立产品与用户内容

- Python、操作系统与浏览器按各自许可与条款提供，本项目不重新授权这些产品。
- 可选远程模型与 GitHub 服务按各自服务、隐私和数据使用条款提供。调用服务不表示本项目获得该供应商的商标或产品授权。
- 使用者上传的文档、生成的题目、做题记录、备份和配置属于运行数据，不会因使用本项目自动采用 Apache-2.0 或进入公开仓库。使用者应确认自己的处理及传播权限。

## 新增依赖时

任何后续新增且随项目分发的第三方代码、素材或文档，都应在此记录组件名、精确版本、来源、许可证、版权署名与必要许可全文。根目录 Apache-2.0 只适用于维护者有权许可的原创部分，不覆盖第三方自身许可证。

完整发布边界与参考依据见 [开源范围、版权与隐私审计](docs/OPEN_SOURCE_AUDIT.md)。

## 拾阶安卓版

`apps/shijie` 中的 Java 本人订单与 Python 笔记任务队列为独立编写的原创教学实例；公开手机种子由这两份实例生成，合计 31 道客观题。私人项目、个人题库、学习记录与资料原件不在公共种子或公开 APK 中。

安卓壳为本仓库原创 Java 代码，运行时调用设备提供的 Android API 与系统 WebView，不在 APK 中另行分发浏览器引擎、Android 系统或 Android SDK。WebView 的使用方式见 [Android 官方说明](https://developer.android.com/develop/ui/views/layout/webapps/webview)。网页、系统字体、图标、讲义与题目保持原生或原创实现，没有打包游戏素材、第三方字体或外部课程原文。

构建使用官方 Android SDK 的 android-36 与 build-tools 36.0.0，以及构建者自己的 JDK。SDK、JDK、模拟器、ADB 和签名工具由构建者另外安装，构建缓存与这些工具本身不进入公开源码或 APK 资产；它们遵循各自条款，Android SDK 见 [官方 SDK 许可协议](https://developer.android.com/studio/terms)。Apache-2.0 不重新授权这些独立工具或系统组件。

开发验收可使用 Node.js、Playwright、Edge 和 Android 模拟器；这些是开发环境能力，不属于日常运行依赖或手机 APK 随附内容。后续若新增实际随包分发的库或素材，应补充其精确版本、来源、许可与署名。
