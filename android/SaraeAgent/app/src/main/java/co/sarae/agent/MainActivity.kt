package co.sarae.agent

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val requestNotifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }
    override fun onCreate(savedInstanceState: Bundle?) { super.onCreate(savedInstanceState); maybeAskNotification(); setContent { SaraeAgentScreen() } }
    private fun maybeAskNotification() {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable private fun SaraeAgentScreen() {
    val context = LocalContext.current
    val store = remember { AgentConfigStore(context) }
    val runtime = remember { AgentRuntime.get(context) }
    val scope = rememberCoroutineScope()
    var config by remember { mutableStateOf(store.load()) }
    var apiUrl by remember { mutableStateOf(config?.apiUrl ?: "https://") }
    var activation by remember { mutableStateOf("") }
    var hostname by remember { mutableStateOf(Build.MODEL) }
    var videos by remember { mutableStateOf(runtime.videos()) }
    var snapshot by remember { mutableStateOf(runtime.current()) }
    var status by remember { mutableStateOf(if (config == null) "Chưa đăng ký" else "Đã đăng ký") }
    var busy by remember { mutableStateOf(false) }
    DisposableEffect(runtime) { val observer: (PlaybackSnapshot) -> Unit = { snapshot = it }; runtime.observe(observer); onDispose { runtime.remove(observer) } }

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
                            busy = true; status = "Đang kích hoạt…"
                            scope.launch(Dispatchers.IO) {
                                val result = runCatching { AgentApi.activate(apiUrl, activation, hostname, AgentApi.clientFor(context)) }
                                launch(Dispatchers.Main) {
                                    result.onSuccess { store.save(it); config = it; status = "Đã kích hoạt · ${it.agentId}" }
                                        .onFailure { status = "Kích hoạt lỗi: ${it.message}" }
                                    busy = false
                                }
                            }
                        }) { Text("Kích hoạt Android Agent") }
                        if (config != null) {
                            Button(onClick = { ContextCompat.startForegroundService(context, Intent(context, AndroidAgentService::class.java)); status = "Agent đang chạy nền" }) { Text("Kết nối") }
                            OutlinedButton(onClick = { context.startService(Intent(context, AndroidAgentService::class.java).setAction(AndroidAgentService.ACTION_STOP)); status = "Đã dừng kết nối" }) { Text("Ngắt") }
                            OutlinedButton(onClick = { store.clear(); config = null; status = "Đã xóa đăng ký trên máy" }) { Text("Xóa đăng ký") }
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
                item { Button(onClick = {
                    busy = true
                    scope.launch(Dispatchers.IO) {
                        val result = runCatching { config?.let { AgentApi(it, AgentApi.clientFor(context)).syncVideos(context) }.orEmpty() }
                        launch(Dispatchers.Main) { result.onSuccess { videos = it; status = "Đã đồng bộ ${it.size} video" }.onFailure { status = "Đồng bộ lỗi: ${it.message}" }; busy = false }
                    }
                }, enabled = config != null && !busy) { Text(if (busy) "Đang xử lý…" else "Đồng bộ video từ backend") } }
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
