pluginManagement {
    plugins {
        id("com.android.library") version "9.4.1"
        id("org.jetbrains.kotlin.android") version "2.2.10"
    }
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}
plugins {
    id("org.gradle.toolchains.foojay-resolver-convention") version "1.0.0"
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "VCAM Manager"
include(":app")
include(":audio-core")
project(":audio-core").projectDir = file("../audio-core")
 
