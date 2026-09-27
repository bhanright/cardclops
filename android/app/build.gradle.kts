import com.android.build.api.artifact.SingleArtifact
import com.android.build.api.variant.BuiltArtifactsLoader
import javax.inject.Inject

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// The repository this Android project lives in: the engine (gallery/) and the web page (static/)
// are packaged from there as they are, never copied into android/ by hand.
val repositoryRoot: File = rootProject.projectDir.parentFile

// The app's version is the engine's: gallery/__init__.py says __version__ = "0.1.3", which becomes
// versionName "0.1.3" and versionCode 103 (major * 10000 + minor * 100 + patch).
val cardclopsVersion: String = Regex("""__version__\s*=\s*"([^"]+)"""")
    .find(File(repositoryRoot, "gallery/__init__.py").readText())
    ?.groupValues?.get(1)
    ?: error("No __version__ in gallery/__init__.py")
val cardclopsVersionCode: Int = cardclopsVersion.split(".")
    .map { part -> part.takeWhile(Char::isDigit).ifEmpty { "0" }.toInt() }
    .let { parts -> parts.getOrElse(0) { 0 } * 10000 + parts.getOrElse(1) { 0 } * 100 + parts.getOrElse(2) { 0 } }

// Release signing reads %USERPROFILE%\.gradle\gradle.properties (see android/README.md). Without
// those properties the release APK is built unsigned and cannot be installed.
val releaseKeystore: String? = providers.gradleProperty("cardclopsKeystore").orNull

android {
    namespace = "com.cardclops.app"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.cardclops.app"
        minSdk = 26            // adaptive icons without a bitmap fallback; Chaquopy itself needs 24
        targetSdk = 36
        versionCode = cardclopsVersionCode
        versionName = cardclopsVersion
        ndk {
            // Phones. Python 3.12 and newer have no 32-bit builds, so arm64-v8a is every phone
            // Cardclops can run on. Add "x86_64" here to run it on an x86_64 emulator (it adds
            // about 16 MB; Chaquopy reads only this default list, not per-build-type lists).
            abiFilters += listOf("arm64-v8a")
        }
    }

    signingConfigs {
        if (releaseKeystore != null) {
            create("release") {
                storeFile = file(releaseKeystore)
                storePassword = providers.gradleProperty("cardclopsKeystorePassword").get()
                keyAlias = providers.gradleProperty("cardclopsKeyAlias").getOrElse("cardclops")
                keyPassword = providers.gradleProperty("cardclopsKeyPassword").get()
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        jniLibs {
            // Chaquopy's native libraries stay compressed in the APK; it extracts what it needs.
            useLegacyPackaging = true
        }
    }
}

chaquopy {
    defaultConfig {
        // The newest Python that Chaquopy 17 has numpy wheels for (3.14 has almost no Android
        // wheels yet). The build machine needs the same Python version to compile .pyc files.
        version = "3.13"
        providers.gradleProperty("cardclopsBuildPython").orNull?.let { buildPython(it) }
        pip {
            install("certifi")     // Scryfall's image CDN chain needs certifi's bundle
            install("numpy")       // the deck builder's scoring
        }
    }
    sourceSets {
        getByName("main") {
            srcDir(layout.buildDirectory.dir("cardclops/python").get().asFile)
        }
    }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.13.0")
    implementation("androidx.core:core-ktx:1.19.1")
}

/** The engine package, gallery/, staged without caches so Chaquopy packages exactly the source. */
val stagePythonEngine = tasks.register<Sync>("stagePythonEngine") {
    from(File(repositoryRoot, "gallery")) {
        exclude("**/__pycache__/**", "**/*.pyc")
    }
    into(layout.buildDirectory.dir("cardclops/python/gallery"))
}
tasks.configureEach {
    // Chaquopy's tasks that read Python sources (mergeDebugPythonSources and the like).
    if (name.contains("Python", ignoreCase = false) && name != stagePythonEngine.name) {
        dependsOn(stagePythonEngine)
    }
}

/** The web page, static/, as assets under static/; MainActivity unpacks them on first launch. */
abstract class StageWebFiles : DefaultTask() {
    @get:InputDirectory
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val webFiles: DirectoryProperty

    @get:OutputDirectory
    abstract val outputDirectory: DirectoryProperty

    @get:Inject
    abstract val fileSystem: FileSystemOperations

    @TaskAction
    fun stage() {
        fileSystem.sync {
            from(webFiles)
            into(outputDirectory.dir("static"))
        }
    }
}

val stageWebFiles = tasks.register<StageWebFiles>("stageWebFiles") {
    webFiles.set(File(repositoryRoot, "static"))
}

/** Copies the finished APK to build/outputs/cardclops/Cardclops-<version>.apk. */
abstract class CopyNamedApk : DefaultTask() {
    @get:InputFiles
    abstract val apkFolder: DirectoryProperty

    @get:Internal
    abstract val builtArtifactsLoader: Property<BuiltArtifactsLoader>

    @get:OutputFile
    abstract val namedApk: RegularFileProperty

    @TaskAction
    fun copy() {
        val builtArtifacts = builtArtifactsLoader.get().load(apkFolder.get())
            ?: error("No APK in ${apkFolder.get()}")
        val apk = File(builtArtifacts.elements.single().outputFile)
        apk.copyTo(namedApk.get().asFile, overwrite = true)
        logger.lifecycle("Cardclops APK: ${namedApk.get().asFile}")
    }
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(stageWebFiles, StageWebFiles::outputDirectory)

        val variantTitle = variant.name.replaceFirstChar { it.uppercase() }
        val suffix = if (variant.buildType == "release") "" else "-${variant.name}"
        val copyTask = tasks.register<CopyNamedApk>("copy${variantTitle}NamedApk") {
            apkFolder.set(variant.artifacts.get(SingleArtifact.APK))
            builtArtifactsLoader.set(variant.artifacts.getBuiltArtifactsLoader())
            namedApk.set(layout.buildDirectory.file("outputs/cardclops/Cardclops-$cardclopsVersion$suffix.apk"))
        }
        tasks.matching { it.name == "assemble$variantTitle" }.configureEach { finalizedBy(copyTask) }
    }
}
