plugins {
    // Chaquopy 17.0.0 (December 2025) supports AGP 7.3 to 9.2, so AGP stays on 9.2.x even though
    // newer 9.x releases exist. Raise both together when Chaquopy supports a newer AGP.
    id("com.android.application") version "9.2.1" apply false
    id("com.chaquo.python") version "17.0.0" apply false
}
