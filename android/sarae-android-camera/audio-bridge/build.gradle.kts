plugins { id("com.android.library") }
android {
    namespace="co.sarae.camera.audiobridge"
    compileSdk=34
    defaultConfig { minSdk=26 }
    compileOptions {
        sourceCompatibility=JavaVersion.VERSION_17
        targetCompatibility=JavaVersion.VERSION_17
    }
}
dependencies {
    implementation(project(":audio-engine"))
    compileOnly(files("../camera-bridge/libs/api-82.jar"))
}
