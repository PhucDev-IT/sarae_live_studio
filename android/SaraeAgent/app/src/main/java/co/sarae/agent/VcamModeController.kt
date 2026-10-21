package co.sarae.agent

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Environment
import java.io.File

enum class CameraSourceMode { REAL_CAMERA, VIRTUAL_VIDEO }

/** Shared command/status contract between SaraeAgent and the injected TikTok process. */
object VcamModeController {
    const val TIKTOK_PACKAGE = "com.zhiliaoapp.musically"
    const val STATUS_ACTION = "co.sarae.agent.action.VCAM_INJECTION_STATUS"
    const val STATUS_PREFS = "vcam_injection_status"
    private const val MODE_FILE = "sarae-source-mode.txt"

    fun modeFile(): File = File(Environment.getExternalStorageDirectory(), "DCIM/Camera1/$MODE_FILE")

    fun writeRequestedMode(mode: CameraSourceMode): File {
        check(Build.VERSION.SDK_INT < Build.VERSION_CODES.R || Environment.isExternalStorageManager()) {
            "Cần quyền truy cập thư mục DCIM/Camera1 để điều khiển injection."
        }
        check(mode != CameraSourceMode.VIRTUAL_VIDEO || !File(modeFile().parentFile, "disable.jpg").exists()) {
            "Tệp disable.jpg hiện có đang buộc camera thật; giữ nguyên tệp này và xóa/tắt nó thủ công trước khi yêu cầu VIRTUAL_VIDEO."
        }
        val file = modeFile()
        check(file.parentFile?.isDirectory == true || file.parentFile?.mkdirs() == true) {
            "Không tạo được thư mục DCIM/Camera1."
        }
        val temp = File(file.parentFile, "$MODE_FILE.tmp")
        temp.writeText(mode.name)
        if (file.exists()) check(file.delete()) { "Không thay được mode file hiện tại." }
        check(temp.renameTo(file)) { "Không lưu được mode VCAM." }
        return file
    }

    fun requestedMode(): CameraSourceMode {
        val root = modeFile().parentFile ?: return CameraSourceMode.REAL_CAMERA
        if (File(root, "disable.jpg").exists()) return CameraSourceMode.REAL_CAMERA
        return runCatching { CameraSourceMode.valueOf(modeFile().readText().trim()) }
            .getOrDefault(CameraSourceMode.REAL_CAMERA)
    }

    fun injectionStatus(context: Context): String {
        val prefs = context.getSharedPreferences(STATUS_PREFS, Context.MODE_PRIVATE)
        val event = prefs.getString("event", null) ?: return "Chưa nhận xác nhận từ tiến trình TikTok."
        val mode = prefs.getString("mode", "UNKNOWN")
        val detail = prefs.getString("detail", "").orEmpty()
        val at = prefs.getLong("at", 0L)
        val age = (System.currentTimeMillis() - at).coerceAtLeast(0L)
        val stale = if (age > 5 * 60_000L) " · dữ liệu cũ" else ""
        return "Injection: $event · $mode$stale${if (detail.isBlank()) "" else " · $detail"}"
    }
}

/** Receives only explicit status messages sent by the configured TikTok package UID. */
class VcamInjectionStatusReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != VcamModeController.STATUS_ACTION) return
        if (Build.VERSION.SDK_INT < 34) return
        val senderUid = sentFromUid
        val senderPackages = context.packageManager.getPackagesForUid(senderUid).orEmpty()
        if (VcamModeController.TIKTOK_PACKAGE !in senderPackages) return

        context.getSharedPreferences(VcamModeController.STATUS_PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString("event", intent.getStringExtra("event") ?: "UNKNOWN")
            .putString("mode", intent.getStringExtra("mode") ?: "UNKNOWN")
            .putString("camera_id", intent.getStringExtra("camera_id"))
            .putString("detail", intent.getStringExtra("detail"))
            .putLong("at", System.currentTimeMillis())
            .apply()
    }
}
