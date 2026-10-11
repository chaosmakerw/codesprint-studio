# 安卓离线边界

拾阶使用独立包名 `io.shijie.learning`，不会读取“拾知”或其他应用的数据。所有页面、题目和讲义从 APK 本地资产加载，学习记录写入此应用自己的 IndexedDB。应用没有联网、广泛存储、定位、摄像头和麦克风权限。

原生壳把固定 HTTPS 来源映射到应用资产，并拒绝路径穿越及外部资源加载。`file://` 跨来源访问关闭；源码、讲义和导入 JSON 只能作为内容读取。外链由用户点击后交给系统浏览器，不在具有原生桥的 WebView 内打开。参考 [Android 官方本地内容说明](https://developer.android.com/develop/ui/views/layout/webapps/load-local-content) 与 [WebView 安全说明](https://developer.android.com/privacy-and-security/security-tips)。

JSON 备份导出与导入使用系统文件选择器，只访问用户选择的文档，不扫描手机磁盘。导出通过 `ACTION_CREATE_DOCUMENT`，导入通过 `ACTION_OPEN_DOCUMENT`。参考 [Android 官方文档选择器说明](https://developer.android.com/training/data-storage/shared/documents-files)。卸载应用前请手动导出备份；云自动备份与自动设备迁移已关闭。

本地首次构建会创建应用专属的签名身份，保存在 `android/private-signing/`，该目录不进入 Git、APK 或公开源码包。务必在本机安全备份它，以便后续用同一签名覆盖升级。丢失已有签名时构建必须停止，不创建冒充原应用的新身份。APK 公开证书只包含通用名称 `Shijie Offline`，不包含开发者个人姓名。

APK 资产内附应用自身的 `LICENSE` 与 `NOTICE`，保留 Apache-2.0 授权和原创版权说明。应用代码的授权不自动覆盖用户导入的项目、资料、答案或个人备份；公共版本只内置明确可分发的原创示例。

`build.py` 默认使用合法分发的原创示例种子。私用种子必须用 `--flavor personal --seed <本机文件>` 明确指定；构建不会自行读取个人桌面数据库。私用 APK 与公开示例 APK 的签名和包名相同，可以覆盖安装；已有本地数据由离线存储初始化规则保留。请不要把个人 APK、私用种子或导出的个人备份上传到公开仓库。

本机 SDK/JDK 路径由 `--sdk`、`ANDROID_SDK_ROOT`、`ANDROID_HOME` 和 `JAVA_HOME` 提供。构建输出、测试工具、签名资料均在 Git 忽略目录中。正式包关闭应用调试与 WebView 远程调试；临时 `--debug` 包仅用于指定的测试模拟器。
