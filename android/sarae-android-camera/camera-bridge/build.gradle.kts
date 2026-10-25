plugins {
    id("com.android.library")
}
dependencies { compileOnly(files("libs/api-82.jar")) }
android {
    namespace = "co.sarae.camera.bridge"
    compileSdk = 34
    defaultConfig { minSdk = 26 }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
