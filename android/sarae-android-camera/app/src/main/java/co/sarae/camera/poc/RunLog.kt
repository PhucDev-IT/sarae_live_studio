package co.sarae.camera.poc

import android.content.Context
import android.os.SystemClock
import android.util.Log
import org.json.JSONObject
import java.io.BufferedWriter
import java.io.File
import java.io.FileWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** JSONL in app-specific external storage, plus compact logcat entries under SaraeCameraPoC. */
class RunLog(context: Context) : AutoCloseable {
    val file: File
    private val writer: BufferedWriter
    private var count = 0
    private var closed = false

    init {
        val dir = File(requireNotNull(context.getExternalFilesDir(null)), "logs").apply { mkdirs() }
        val stamp = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date())
        file = File(dir, "phase1-$stamp.jsonl")
        writer = BufferedWriter(FileWriter(file, true))
    }

    @Synchronized fun record(kind: String, detail: String, extras: Map<String, Any?> = emptyMap()) {
        if (closed) return
        val event = JSONObject()
            .put("wallTimeMs", System.currentTimeMillis())
            .put("elapsedRealtimeNs", SystemClock.elapsedRealtimeNanos())
            .put("kind", kind)
            .put("detail", detail)
        extras.forEach { (key, value) -> event.put(key, value) }
        writer.write(event.toString())
        writer.newLine()
        count++
        if (count % 30 == 0 || kind != "frame_rendered") writer.flush()
        if (kind != "frame_rendered" || count % 60 == 0) Log.i("SaraeCameraPoC", event.toString())
    }

    @Synchronized override fun close() {
        if (closed) return
        writer.flush()
        writer.close()
        closed = true
    }
}
