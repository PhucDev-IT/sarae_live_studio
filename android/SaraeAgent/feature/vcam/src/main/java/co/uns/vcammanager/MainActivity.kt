package co.uns.vcammanager

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import co.uns.vcammanager.ui.theme.VCAMManagerTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

private enum class Page { PLAYLIST, AUDIO_TEST }
private data class VideoItem(val uri: Uri, val name: String)

@Composable
fun VcamManagerScreen(onBack: () -> Unit) {
    VCAMManagerTheme { ManagerApp(onBack) }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ManagerApp(onBack: () -> Unit) {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("playlist", 0) }
    val videos = remember { mutableStateListOf<VideoItem>() }
    var repeat by remember { mutableStateOf(prefs.getBoolean("repeat", true)) }
    var page by remember { mutableStateOf(Page.PLAYLIST) }
    var status by remember { mutableStateOf("Chọn video để tạo danh sách phát.") }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        val saved = prefs.getString("uris", "").orEmpty().lines().filter(String::isNotBlank)
        saved.forEach { raw ->
            runCatching {
                val uri = Uri.parse(raw)
                videos.add(VideoItem(uri, displayName(context, uri)))
            }
        }
    }
    DisposableEffect(videos.toList()) {
        prefs.edit().putString("uris", videos.joinToString("\n") { it.uri.toString() }).apply()
        onDispose { }
    }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        uris.forEach { uri ->
            runCatching { context.contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
            if (videos.none { it.uri == uri }) videos.add(VideoItem(uri, displayName(context, uri)))
        }
        status = if (videos.isEmpty()) "Chưa có video." else "${videos.size} video trong danh sách."
    }

    Scaffold(topBar = { TopAppBar(title = { Text("VCAM & Audio") }, navigationIcon = {
        androidx.compose.material3.TextButton(onClick = onBack) { Text("‹ Trở về") }
    }) }) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(horizontal = 16.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (page == Page.PLAYLIST) Button(onClick = { page = Page.PLAYLIST }) { Text("Playlist") }
                else OutlinedButton(onClick = { page = Page.PLAYLIST }) { Text("Playlist") }
                if (page == Page.AUDIO_TEST) Button(onClick = { page = Page.AUDIO_TEST }) { Text("Audio test") }
                else OutlinedButton(onClick = { page = Page.AUDIO_TEST }) { Text("Audio test") }
            }
            Spacer(Modifier.height(8.dp))
            if (page == Page.PLAYLIST) {
                PlaylistPage(
                    videos = videos,
                    repeat = repeat,
                    status = status,
                    onRepeat = { repeat = it; prefs.edit().putBoolean("repeat", it).apply() },
                    onPick = { picker.launch(arrayOf("video/*")) },
                    onMove = { from, to -> if (to in videos.indices) { val item = videos.removeAt(from); videos.add(to, item) } },
                    onRemove = { videos.removeAt(it) },
                    onApply = {
                        if (videos.isEmpty()) { status = "Chọn ít nhất một video trước."; return@PlaylistPage }
                        if (android.os.Build.VERSION.SDK_INT >= 30 && !Environment.isExternalStorageManager()) {
                            context.startActivity(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:${context.packageName}")))
                            status = "Cho phép quyền quản lý tệp rồi nhấn Áp dụng lần nữa."
                        } else {
                            val snapshot = videos.toList()
                            val loop = repeat
                            scope.launch {
                                status = "Đang sao chép video vào DCIM/Camera1…"
                                status = withContext(Dispatchers.IO) { applyPlaylist(context, snapshot, loop) }
                            }
                        }
                    }
                )
            } else {
                AudioTestPage()
            }
        }
    }
}

