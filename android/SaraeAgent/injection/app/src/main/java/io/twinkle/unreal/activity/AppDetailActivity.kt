package io.twinkle.unreal.activity

import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import io.twinkle.unreal.ui.screen.AppDetailScreen
import io.twinkle.unreal.ui.theme.VcamProTheme
import io.twinkle.unreal.ui.viewmodel.AppDetailViewModel

class AppDetailActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val packageName = intent.getStringExtra("package_name")
        val packageInfo =
            packageManager.getPackageInfo(packageName ?: "", PackageManager.GET_PERMISSIONS)
        val applicationInfo = requireNotNull(packageInfo.applicationInfo) { "Package has no application info" }
        val appDetailViewModel = AppDetailViewModel()
        appDetailViewModel.init(applicationInfo.packageName)
        setContent {
            VcamProTheme {
                AppDetailScreen(activity = this, packageInfo, vm = appDetailViewModel)
            }
        }
    }

}
