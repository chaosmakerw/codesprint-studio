package io.shijie.learning;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.database.Cursor;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.OpenableColumns;
import android.util.Base64;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.MimeTypeMap;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Fixed HTTPS origin, packaged assets and user-selected SAF documents only. */
public final class MainActivity extends Activity {
    private static final String HOST = "appassets.androidplatform.net";
    private static final String HOME = "https://" + HOST + "/index.html";
    private static final int PICK_FILE = 3201, EXPORT_FILE = 3202;
    private static final int MAX_EXPORT_BYTES = 64 * 1024 * 1024;
    private static final int MAX_EXPORT_BASE64 = ((MAX_EXPORT_BYTES + 2) / 3) * 4;
    private WebView web;
    private FrameLayout root;
    private ValueCallback<Uri[]> fileCallback;
    private final ExecutorService files = Executors.newSingleThreadExecutor();
    private File pendingExport;
    private String exportName;
    private boolean exportBusy;

    @Override public void onCreate(Bundle savedState) {
        super.onCreate(savedState);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(246, 245, 237));
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(246, 245, 237));
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(
                    WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                root.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            } else {
                root.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            }
            return insets;
        });
        configureWeb();
        if (savedState == null || web.restoreState(savedState) == null) web.loadUrl(HOME);
    }

    @SuppressWarnings("deprecation") private void configureWeb() {
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true); // Granted by the document picker, never by a path from JS.
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSupportMultipleWindows(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setBuiltInZoomControls(false);
        CookieManager.getInstance().setAcceptCookie(false);
        WebView.setWebContentsDebuggingEnabled(
            (getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0);
        web.addJavascriptInterface(new OfflineBridge(), "ShijieNative");
        web.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                return localResponse(request.getUrl());
            }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, String url) {
                return localResponse(Uri.parse(url));
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (isLocal(request.getUrl())) return false;
                if (request.isForMainFrame()) openExternal(request.getUrl());
                return true;
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, String url) {
                Uri uri = Uri.parse(url);
                if (isLocal(uri)) return false;
                openExternal(uri);
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                                       FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                picker.addCategory(Intent.CATEGORY_OPENABLE);
                picker.setType("*/*");
                picker.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                String[] accepted = params.getAcceptTypes();
                if (accepted != null && accepted.length > 0 && accepted[0].contains("/"))
                    picker.putExtra(Intent.EXTRA_MIME_TYPES, accepted);
                picker.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                try { startActivityForResult(picker, PICK_FILE); }
                catch (ActivityNotFoundException error) {
                    callback.onReceiveValue(null); fileCallback = null;
                    toast("系统没有可用的文件选择器。");
                }
                return true;
            }
        });
        web.setDownloadListener((url, agent, disposition, mime, length) -> {
            if (url.startsWith("blob:"))
                web.evaluateJavascript("window.ShijieOffline&&window.ShijieOffline.downloadUrl&&"
                    + "window.ShijieOffline.downloadUrl(" + JSONObject.quote(url) + ")", null);
        });
    }

    private static boolean isLocal(Uri uri) {
        return "https".equalsIgnoreCase(uri.getScheme()) && HOST.equalsIgnoreCase(uri.getHost())
            && (uri.getPort() == -1 || uri.getPort() == 443) && uri.getUserInfo() == null;
    }

    private void openExternal(Uri uri) {
        if (!"https".equalsIgnoreCase(uri.getScheme()) && !"http".equalsIgnoreCase(uri.getScheme())) return;
        try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); }
        catch (ActivityNotFoundException error) { toast("手机未安装可打开该链接的浏览器。"); }
    }

    private WebResourceResponse localResponse(Uri uri) {
        if (!isLocal(uri)) return errorResponse(403, "Forbidden");
        String path = uri.getPath();
        if (path == null || path.equals("/")) path = "/index.html";
        if (path.indexOf('\\') >= 0 || path.indexOf('\0') >= 0) return errorResponse(403, "Forbidden");
        for (String segment : path.split("/"))
            if (segment.equals(".") || segment.equals("..")) return errorResponse(403, "Forbidden");
        if (path.endsWith("/")) path += "index.html";
        String extension = path.substring(path.lastIndexOf('.') + 1).toLowerCase(java.util.Locale.ROOT);
        String mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension);
        if (extension.equals("js") || extension.equals("mjs")) mime = "application/javascript";
        if (extension.equals("json")) mime = "application/json";
        if (extension.equals("md")) mime = "text/plain";
        if (extension.equals("woff2")) mime = "font/woff2";
        if (mime == null) mime = "application/octet-stream";
        Map<String,String> headers = new HashMap<>();
        headers.put("X-Content-Type-Options", "nosniff");
        headers.put("Referrer-Policy", "no-referrer");
        headers.put("Cache-Control", "no-cache");
        headers.put("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
            + "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; "
            + "object-src 'none'; frame-src 'none'; base-uri 'self'; form-action 'none'");
        try {
            InputStream content = getAssets().open("site" + path);
            String encoding = mime.startsWith("text/") || mime.contains("javascript") || mime.contains("json") ? "UTF-8" : null;
            return new WebResourceResponse(mime, encoding, 200, "OK", headers, content);
        } catch (IOException missing) { return errorResponse(404, "Not Found"); }
    }

    private static WebResourceResponse errorResponse(int code, String reason) {
        return new WebResourceResponse("text/plain", "UTF-8", code, reason, new HashMap<>(),
            new ByteArrayInputStream(reason.getBytes(StandardCharsets.UTF_8)));
    }

    public final class OfflineBridge {
        @JavascriptInterface public String getVersion() { return "0.4.1"; }
        @JavascriptInterface public void exportFile(String filename, String mime, String base64) {
            runOnUiThread(() -> {
                if (exportBusy) { exportResult(false, "", "已有文件正在保存，请完成后重试。"); return; }
                if (base64 == null || base64.length() > MAX_EXPORT_BASE64) {
                    exportResult(false, "", "文件过大，无法导出。"); return;
                }
                String safeName = filename == null ? "shijie-backup.json"
                    : filename.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]", "_");
                if (safeName.length() > 150) safeName = safeName.substring(0, 150);
                if (safeName.isEmpty() || safeName.equals(".") || safeName.equals("..")) safeName = "shijie-backup.json";
                final String selectedName = safeName;
                final String selectedMime = mime != null && mime.matches("[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+")
                    ? mime : "application/octet-stream";
                exportBusy = true; exportName = selectedName;
                files.execute(() -> {
                    try {
                        byte[] bytes = Base64.decode(base64, Base64.NO_WRAP);
                        if (bytes.length > MAX_EXPORT_BYTES) throw new IOException("File too large");
                        File temporary = File.createTempFile("shijie-export-", ".tmp", getCacheDir());
                        try (FileOutputStream out = new FileOutputStream(temporary)) { out.write(bytes); }
                        pendingExport = temporary;
                        runOnUiThread(() -> {
                            Intent picker = new Intent(Intent.ACTION_CREATE_DOCUMENT);
                            picker.addCategory(Intent.CATEGORY_OPENABLE);
                            picker.setType(selectedMime);
                            picker.putExtra(Intent.EXTRA_TITLE, selectedName);
                            picker.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                            try { startActivityForResult(picker, EXPORT_FILE); }
                            catch (ActivityNotFoundException error) { finishExport(false, "系统没有可用的保存位置选择器。"); }
                        });
                    } catch (Exception error) { runOnUiThread(() -> finishExport(false, "无法生成待保存文件，请检查剩余空间。")); }
                });
            });
        }
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == PICK_FILE && fileCallback != null) {
            ValueCallback<Uri[]> callback = fileCallback; fileCallback = null;
            Uri[] selected = WebChromeClient.FileChooserParams.parseResult(result, data);
            if (selected != null) {
                for (Uri uri : selected) {
                    if (uri == null || !"content".equals(uri.getScheme())) {
                        callback.onReceiveValue(null); toast("请选择系统文档提供器中的文件。"); return;
                    }
                }
            }
            callback.onReceiveValue(selected);
        } else if (request == EXPORT_FILE) {
            if (result != RESULT_OK || data == null || data.getData() == null || pendingExport == null) {
                finishExport(false, "已取消保存。"); return;
            }
            Uri uri = data.getData();
            if (!"content".equals(uri.getScheme())) { finishExport(false, "所选位置无法安全保存文件。"); return; }
            try (Cursor cursor = getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
                if (cursor != null && cursor.moveToFirst()) {
                    String actual = cursor.getString(0);
                    if (actual != null && !actual.isEmpty()) exportName = actual;
                }
            } catch (Exception ignored) { /* Requested name remains a safe fallback. */ }
            File source = pendingExport;
            files.execute(() -> {
                boolean success = false;
                try (InputStream input = new FileInputStream(source);
                     OutputStream output = getContentResolver().openOutputStream(uri, "w")) {
                    if (output == null) throw new IOException("No output stream");
                    byte[] buffer = new byte[64 * 1024]; int count;
                    while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                    output.flush(); success = true;
                } catch (Exception error) { /* Fixed message; no device paths or exception details. */ }
                final boolean saved = success;
                runOnUiThread(() -> finishExport(saved, saved ? "已保存到所选位置。" : "保存失败，请检查存储空间并重试。"));
            });
        }
    }

    private void finishExport(boolean success, String message) {
        if (pendingExport != null) { pendingExport.delete(); pendingExport = null; }
        exportBusy = false;
        exportResult(success, exportName == null ? "" : exportName, message);
    }

    private void exportResult(boolean success, String name, String message) {
        if (web == null) return;
        String detail = "{ok:" + success + ",name:" + JSONObject.quote(name) + ",message:" + JSONObject.quote(message) + "}";
        web.evaluateJavascript("window.dispatchEvent(new CustomEvent('shijie-native-export',{detail:" + detail + "}));", null);
        toast(message);
    }

    @Override public void onBackPressed() {
        web.evaluateJavascript("(()=>{const d=document.querySelector('dialog[open]');"
            + "if(d){if(d.dispatchEvent(new Event('cancel',{cancelable:true})))d.close();return true;}"
            + "if(window.ShijieOffline&&window.ShijieOffline.handleBack)"
            + "return !!window.ShijieOffline.handleBack();return false;})()", handled -> {
                if ("true".equals(handled)) return;
                if (web.canGoBack()) web.goBack(); else finish();
            });
    }

    @Override protected void onSaveInstanceState(Bundle state) { super.onSaveInstanceState(state); web.saveState(state); }
    @Override protected void onResume() { super.onResume(); if (web != null) web.onResume(); }
    @Override protected void onPause() { if (web != null) web.onPause(); super.onPause(); }
    @Override protected void onDestroy() {
        if (fileCallback != null) fileCallback.onReceiveValue(null);
        if (web != null) { root.removeView(web); web.removeJavascriptInterface("ShijieNative"); web.destroy(); web = null; }
        files.shutdown(); super.onDestroy();
    }
    private void toast(String text) { Toast.makeText(this, text, Toast.LENGTH_SHORT).show(); }
}
