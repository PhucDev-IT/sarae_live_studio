plugins {
    id("com.android.library")
}
android {
    namespace = "co.sarae.camera.engine"
    compileSdk = 34
    defaultConfig { minSdk = 26 }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
dependencies {
    implementation(project(":camera-bridge"))
    testImplementation("junit:junit:4.13.2")
}
