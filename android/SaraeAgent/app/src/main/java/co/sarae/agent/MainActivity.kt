package co.sarae.agent

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Environment
import android.provider.Settings
import android.os.Build
import android.os.Bundle
import android.view.SurfaceHolder
import android.view.SurfaceView
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import co.uns.vcammanager.VcamManagerScreen
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val requestNotifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }
    override fun onCreate(savedInstanceState: Bundle?) { super.onCreate(savedInstanceState); maybeAskNotification(); setContent { SaraeAgentNavigation() } }
    private fun maybeAskNotification() {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
}

@Composable
private fun SaraeAgentNavigation() {
    val navController = rememberNavController()
    NavHost(navController = navController, startDestination = "agent") {
        composable("agent") { SaraeAgentScreen(onOpenVcam = { navController.navigate("vcam") }) }
        composable("vcam") { VcamManagerScreen(onBack = { navController.popBackStack() }) }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable private fun SaraeAgentScreen(onOpenVcam: () -> Unit) {
    val context = LocalContext.current
    val agentModel: AgentViewModel = viewModel()
    val playbackModel: PlaybackViewModel = viewModel()
    val runtime = playbackModel.runtime
    val scope = rememberCoroutineScope()
    val config by agentModel.config.collectAsStateWithLifecycle()
    val status by agentModel.status.collectAsStateWithLifecycle()
    val busy by agentModel.busy.collectAsStateWithLifecycle()
    var apiUrl by remember { mutableStateOf(config?.apiUrl ?: "https://") }
    var activation by remember { mutableStateOf("") }
    var hostname by remember { mutableStateOf(Build.MODEL) }
    var videos by remember { mutableStateOf(runtime.videos()) }
    val snapshot by playbackModel.snapshot.collectAsStateWithLifecycle()
    var vcamStatus by remember { mutableStateOf("Chưa áp dụng nguồn VCAM từ Android Agent.") }
    var vcamBusy by remember { mutableStateOf(false) }
    var requestedCameraMode by remember { mutableStateOf(VcamModeController.requestedMode()) }
    var injectionStatus by remember { mutableStateOf(VcamModeController.injectionStatus(context)) }

    LaunchedEffect(context) {
        while (true) {
            injectionStatus = VcamModeController.injectionStatus(context)
            delay(1_000)
        }
    }

    MaterialTheme {
        Scaffold(topBar = { TopAppBar(title = { Text("Sarae Android Agent V1") }) }) { padding ->
            LazyColumn(Modifier.fillMaxSize().padding(padding).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                item {
                    Text(status, style = MaterialTheme.typography.titleMedium)
                    OutlinedTextField(apiUrl, { apiUrl = it }, label = { Text("Backend HTTPS URL") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
                    OutlinedTextField(hostname, { hostname = it }, label = { Text("Tên thiết bị") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
                    if (config == null) OutlinedTextField(activation, { activation = it }, label = { Text("Mã kích hoạt") }, modifier = Modifier.fillMaxWidth())
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(enabled = !busy && config == null && activation.isNotBlank(), onClick = {
                            agentModel.activate(apiUrl, activation, hostname)
                        }) { Text("Kích hoạt Android Agent") }
                        if (config != null) {
                            Button(onClick = { ContextCompat.startForegroundService(context, Intent(context, AndroidAgentService::class.java)); agentModel.setStatus("Agent đang chạy nền") }) { Text("Kết nối") }
                            OutlinedButton(onClick = { context.startService(Intent(context, AndroidAgentService::class.java).setAction(AndroidAgentService.ACTION_STOP)); agentModel.setStatus("Đã dừng kết nối") }) { Text("Ngắt") }
                            OutlinedButton(onClick = { agentModel.clearRegistration() }) { Text("Xóa đăng ký") }
                        }
                    }
                }
                item { AndroidView(factory = { ctx -> SurfaceView(ctx).apply {
                    holder.addCallback(object : SurfaceHolder.Callback {
                        override fun surfaceCreated(holder: SurfaceHolder) { runtime.attachSurface(holder.surface) }
                        override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) { runtime.attachSurface(holder.surface) }
                        override fun surfaceDestroyed(holder: SurfaceHolder) { runtime.attachSurface(null) }
                    })
                } }, modifier = Modifier.fillMaxWidth().height(230.dp)) }
                item {
                    Text("Mock LIVE · không kết nối TikTok", style = MaterialTheme.typography.titleMedium)
                    Text("${snapshot.state} · ${snapshot.currentId ?: "chưa phát"} · ${snapshot.positionMs / 1000}s / ${snapshot.durationMs / 1000}s")
                    if (snapshot.error != null) Text("Lỗi: ${snapshot.error}", color = MaterialTheme.colorScheme.error)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { runtime.previous() }) { Text("Trước") }
                        Button(onClick = { runtime.resume() }) { Text("Phát / tiếp tục") }
                        Button(onClick = { runtime.pause() }) { Text("Tạm dừng") }
                        Button(onClick = { runtime.next() }) { Text("Tiếp") }
                        OutlinedButton(onClick = { runtime.stop() }) { Text("Dừng") }
                    }
                }
                item {
                    Button(
                        onClick = {
                            agentModel.syncVideos { result -> result.onSuccess { videos = it } }
                        },
                        enabled = config != null && !busy
                    ) { Text(if (busy) "Đang xử lý…" else "Đồng bộ video từ backend") }
                }
                item {
                    HorizontalDivider()
                    Text("Nguồn VCAM", style = MaterialTheme.typography.titleMedium)
                    Text("TikTok pre-LIVE · yêu cầu: ${requestedCameraMode.name}", style = MaterialTheme.typography.bodyMedium)
                    Text(injectionStatus, style = MaterialTheme.typography.bodySmall)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilledTonalButton(
                            enabled = !vcamBusy && VcamPlaylistBridge.canWriteSharedFolder(),
                            onClick = {
                                vcamBusy = true
                                scope.launch(Dispatchers.IO) {
                                    val result = runCatching { VcamModeController.writeRequestedMode(CameraSourceMode.REAL_CAMERA) }
                                    launch(Dispatchers.Main) {
                                        result.onSuccess {
                                            requestedCameraMode = CameraSourceMode.REAL_CAMERA
                                            vcamStatus = "REAL_CAMERA requested. TikTok preview sluiten en opnieuw openen voor veilige toepassing."
                                        }.onFailure { vcamStatus = "Mode niet op te slaan: ${it.message}" }
                                        vcamBusy = false
                                    }
                                }
                            }
                        ) { Text("REAL_CAMERA") }
                        FilledTonalButton(
                            enabled = !vcamBusy && VcamPlaylistBridge.canWriteSharedFolder(),
                            onClick = {
                                vcamBusy = true
                                scope.launch(Dispatchers.IO) {
                                    val result = runCatching { VcamModeController.writeRequestedMode(CameraSourceMode.VIRTUAL_VIDEO) }
                                    launch(Dispatchers.Main) {
                                        result.onSuccess {
                                            requestedCameraMode = CameraSourceMode.VIRTUAL_VIDEO
                                            vcamStatus = "VIRTUAL_VIDEO requested. TikTok preview sluiten en opnieuw openen voor veilige toepassing."
                                        }.onFailure { vcamStatus = "Mode niet op te slaan: ${it.message}" }
                                        vcamBusy = false
                                    }
                                }
                            }
                        ) { Text("VIRTUAL_VIDEO") }
                    }
                    Text("Mode được áp dụng khi TikTok mở camera/session mới. Không đổi output của Camera2 session đang chạy; đóng camera preview rồi mở lại để chuyển nguồn an toàn. Injection status chỉ được xác nhận qua callback từ process TikTok.", style = MaterialTheme.typography.bodySmall)
                    OutlinedButton(onClick = onOpenVcam) {
                        Text("Playlist & Audio Test Harness")
                    }
                    Text("Áp dụng queue MP4 vào DCIM/Camera1 của VCAM hiện tại. Đây là thao tác chuẩn bị nguồn; không tự bắt đầu LIVE hay xác nhận TikTok đã nhận hình.")
                    Text(vcamStatus, style = MaterialTheme.typography.bodySmall)
                    if (!VcamPlaylistBridge.canWriteSharedFolder()) {
                        OutlinedButton(onClick = {
                            val intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                                Uri.parse("package:${context.packageName}"))
                            context.startActivity(intent)
                        }) { Text("Cấp quyền truy cập thư mục VCAM") }
                    }
                    Button(
                        enabled = !vcamBusy && videos.isNotEmpty() && VcamPlaylistBridge.canWriteSharedFolder(),
                        onClick = {
                            vcamBusy = true
                            val selectedIds = snapshot.queue.ifEmpty { videos.map { it.mediaId } }
                            val repeat = snapshot.loop
                            scope.launch(Dispatchers.IO) {
                                val result = runCatching { VcamPlaylistBridge.apply(context, selectedIds, repeat) }
                                launch(Dispatchers.Main) {
                                    vcamStatus = result.getOrElse { "Không áp dụng được VCAM: ${it.message}" }
                                    vcamBusy = false
                                }
                            }
                        }
                    ) { Text("Áp dụng queue cho VCAM") }
                }
                item { Text("Video đã tải xuống · ${videos.size}", style = MaterialTheme.typography.titleMedium) }
                items(videos, key = { it.mediaId }) { video ->
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp)) {
                            Text(video.filename, style = MaterialTheme.typography.titleSmall)
                            Text("${video.sizeBytes / 1024 / 1024} MB · ${video.mediaId.take(12)}…")
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Button(onClick = { runtime.play(video.mediaId); runtime.setQueue(videos.map { it.mediaId }, true, true) }) { Text("Phát ngay") }
                                OutlinedButton(onClick = { runtime.switch(video.mediaId, true) }) { Text("Sau video hiện tại") }
                            }
                        }
                    }
                }
            }
        }
    }
}
