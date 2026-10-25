plugins {
    id("com.android.application")
}
android {
    namespace = "co.sarae.camera.poc"
    compileSdk = 34
    defaultConfig {
        applicationId = "co.sarae.camera.poc"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1-phase1"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
dependencies {
    compileOnly(files("../camera-bridge/libs/api-82.jar"))
    implementation(project(":camera-bridge"))
    implementation(project(":engine"))
    implementation(project(":audio-engine"))
    implementation(project(":audio-bridge"))
    implementation(project(":native-observer"))
}