@Composable
private fun PlaylistPage(
    videos: List<VideoItem>, repeat: Boolean, status: String,
    onRepeat: (Boolean) -> Unit, onPick: () -> Unit,
    onMove: (Int, Int) -> Unit, onRemove: (Int) -> Unit, onApply: () -> Unit
) {
    Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("Chọn và sắp xếp video. TikTok phát lần lượt rồi bắt đầu lại từ đầu.", style = MaterialTheme.typography.bodyMedium)
        Button(onClick = onPick, modifier = Modifier.fillMaxWidth()) { Text("＋  Chọn video") }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Lặp lại cả danh sách", modifier = Modifier.weight(1f))
            Switch(checked = repeat, onCheckedChange = onRepeat)
        }
        Text("${videos.size} video · $status", style = MaterialTheme.typography.bodySmall)
        HorizontalDivider()
        LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            itemsIndexed(videos, key = { _, item -> item.uri.toString() }) { index, item ->
                Card(Modifier.fillMaxWidth()) {
                    Row(Modifier.padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text("${index + 1}. ${item.name}", Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                        OutlinedButton(onClick = { onMove(index, index - 1) }, enabled = index > 0) { Text("↑") }
                        OutlinedButton(onClick = { onMove(index, index + 1) }, enabled = index < videos.lastIndex) { Text("↓") }
                        OutlinedButton(onClick = { onRemove(index) }) { Text("×") }
                    }
                }
            }
        }
        Button(onClick = onApply, modifier = Modifier.fillMaxWidth()) { Text("Áp dụng playlist cho VCAM") }
        Text("Đóng rồi mở lại màn hình camera sau khi áp dụng.", style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun AudioTestPage() {
    val context = LocalContext.current
    val runner = remember { AudioTestRunner(context.applicationContext) }
    var selected by remember { mutableStateOf<Pair<Uri, String>?>(null) }
    val profiles = remember { listOf(AudioOutputProfile(44100, 1), AudioOutputProfile(44100, 2), AudioOutputProfile(48000, 1), AudioOutputProfile(48000, 2)) }
    var selectedProfile by remember { mutableStateOf(profiles[1]) }
    var profileMenuExpanded by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("Chọn MP4 có tiếng; bài test chạy độc lập, không mở TikTok LIVE.") }
    var metrics by remember { mutableStateOf("Chưa chạy.") }
    var wavPath by remember { mutableStateOf<String?>(null) }
    var muted by remember { mutableStateOf(false) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            runCatching { context.contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
            selected = uri to displayName(context, uri)
            status = "Đã chọn ${selected?.second}"
        }
    }
    val wavExport = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("audio/wav")) { uri ->
        val source = wavPath?.let(::File)
        if (uri != null && source != null) {
            runCatching { context.contentResolver.openOutputStream(uri)?.use { out -> source.inputStream().use { it.copyTo(out) } } }
                .onSuccess { status = "Đã xuất WAV: ${source.name}" }.onFailure { status = "Xuất WAV lỗi: ${it.message}" }
        }
    }
    val logExport = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/plain")) { uri ->
        if (uri != null) runCatching {
            context.contentResolver.openOutputStream(uri)?.bufferedWriter()?.use { it.write(runner.exportLog()) }
        }.onSuccess { status = "Đã xuất log." }.onFailure { status = "Xuất log lỗi: ${it.message}" }
    }
    DisposableEffect(Unit) { onDispose { runner.stop() } }
    Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("Harness dùng cùng VirtualAudioFeeder như hook AudioRecord/TEAudioDataInterface. Không xác nhận callback TikTok hoặc âm thanh LIVE.", style = MaterialTheme.typography.bodyMedium)
        OutlinedButton(onClick = { picker.launch(arrayOf("video/mp4", "video/*")) }, modifier = Modifier.fillMaxWidth()) { Text("Chọn MP4 có hình + tiếng") }
        Text(selected?.second ?: "Chưa chọn file")
        Box {
            OutlinedButton(onClick = { profileMenuExpanded = true }) { Text("Đầu ra: ${selectedProfile.label}") }
            DropdownMenu(expanded = profileMenuExpanded, onDismissRequest = { profileMenuExpanded = false }) {
                profiles.forEach { profile ->
                    DropdownMenuItem(
                        text = { Text(profile.label) },
                        onClick = { selectedProfile = profile; profileMenuExpanded = false }
                    )
                }
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = muted, onCheckedChange = { muted = it })
            Text("Tắt tiếng loa khi chạy endurance (PCM vẫn đi qua AudioTrack)")
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = {
                val item = selected ?: run { status = "Hãy chọn MP4 trước."; return@Button }
                status = "Đang phát PCM qua feeder dùng chung…"
                runner.start(item.first, selectedProfile.sampleRate, selectedProfile.channels, muted) { event, stats, path ->
                    status = event
                    metrics = stats
                    wavPath = path
                }
            }, enabled = selected != null) { Text("Start / Replay") }
            OutlinedButton(onClick = { runner.stop(); wavPath = runner.lastWavPath; status = "Đã dừng. WAV được hoàn tất." }) { Text("Stop") }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { wavPath?.let { wavExport.launch(File(it).name) } }, enabled = wavPath != null) { Text("Xuất WAV") }
            OutlinedButton(onClick = { logExport.launch("vcam-audio-test.log") }) { Text("Export Logs") }
        }
        Text(status, style = MaterialTheme.typography.bodySmall)
        HorizontalDivider()
        Text("Metrics", style = MaterialTheme.typography.titleMedium)
        Text(metrics, style = MaterialTheme.typography.bodySmall)
        Text("TikTok onReceive callback: NOT_TESTABLE_OFFLINE · TikTok LIVE audio: UNVERIFIED", style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(4.dp))
        Text("Cho phép chạy ít nhất 30 phút bằng cách để Start hoạt động rồi Stop; live không được mở.", style = MaterialTheme.typography.bodySmall)
    }
}

private fun displayName(context: android.content.Context, uri: Uri): String {
    return runCatching {
        context.contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) cursor.getString(0) else null
        }
    }.getOrNull() ?: (uri.lastPathSegment ?: "video.mp4")
}

private fun applyPlaylist(context: android.content.Context, items: List<VideoItem>, repeat: Boolean): String {
    return try {
        val base = File(Environment.getExternalStorageDirectory(), "DCIM/Camera1")
        val playlist = File(base, "playlist")
        if (!playlist.exists() && !playlist.mkdirs()) error("Không tạo được ${playlist.absolutePath}")
        playlist.listFiles()?.filter { it.isFile }?.forEach { if (!it.delete()) error("Không thay được ${it.name}") }
        val paths = items.mapIndexed { index, item ->
            val target = File(playlist, "%03d.mp4".format(index + 1))
            context.contentResolver.openInputStream(item.uri)?.use { input -> target.outputStream().use(input::copyTo) }
                ?: error("Không mở được ${item.name}")
            target.absolutePath
        }
        File(base, "virtual.mp4").outputStream().use { output -> File(paths.first()).inputStream().use { it.copyTo(output) } }
        File(base, "playlist.txt").let { manifest ->
            if (repeat) manifest.writeText(paths.joinToString("\n", postfix = "\n")) else manifest.delete()
        }
        "Đã áp dụng ${items.size} video vào ${playlist.absolutePath}."
    } catch (t: Throwable) { "Không áp dụng được playlist: ${t.message}" }
}
