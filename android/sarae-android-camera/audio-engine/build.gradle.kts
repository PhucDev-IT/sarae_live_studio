plugins { id("com.android.library") }
android {
    namespace="co.sarae.camera.audio"
    compileSdk=34
    defaultConfig { minSdk=26 }
    compileOptions {
        sourceCompatibility=JavaVersion.VERSION_17
        targetCompatibility=JavaVersion.VERSION_17
    }
}
dependencies { testImplementation("junit:junit:4.13.2") }
