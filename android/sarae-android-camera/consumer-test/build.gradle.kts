plugins { id("com.android.application") }
android {
    namespace = "co.sarae.camera.consumer"
    compileSdk = 34
    defaultConfig { applicationId = "co.sarae.camera.consumer"; minSdk = 28; targetSdk = 34; versionCode = 1; versionName = "0.2-test" }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
}
