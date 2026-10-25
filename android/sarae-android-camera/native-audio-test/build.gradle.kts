plugins { id("com.android.application") }
android {
    namespace = "co.sarae.camera.nativeaudiotest"
    compileSdk = 34
    defaultConfig {
        applicationId = "co.sarae.camera.nativeaudiotest"
        minSdk = 26
        targetSdk = 34
        versionCode = 15
        versionName = "0.15-native-poc"
        ndk { abiFilters += "arm64-v8a" }
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
}
dependencies {
    implementation(project(":audio-engine"))
    implementation(project(":native-audio-adapter"))
}
