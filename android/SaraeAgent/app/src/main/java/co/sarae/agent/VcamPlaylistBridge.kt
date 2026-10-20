package co.sarae.agent

import android.content.Context
import android.os.Build
import android.os.Environment
import java.io.File

/**
 * Exports downloaded Android Agent videos to the legacy VCAM shared folder.
 * This is an explicit file handoff, NOT evidence that a running TikTok camera
 * process picked up the new playlist (the camera may need to be reopened).
 */
object VcamPlaylistBridge {
    fun canWriteSharedFolder(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.R || Environment.isExternalStorageManager()

    fun apply(context: Context, videoIds: List<String>, repeat: Boolean): String {
        check(canWriteSharedFolder()) { "Cần cấp quyền Quản lý tất cả tệp trong Cài đặt Android." }
        require(videoIds.isNotEmpty()) { "Chưa chọn video để áp dụng." }
        require(videoIds.size == videoIds.distinct().size) { "Queue chứa video trùng nhau." }
        val sourceDir = File(context.filesDir, "agent-videos")
        val sources = videoIds.map { id ->
            require(id.matches(Regex("[a-zA-Z0-9_-]+"))) { "Media ID không hợp lệ." }
            File(sourceDir, "$id.mp4").also { require(it.isFile && it.length() > 0L) { "Thiếu MP4: $id" } }
        }
        val root = File(Environment.getExternalStorageDirectory(), "DCIM/Camera1")
        val playlist = File(root, "playlist")
        check(playlist.isDirectory || playlist.mkdirs()) { "Không tạo được thư mục VCAM." }

        // Stage all files first: a failed copy must not destroy the old playlist.
        val staged = File(root, "sarae-stage-${System.currentTimeMillis()}")
        check(staged.mkdirs()) { "Không tạo được thư mục tạm." }
        try {
            val prepared = sources.mapIndexed { index, file ->
                File(staged, "%03d.mp4".format(index + 1)).also { target ->
                    file.inputStream().use { input -> target.outputStream().use { output -> input.copyTo(output) } }
                    check(target.length() == file.length()) { "Sao chép MP4 không đầy đủ." }
                }
            }
            // Keep existing playlist until every staged file is verified.
            playlist.listFiles()?.filter { it.isFile && it.extension.equals("mp4", true) }
                ?.forEach { check(it.delete()) { "Không thay được ${it.name}" } }
            val paths = prepared.map { stagedFile ->
                val dest = File(playlist, stagedFile.name)
                check(stagedFile.renameTo(dest)) { "Không chuyển được ${dest.name}" }
                dest.absolutePath
            }
            val first = File(paths.first())
            val tempVirtual = File(root, "virtual.mp4.sarae.tmp")
            first.inputStream().use { input -> tempVirtual.outputStream().use { input.copyTo(it) } }
            check(tempVirtual.length() == first.length()) { "virtual.mp4 không đầy đủ." }
            val virtual = File(root, "virtual.mp4")
            if (virtual.exists()) check(virtual.delete()) { "Không thay được virtual.mp4." }
            check(tempVirtual.renameTo(virtual)) { "Không kích hoạt được virtual.mp4." }

            val manifest = File(root, "playlist.txt")
            if (repeat) manifest.writeText(paths.joinToString("\n", postfix = "\n"))
            else if (manifest.exists()) check(manifest.delete()) { "Không xóa được playlist cũ." }
            return "Đã áp dụng ${paths.size} video cho VCAM. Mở lại camera đích để kiểm tra nguồn mới."
        } finally {
            staged.listFiles()?.forEach { it.delete() }
            staged.delete()
        }
    }
}
