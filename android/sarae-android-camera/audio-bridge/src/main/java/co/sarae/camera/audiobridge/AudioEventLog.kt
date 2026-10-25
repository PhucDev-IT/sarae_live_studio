package co.sarae.camera.audiobridge

import android.content.Context
import android.os.Process
import android.os.SystemClock
import org.json.JSONObject
import java.io.File
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.atomic.AtomicLong

/** Audio capture threads never perform JSON serialization or disk flush. */
internal class AudioEventLog(context: Context) {
    private data class Event(val kind: String,val ns: Long,val values: Map<String,Any?>)
    private val queue=ArrayBlockingQueue<Event>(512)
    private val dropped=AtomicLong()
    init {
        val dir=File(context.getExternalFilesDir(null),"phase11").apply {mkdirs()}
        Thread({
            File(dir,"audio-${Process.myPid()}.jsonl").bufferedWriter().use { writer ->
                var lastFlush=System.nanoTime()
                while(true) {
                    val event=queue.poll(1,java.util.concurrent.TimeUnit.SECONDS)
                    val lost=dropped.getAndSet(0)
                    if(lost>0) {writer.write(JSONObject().put("kind","audio_log_dropped").put("count",lost).put("ns",SystemClock.elapsedRealtimeNanos()).toString());writer.newLine()}
                    if(event!=null) {
                        val row=JSONObject(event.values).put("kind",event.kind).put("ns",event.ns).put("pid",Process.myPid())
                        writer.write(row.toString());writer.newLine()
                    }
                    if(event==null || System.nanoTime()-lastFlush>=500_000_000L) {writer.flush();lastFlush=System.nanoTime()}
                }
            }
        },"sarae-audio-log").apply {isDaemon=true;start()}
    }
    fun record(kind: String,values: Map<String,Any?> = emptyMap()) {
        if(!queue.offer(Event(kind,SystemClock.elapsedRealtimeNanos(),values.toMap()))) dropped.incrementAndGet()
    }
}
