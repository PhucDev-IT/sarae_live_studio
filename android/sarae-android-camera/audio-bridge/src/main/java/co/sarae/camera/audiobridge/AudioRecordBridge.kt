package co.sarae.camera.audiobridge

import android.app.Application
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioRecord
import android.media.AudioFormat
import android.os.SystemClock
import co.sarae.camera.audio.*
import de.robv.android.xposed.XC_MethodHook
import de.robv.android.xposed.XposedBridge
import de.robv.android.xposed.XposedHelpers
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Collections
import java.util.IdentityHashMap
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.roundToInt

/** One candidate API family. Native AAudio/OpenSL capture is never silently claimed supported. */
class AudioRecordBridge {
    @Volatile private var engine:MediaAudioEngine?=null
    @Volatile private var logger:AudioEventLog?=null
    @Volatile private var mode=AudioMode.MICROPHONE
    private val tokens=AtomicLong()
    private data class Capture(val id: Int,val cursor:AudioCursor=AudioCursor(),var reads: Long=0,var lastSource:String?=null,var lastEpoch:Long?=null)
    private val captures=Collections.synchronizedMap(IdentityHashMap<AudioRecord,Capture>())
    private val depth=ThreadLocal.withInitial {0}
    fun nextVideoToken():Long=tokens.incrementAndGet()
    fun beginVideo(token: Long)=engine?.beginVideo(token)
    fun prepare(token: Long,id: Long,uri: String)=engine?.prepare(token,id,uri)
    fun prepareBeforeSwitch(token:Long,id:Long,uri:String,ready:()->Unit) {
        val current=engine
        if(mode!=AudioMode.VIDEO || current==null)ready() else current.prepareBeforeSwitch(token,id,uri,ready)
    }
    fun videoFrame(token: Long,id: Long,uri: String,ptsNs: Long,elapsedNs: Long)=engine?.videoFrame(token,id,uri,ptsNs,elapsedNs)
    fun endVideo(token: Long)=engine?.endVideo(token)
    private fun log(kind: String,values:Map<String,Any?> = emptyMap())=logger?.record(kind,values)
    fun install() {
        XposedHelpers.findAndHookMethod(Application::class.java,"attach",Context::class.java,object:XC_MethodHook(){
            override fun afterHookedMethod(p:MethodHookParam) {
                val ctx=p.args[0] as Context
                logger=AudioEventLog(ctx);engine=MediaAudioEngine(ctx) {kind,values->log(kind,values)}
                ctx.registerReceiver(object:BroadcastReceiver(){
                    override fun onReceive(context:Context,intent:Intent) {
                        if(intent.action=="co.sarae.camera.AUDIO_SELF_TEST") {
                            AudioSelfTest.run(ctx,intent.getStringExtra("source") ?: "A") {k,v->log(k,v)};return
                        }
                        val selected=try {AudioMode.valueOf(intent.getStringExtra("mode") ?: "")} catch(_:Exception){return}
                        if(selected==AudioMode.MIXED) {log("audio_mode_rejected",mapOf("requested" to "MIXED","reason" to "reserved, not implemented"));return}
                        val old=mode;mode=selected
                        log("audio_mode",mapOf("previous" to old.name,"mode" to mode.name))
                    }
                },IntentFilter("co.sarae.camera.AUDIO_MODE").apply {addAction("co.sarae.camera.AUDIO_SELF_TEST")},Context.RECEIVER_EXPORTED)
                log("audio_bridge_attached",mapOf("api" to "Java AudioRecord public read","mode" to mode.name,"nativePathsHooked" to false,"pipelineVersion" to "phase11"))
            }
        })
        for(name in listOf("startRecording","stop","release")) XposedBridge.hookAllMethods(AudioRecord::class.java,name,object:XC_MethodHook(){
            override fun afterHookedMethod(p:MethodHookParam) {
                val record=p.thisObject as AudioRecord
                if(name=="release") {captures.remove(record);log("audio_release",mapOf("capture" to System.identityHashCode(record)));return}
                if(p.hasThrowable()) {log("audio_lifecycle_error",mapOf("method" to name,"error" to p.throwable.toString()));return}
                if(name=="startRecording") {
                    captures[record]=Capture(System.identityHashCode(record))
                    log("audio_capture_start",configuration(record)+mapOf("javaStack" to Throwable().stackTrace.take(14).joinToString("\n")))
                } else {captures.remove(record);log("audio_capture_stop",mapOf("capture" to System.identityHashCode(record)))}
            }
        })
        XposedBridge.hookAllMethods(AudioRecord::class.java,"read",object:XC_MethodHook(){
            override fun beforeHookedMethod(p:MethodHookParam) {
                depth.set((depth.get() ?: 0)+1)
                p.setObjectExtra("saraeReadStartNs",SystemClock.elapsedRealtimeNanos())
            }
            override fun afterHookedMethod(p:MethodHookParam) {
                depth.set((depth.get() ?: 1)-1)
                if(depth.get()!=0 || p.hasThrowable()) return
                val count=p.result as? Int ?: return
                val record=p.thisObject as AudioRecord
                if(count<=0) {if(count<0) log("audio_read_error",mapOf("capture" to System.identityHashCode(record),"result" to count));return}
                try {
                    val state=captures[record] ?: Capture(System.identityHashCode(record)).also {captures[record]=it}
                    state.reads++
                    if(state.reads==1L) log("audio_read_confirmed",configuration(record)+mapOf("method" to p.method.toString(),"result" to count,
                        "javaStack" to Throwable().stackTrace.take(16).joinToString("\n")))
                    val selected=mode
                    if(selected==AudioMode.MICROPHONE) {
                        if(state.reads%50L==1L)log("pcm_microphone_passthrough",mapOf("capture" to state.id,"reads" to state.reads,"result" to count))
                        return
                    }
                    substitute(record,state,p.args,count,selected,p.getObjectExtra("saraeReadStartNs") as? Long ?: SystemClock.elapsedRealtimeNanos())
                } catch(t:Throwable) {log("audio_bridge_error",mapOf("error" to t.stackTraceToString(),"mode" to mode.name))}
            }
        })
    }
    private fun configuration(record:AudioRecord): Map<String,Any?> = mapOf("capture" to System.identityHashCode(record),"rate" to record.sampleRate,
        "channels" to record.channelCount,"encoding" to record.audioFormat,"bufferFrames" to record.bufferSizeInFrames,
        "source" to record.audioSource,"session" to record.audioSessionId,"recordingState" to record.recordingState)
    private fun substitute(record:AudioRecord,capture:Capture,args:Array<Any>,count:Int,selected:AudioMode,readStartNs:Long) {
        val format=PcmFormat(record.sampleRate,record.channelCount)
        val encoding=record.audioFormat
        val bytes=when(encoding){AudioFormat.ENCODING_PCM_8BIT->1;AudioFormat.ENCODING_PCM_16BIT->2;AudioFormat.ENCODING_PCM_FLOAT->4;else->error("Unsupported capture PCM $encoding")}
        val target=args[0]
        val totalSamples=if(target is ByteBuffer || target is ByteArray) count/bytes else count
        require(totalSamples%format.channels==0 && totalSamples>0)
        val frames=totalSamples/format.channels
        require(frames<=format.rate)
        val result=if(selected==AudioMode.MUTED) MediaAudioEngine.Result(FloatArray(totalSamples),0,0,null)
            else requireNotNull(engine).read(format,frames,capture.cursor)
        val kind=when(bytes){1->PcmEncoding.U8;2->PcmEncoding.S16;else->PcmEncoding.FLOAT}
        PcmBufferWriter.write(target,if(target is ByteBuffer) 0 else args[1] as Int,result.pcm,kind,count)
        if(capture.lastSource!=result.key || capture.lastEpoch!=result.epoch) {
            log("audio_read_source_transition",mapOf("capture" to capture.id,"previousKey" to capture.lastSource,"key" to result.key,"epoch" to result.epoch,"mediaNs" to result.mediaNs,"readStartNs" to readStartNs,"readEndNs" to SystemClock.elapsedRealtimeNanos()))
            capture.lastSource=result.key;capture.lastEpoch=result.epoch
        }
        if(capture.reads%20L==1L || result.missingFrames>0)
            log("pcm_substituted",mapOf("capture" to capture.id,"mode" to selected.name,"frames" to frames,"resultUnchanged" to count,
                "missingFrames" to result.missingFrames,"driftNs" to result.driftNs,"key" to result.key,"epoch" to result.epoch,"mediaNs" to result.mediaNs,"readStartNs" to readStartNs,"readEndNs" to SystemClock.elapsedRealtimeNanos(),"readFrames" to capture.cursor.framesRead))
    }
}
