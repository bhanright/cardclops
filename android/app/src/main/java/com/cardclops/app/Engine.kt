package com.cardclops.app

import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import androidx.core.content.pm.PackageInfoCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.SecureRandom

/**
 * Cardclops's Python engine, started once per process and shared by every activity instance.
 *
 * The engine (gallery/android.py) serves the web page and the JSON API on 127.0.0.1, on the same
 * port every launch when it is free: the WebView keeps the page's saved settings (theme, layout
 * choices) per origin, and the origin includes the port. Other apps on the phone can reach 127.0.0.1 too, so every request must carry [token], a
 * secret made fresh for each process: the WebView sends it as a cookie, this class as a header.
 */
object Engine {
    const val TOKEN_COOKIE = "cardclops_token"
    const val TOKEN_HEADER = "X-Cardclops-Token"
    private const val PREFS = "engine"
    private const val PORT_KEY = "port"

    /** 32 random bytes as hex; lives as long as the process, and so does the engine that checks it. */
    val token: String by lazy {
        val bytes = ByteArray(32).also { SecureRandom().nextBytes(it) }
        bytes.joinToString("") { "%02x".format(it) }
    }

    @Volatile
    var port: Int = 0
        private set

    val origin: String get() = "http://127.0.0.1:$port"

    /**
     * Starts Python and the engine and returns the port; if the engine is already running, returns
     * its port at once. Blocks for a few seconds the first time (Python unpacks its standard library
     * on the first launch after an install), so call it off the main thread.
     */
    @Synchronized
    fun start(context: Context): Int {
        if (port != 0) return port
        val app = context.applicationContext
        val webFiles = WebFiles.unpack(app)
        if (!Python.isStarted()) Python.start(AndroidPlatform(app))
        val python = Python.getInstance()
        // gallery/paths.py reads this when gallery is first imported, which is inside start() below.
        python.getModule("os").get("environ")!!
            .callAttr("__setitem__", "CARDCLOPS_STATIC_DIR", webFiles.absolutePath)
        val home = File(app.filesDir, "cardclops")               // the user's database and its backups
        val cache = File(app.noBackupFilesDir, "cache")          // Scryfall data and images; rebuildable
        // The automatic refresh downloads ~80 MB of card data; on a phone, only over Wi-Fi or
        // another unmetered network. Tools → Refresh card data now works on any network.
        val saved = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        port = python.getModule("gallery.android")
            .callAttr("start", home.absolutePath, cache.absolutePath, token, onUnmeteredNetwork(app), saved.getInt(PORT_KEY, 0))
            .toInt()
        saved.edit().putInt(PORT_KEY, port).apply()
        return port
    }

    private fun onUnmeteredNetwork(context: Context): Boolean {
        val connectivity = context.getSystemService(ConnectivityManager::class.java) ?: return false
        val capabilities = connectivity.getNetworkCapabilities(connectivity.activeNetwork) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
    }

    /** GET a path from the engine (e.g. "/api/setup/progress") with the token; blocking. */
    fun get(path: String, timeoutMillis: Int = 10_000): String {
        val connection = URL(origin + path).openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = timeoutMillis
            connection.readTimeout = timeoutMillis
            connection.setRequestProperty(TOKEN_HEADER, token)
            val status = connection.responseCode
            if (status != HttpURLConnection.HTTP_OK) throw IOException("HTTP $status for $path")
            return connection.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
        } finally {
            connection.disconnect()
        }
    }
}

/**
 * The web page (the repository's static/ folder, packaged as assets under static/). The engine
 * serves files from a real directory, so they are copied out of the APK once per installed build.
 */
object WebFiles {
    private const val ASSET_ROOT = "static"
    private const val STAMP_FILE = ".unpacked-from"

    fun unpack(context: Context): File {
        val target = File(context.noBackupFilesDir, ASSET_ROOT)
        val stampFile = File(target, STAMP_FILE)
        val stamp = buildStamp(context)
        if (stampFile.isFile && stampFile.readText() == stamp) return target

        // Unpack beside the old copy and swap, so a crash halfway never leaves a mixed page.
        val staging = File(context.noBackupFilesDir, "$ASSET_ROOT.new")
        staging.deleteRecursively()
        copyAssetTree(context, ASSET_ROOT, staging)
        File(staging, STAMP_FILE).writeText(stamp)
        target.deleteRecursively()
        if (!staging.renameTo(target)) throw IOException("Couldn't move the web files into $target")
        return target
    }

    /** Changes with every install, even of the same version (lastUpdateTime), so a reinstall re-unpacks. */
    private fun buildStamp(context: Context): String {
        val manager = context.packageManager
        val info = if (Build.VERSION.SDK_INT >= 33) {
            manager.getPackageInfo(context.packageName, PackageManager.PackageInfoFlags.of(0))
        } else {
            @Suppress("DEPRECATION")
            manager.getPackageInfo(context.packageName, 0)
        }
        return "${PackageInfoCompat.getLongVersionCode(info)}-${info.lastUpdateTime}"
    }

    private fun copyAssetTree(context: Context, assetPath: String, destination: File) {
        val children = context.assets.list(assetPath).orEmpty()
        if (children.isEmpty()) {
            destination.parentFile?.mkdirs()
            context.assets.open(assetPath).use { input -> destination.outputStream().use { input.copyTo(it) } }
            return
        }
        destination.mkdirs()
        for (child in children) copyAssetTree(context, "$assetPath/$child", File(destination, child))
    }
}
