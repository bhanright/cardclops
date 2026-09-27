# Chaquopy's AAR brings its own keep rules for the Python bridge. The app's Kotlin code is called
# only from Android itself (activities in the manifest), which R8 keeps without help.

# The page calls MainActivity.ThemeBridge.setBars by name through addJavascriptInterface.
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
