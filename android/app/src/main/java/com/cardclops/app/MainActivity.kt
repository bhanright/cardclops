package com.cardclops.app

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.View
import android.view.WindowManager
import android.webkit.ConsoleMessage
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.net.toUri
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import org.json.JSONObject
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import kotlin.math.max

/**
 * The whole app: a loading screen while the engine starts, then the gallery's own web page in a
 * WebView. The page is the same one the desktop app shows; everything Android-specific (files,
 * links, sharing, the back button, keeping the screen on during long jobs) is handled here.
 */
class MainActivity : ComponentActivity() {

    private lateinit var root: View
    private lateinit var web: WebView
    private lateinit var splash: View
    private lateinit var splashStatus: TextView
    private lateinit var splashProgress: ProgressBar
    private lateinit var retryButton: Button

    private val background: ExecutorService = Executors.newSingleThreadExecutor()
    private val mainThread = Handler(Looper.getMainLooper())
    private var pageShown = false
    private var savedWebState: Bundle? = null
    private var resumed = false

    /** The page's pending <input type=file>; it must always be answered, or it never opens again. */
    private var fileCallback: ValueCallback<Array<Uri>>? = null

    private val openDocument = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        answerFileChooser(uri?.let { arrayOf(it) })
    }
    private val openDocuments = registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        answerFileChooser(uris.takeIf { it.isNotEmpty() }?.toTypedArray())
    }

    private val isDebuggable: Boolean
        get() = (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0

    override fun onCreate(savedInstanceState: Bundle?) {
        val backgroundColor = ContextCompat.getColor(this, R.color.bg)
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(backgroundColor),
        )
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        root = findViewById(R.id.root)
        web = findViewById(R.id.web)
        splash = findViewById(R.id.splash)
        splashStatus = findViewById(R.id.splash_status)
        splashProgress = findViewById(R.id.splash_progress)
        retryButton = findViewById(R.id.splash_retry)
        retryButton.setOnClickListener { startEngine() }

        // Draw edge to edge, but keep the page clear of the status bar, the navigation bar, a
        // display cutout and the keyboard: the page itself knows nothing about any of them.
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            val keyboard = insets.getInsets(WindowInsetsCompat.Type.ime())
            view.setPadding(bars.left, bars.top, bars.right, max(bars.bottom, keyboard.bottom))
            WindowInsetsCompat.CONSUMED
        }

        savedWebState = savedInstanceState?.getBundle(STATE_WEB)
            ?.takeIf { savedInstanceState.getInt(STATE_PORT) == Engine.port && Engine.port != 0 }
        configureWebView()
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = goBack(this)
        })
        startEngine()
    }

    // ---- starting ---------------------------------------------------------------------------

    private fun startEngine() {
        splashStatus.setText(R.string.starting)
        splashProgress.visibility = View.VISIBLE
        retryButton.visibility = View.GONE
        val started = System.nanoTime()
        background.execute {
            try {
                val port = Engine.start(this)
                Log.i(TAG, "Engine on port $port after ${(System.nanoTime() - started) / 1_000_000} ms")
                mainThread.post { if (!isDestroyed) showPage() }
            } catch (error: Throwable) {
                Log.e(TAG, "Engine failed to start", error)
                mainThread.post { if (!isDestroyed) showStartFailure(error) }
            }
        }
    }

    private fun showStartFailure(error: Throwable) {
        splashProgress.visibility = View.GONE
        val detail = (error.message ?: error.javaClass.simpleName).lineSequence().take(6).joinToString("\n")
        splashStatus.text = getString(R.string.start_failed, detail)
        retryButton.visibility = View.VISIBLE
    }

    private fun showPage() {
        val cookies = CookieManager.getInstance()
        cookies.setAcceptCookie(true)
        cookies.setAcceptThirdPartyCookies(web, false)
        // Cookies ignore the port, so this also replaces any previous launch's token.
        val cookie = "${Engine.TOKEN_COOKIE}=${Engine.token}; Path=/; HttpOnly; SameSite=Strict"
        cookies.setCookie(Engine.origin, cookie) {
            cookies.flush()
            val saved = savedWebState
            savedWebState = null
            if (saved == null || web.restoreState(saved) == null) web.loadUrl("${Engine.origin}/")
        }
    }

    /** The first page load is done: swap the loading screen for the page. */
    private fun revealPage() {
        if (pageShown) return
        pageShown = true
        web.visibility = View.VISIBLE
        splash.animate().alpha(0f).setDuration(180).withEndAction { splash.visibility = View.GONE }.start()
        pollJobs()
    }

    // ---- the WebView ------------------------------------------------------------------------

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        WebView.setWebContentsDebuggingEnabled(isDebuggable)
        web.setBackgroundColor(ContextCompat.getColor(this, R.color.bg))
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            // No file:// access: the page comes from the engine. Content access stays on because
            // the document picker hands the page content:// URIs that its FileReader must read.
            allowFileAccess = false
            allowContentAccess = true
            setSupportMultipleWindows(false)     // target=_blank links come to shouldOverrideUrlLoading
            javaScriptCanOpenWindowsAutomatically = false
            setGeolocationEnabled(false)
            mediaPlaybackRequiresUserGesture = true
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
                handleNavigation(request.url)

            override fun onPageFinished(view: WebView, url: String) {
                if (isEngineUrl(url.toUri())) revealPage()
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                webView: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams,
            ): Boolean = showFileChooser(callback, params)

            override fun onConsoleMessage(message: ConsoleMessage): Boolean {
                if (isDebuggable) Log.d(TAG, "page: ${message.message()} (${message.sourceId()}:${message.lineNumber()})")
                return true
            }
        }
        web.setDownloadListener { url, _, _, mimeType, _ ->
            val uri = url.toUri()
            if (isEngineUrl(uri) && mimeType.orEmpty().startsWith("text/")) shareFromEngine(uri)
            else if (!isEngineUrl(uri)) openExternally(uri)
        }
    }

    private fun isEngineUrl(uri: Uri): Boolean =
        uri.scheme == "http" && uri.host == "127.0.0.1" && Engine.port != 0 && uri.port == Engine.port

    /** true when the WebView should not load [uri] itself. */
    private fun handleNavigation(uri: Uri): Boolean {
        if (isEngineUrl(uri)) {
            // "Missing list as text" and other plain-text exports: share them instead of showing
            // a bare text page with no way back but the back button.
            if (uri.path.orEmpty().endsWith(".txt")) {
                shareFromEngine(uri)
                return true
            }
            return false
        }
        openExternally(uri)
        return true
    }

    /** Scryfall, TCGplayer, Archidekt and every other outside link open in the browser. */
    private fun openExternally(uri: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE))
        } catch (_: ActivityNotFoundException) {
            Toast.makeText(this, R.string.no_app_for_link, Toast.LENGTH_SHORT).show()
        }
    }

    /** Fetches a text file from the engine (with the token) and offers it to the share sheet. */
    private fun shareFromEngine(uri: Uri) {
        val path = uri.encodedPath.orEmpty() + (uri.encodedQuery?.let { "?$it" } ?: "")
        val name = uri.lastPathSegment ?: "cardclops.txt"
        background.execute {
            val text = try {
                Engine.get(path)
            } catch (error: Exception) {
                Log.w(TAG, "Couldn't fetch $path", error)
                null
            }
            mainThread.post {
                if (isDestroyed) return@post
                if (text == null) {
                    Toast.makeText(this, R.string.download_failed, Toast.LENGTH_SHORT).show()
                    return@post
                }
                val send = Intent(Intent.ACTION_SEND)
                    .setType("text/plain")
                    .putExtra(Intent.EXTRA_SUBJECT, name)
                    .putExtra(Intent.EXTRA_TITLE, name)
                    .putExtra(Intent.EXTRA_TEXT, text)
                startActivity(Intent.createChooser(send, getString(R.string.share_list)))
            }
        }
    }

    // ---- choosing files ---------------------------------------------------------------------

    private fun showFileChooser(callback: ValueCallback<Array<Uri>>, params: WebChromeClient.FileChooserParams): Boolean {
        answerFileChooser(null)                  // a chooser left open by an earlier request
        fileCallback = callback
        val mimeTypes = mimeTypesFor(params.acceptTypes)
        return try {
            if (params.mode == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE) openDocuments.launch(mimeTypes)
            else openDocument.launch(mimeTypes)
            true
        } catch (_: ActivityNotFoundException) {
            fileCallback = null
            false
        }
    }

    private fun answerFileChooser(uris: Array<Uri>?) {
        fileCallback?.onReceiveValue(uris)
        fileCallback = null
    }

    /**
     * The page's accept="..." (".csv,text/csv,.txt", ".txt,.dek,.dck,.csv,...") as MIME types for
     * the document picker. Phones label CSV and deck files inconsistently (text/csv,
     * text/comma-separated-values, application/vnd.ms-excel, or application/octet-stream for a
     * .dek), so each extension maps to every type it is seen under.
     */
    private fun mimeTypesFor(acceptTypes: Array<String>?): Array<String> {
        val accepted = acceptTypes.orEmpty().flatMap { it.split(',') }.map { it.trim().lowercase() }.filter { it.isNotEmpty() }
        if (accepted.isEmpty()) return arrayOf("*/*")
        val types = linkedSetOf<String>()
        for (entry in accepted) {
            when (entry) {
                ".csv", "text/csv" -> types += listOf("text/csv", "text/comma-separated-values", "application/csv", "application/vnd.ms-excel")
                ".txt", "text/plain" -> types += "text/plain"
                ".dek", ".dck" -> types += listOf("text/plain", "application/octet-stream", "application/xml", "text/xml")
                else -> if (entry.contains('/')) types += entry
            }
        }
        types += "text/*"
        return types.toTypedArray()
    }

    // ---- back, lifecycle --------------------------------------------------------------------

    /** Back closes an open card or dialog first, then walks the page's own history, then leaves. */
    private fun goBack(callback: OnBackPressedCallback) {
        if (!pageShown) return leave(callback)
        web.evaluateJavascript(CLOSE_OVERLAY_SCRIPT) { result ->
            when {
                result == "true" -> Unit
                web.canGoBack() -> web.goBack()
                else -> leave(callback)
            }
        }
    }

    private fun leave(callback: OnBackPressedCallback) {
        callback.isEnabled = false
        onBackPressedDispatcher.onBackPressed()   // the system's default: to the home screen
        callback.isEnabled = true
    }

    override fun onResume() {
        super.onResume()
        resumed = true
        web.onResume()
        if (pageShown) pollJobs()
    }

    override fun onPause() {
        resumed = false
        web.onPause()
        super.onPause()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (pageShown) {
            outState.putBundle(STATE_WEB, Bundle().also { web.saveState(it) })
            outState.putInt(STATE_PORT, Engine.port)
        }
    }

    override fun onDestroy() {
        mainThread.removeCallbacksAndMessages(null)
        answerFileChooser(null)
        background.shutdown()
        web.destroy()
        super.onDestroy()
    }

    /**
     * While a download, import or refresh runs (the first-run setup takes about two minutes), keep
     * the screen on so the phone doesn't sleep and let Android stop the app halfway through.
     */
    private fun pollJobs() {
        mainThread.removeCallbacks(pollRunnable)
        mainThread.post(pollRunnable)
    }

    private val pollRunnable: Runnable = object : Runnable {
        override fun run() {
            if (!resumed || isDestroyed) return
            background.execute {
                val running = try {
                    !JSONObject(Engine.get("/api/setup/progress", 5_000)).optBoolean("done", true)
                } catch (_: Exception) {
                    false
                }
                mainThread.post {
                    if (isDestroyed) return@post
                    if (running) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                    else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                    if (resumed) mainThread.postDelayed(this, if (running) 3_000L else 10_000L)
                }
            }
        }
    }

    private companion object {
        const val TAG = "Cardclops"
        const val STATE_WEB = "web"
        const val STATE_PORT = "port"

        /** Closes the card viewer or a dialog the way Escape does; answers whether one was open. */
        const val CLOSE_OVERLAY_SCRIPT = """(function () {
            var open = document.body.classList.contains('modal-open') || document.querySelector('.dialog');
            if (!open) return false;
            document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true}));
            return true;
        })()"""
    }
}
