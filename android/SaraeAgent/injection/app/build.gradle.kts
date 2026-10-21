plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    id("com.google.devtools.ksp") version "2.2.10-2.0.2"
    id("org.jetbrains.kotlin.plugin.serialization") version "2.2.10"
}

android {
    namespace = "io.twinkle.unreal"
    compileSdk { version = release(37) }
    defaultConfig {
        applicationId = "io.twinkle.unreal"
        minSdk = 26
        targetSdk = 35
        versionCode = 20261021
        versionName = "1.0.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
}

dependencies {
    implementation(project(":core:audio"))
    implementation("com.highcapable.yukihookapi:api:1.2.0")
    compileOnly(files("libs/api-82.jar"))
    ksp("com.highcapable.yukihookapi:ksp-xposed:1.2.0")
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation("androidx.compose.material:material-icons-extended:1.7.8")
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.11.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.11.0")
    implementation("androidx.datastore:datastore-preferences:1.2.0")
    implementation("me.zhanghai.android.appiconloader:appiconloader:1.5.0")
    implementation("com.github.alorma.compose-settings:ui-tiles:2.1.0")
    implementation("com.github.alorma.compose-settings:ui-tiles-extended:2.1.0")
    implementation("com.google.accompanist:accompanist-drawablepainter:0.35.0-alpha")
    implementation("com.google.accompanist:accompanist-permissions:0.35.0-alpha")
    implementation("com.github.nanihadesuka:LazyColumnScrollbar:2.0.7")
    implementation("com.blankj:utilcodex:1.31.1")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.0")
    testImplementation(libs.junit)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    debugImplementation(libs.androidx.compose.ui.tooling)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
}

