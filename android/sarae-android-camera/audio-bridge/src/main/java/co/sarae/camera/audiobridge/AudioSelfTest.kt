package co.sarae.camera.audiobridge

import android.content.Context
import android.net.Uri
import android.os.SystemClock
import co.sarae.camera.audio.*
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.atomic.AtomicBoolean

internal object AudioSelfTest {
    private val running=AtomicBoolean()
    fun run(context:Context,source:String,log:(String,Map<String,Any?>)->Unit) {
        if(source !in listOf("A","B") || !running.compareAndSet(false,true))return
        Thread({
            val engine=MediaAudioEngine(context,log)
            try {
                val uri=Uri.fromFile(File(context.getExternalFilesDir(null),"$source.mp4")).toString()
                val dir=File(context.getExternalFilesDir(null),"phase10").apply {mkdirs()}
                engine.beginVideo(1);engine.prepare(1,0,uri)
                Thread.sleep(350)
                val start=SystemClock.elapsedRealtimeNanos();val cursor=AudioCursor();var missing=0L
                File(dir,"selftest-$source-s16-48000-mono.pcm").outputStream().use {output->
                    for(block in 0 until 200) {
                        val now=SystemClock.elapsedRealtimeNanos()
                        engine.videoFrame(1,0,uri,(block+1)*10_000_000L,now)
                        val result=engine.read(PcmFormat(48000,1),480,cursor,now);missing+=result.missingFrames
                        val buffer=ByteBuffer.allocate(960).order(ByteOrder.LITTLE_ENDIAN)
                        result.pcm.forEach {buffer.putShort((it.coerceIn(-1f,1f)*32767).toInt().toShort())}
                        output.write(buffer.array())
                        val wait=start+(block+1)*10_000_000L-SystemClock.elapsedRealtimeNanos()
                        if(wait>0)Thread.sleep(wait/1_000_000L,(wait%1_000_000L).toInt())
                    }
                }
                log("audio_selftest_complete",mapOf("source" to source,"frames" to 96000,"missingFrames" to missing,"rate" to 48000,"channels" to 1))
            } catch(t:Throwable) {log("audio_selftest_error",mapOf("error" to t.stackTraceToString()))}
            finally {engine.close();running.set(false)}
        },"sarae-audio-selftest").start()
    }
}
