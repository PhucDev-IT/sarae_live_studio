pluginManagement {
    repositories { google(); mavenCentral(); gradlePluginPortal() }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories { google(); mavenCentral() }
}
rootProject.name = "sarae-android-camera"
include(":app", ":engine", ":camera-bridge")
include(":consumer-test")
include(":audio-engine", ":audio-bridge")
include(":native-audio-adapter", ":native-audio-test")
include(":native-observer", ":native-observer-module")
