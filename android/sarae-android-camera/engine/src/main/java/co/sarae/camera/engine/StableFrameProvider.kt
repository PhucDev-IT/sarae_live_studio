package co.sarae.camera.engine

import android.content.Context
import android.graphics.SurfaceTexture
import android.net.Uri
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.os.Trace
import android.view.Surface
import co.sarae.camera.bridge.CameraFrameProvider
import co.sarae.camera.bridge.FrameEvent
import co.sarae.camera.bridge.RenderConfiguration
import java.io.File

/** Persistent EGL producer. Reuses the existing decoder implementation for active/preload slots. */
class StableFrameProvider(private val context: Context, targetFps: Int = 30) : CameraFrameProvider {
    init { require(targetFps in 1..60) }
    private val periodNs=1_000_000_000L/targetFps
    private val thread=HandlerThread("sarae-egl-output").apply { start() }
    private val handler=Handler(thread.looper)
    private var egl: EglOutput?=null
    private var sink: (FrameEvent)->Unit={}
    private var active: Slot?=null
    private var pending: Slot?=null
    private val slots=mutableSetOf<Slot>()
    private var switchId=0L
    private var afterCurrent=false
    private var eosReached=false
    private var nextNs=0L
    private var lastPtsNs=0L
    private var lastSwapNs=0L
    private var outputFrames=0L
    private var running=false
    private var closing=false
    private var repeated=0L
    private var skipped=0L
    private var configuration=RenderConfiguration()
    private var geometrySlot: Slot?=null
    private var previousOutputLogNs=0L
    private var captureNext: File?=null

    private inner class Slot(val uri: Uri, val id: Long) {
        val input=requireNotNull(egl).createInput()
        val surface=Surface(input.second)
        val decoder=LoopingMp4Provider(context,preloadFirstFrame=true,rendererOwnsRotation=true)
        val matrix=FloatArray(16)
        var ready=false
        var dirty=false
        var arrivals=0L
        var consumed=0L
        var timestampNs=0L
        var released=false
        var width=0
        var height=0
        var rotation=0
    }
    private fun event(kind: String, detail: String, slot: Slot?=active, pts: Long?=null, timings: Map<String,Long> = emptyMap()) {
        sink(FrameEvent(kind,detail,SystemClock.elapsedRealtimeNanos(),slot?.uri?.toString(),outputFrames,
            fps=1_000_000_000.0/periodNs,outputPresentationTimeNs=pts,surfaceTextureTimestampNs=slot?.timestampNs,switchId=slot?.id,timingsNs=timings))
    }
    override fun configureRender(configuration: RenderConfiguration) { handler.post { guard {
        this.configuration=configuration; geometrySlot=null
        event("render_configuration","$configuration")
    } } }
    private fun guard(block: ()->Unit) { try { block() } catch (t: Throwable) {
        event("pipeline_error",t.stackTraceToString()); stopOnThread()
    } }
    override fun start(output: Surface, source: Uri, events: (FrameEvent)->Unit) { handler.post { guard {
        check(egl == null) { "Create a new provider after Surface lifecycle ends" }
        sink=events
        egl=EglOutput(output)
        running=true
        event("egl_created","surface=${output.hashCode()} size=${egl!!.width}x${egl!!.height} periodNs=$periodNs")
        prepare(source,0L)
        nextNs=System.nanoTime()
        handler.post(render)
    } } }
    private fun prepare(uri: Uri, id: Long) {
        val begin=SystemClock.elapsedRealtimeNanos()
        val slot=Slot(uri,id); slots.add(slot); pending=slot
        slot.input.second.setOnFrameAvailableListener({
            if (!slot.released && running) {
                slot.arrivals++
                if (slot.dirty) event("source_coalesced","arrivals=${slot.arrivals}",slot)
                slot.dirty=true
                event("source_arrival","arrival=${slot.arrivals}",slot)
            }
        },handler)
        event("preload_start","id=$id",slot,timings=mapOf("slotSetupNs" to (SystemClock.elapsedRealtimeNanos()-begin)))
        slot.decoder.start(slot.surface,uri) { frame -> handler.post {
            val receipt=SystemClock.elapsedRealtimeNanos()
            if (!slot.released && running) {
                if(frame.kind == "video_format") {
                    slot.width=requireNotNull(frame.videoWidth); slot.height=requireNotNull(frame.videoHeight)
                    slot.rotation=frame.mediaRotationDegrees ?: 0
                }
                sink(frame.copy(detail="slot=${slot.id} ${frame.detail}",switchId=slot.id,receivedRealtimeNs=receipt,
                    timingsNs=frame.timingsNs+mapOf("handlerQueueNs" to (receipt-frame.elapsedRealtimeNs))))
                if (frame.kind == "loop" && slot == active && pending != null && afterCurrent) eosReached=true
                if (frame.kind == "decoder_error") {
                    event("preload_failed",frame.detail,slot)
                    if (pending == slot) pending=null
                    dispose(slot)
                }
            }
        } }
    }
    override fun switchSource(source: Uri)=switchNow(source)
    override fun switchNow(source: Uri)=request(source,false)
    override fun switchAfterCurrent(source: Uri)=request(source,true)
    private fun request(uri: Uri, after: Boolean) { handler.post { guard {
        if (!running) { event("switch_rejected","not running"); return@guard }
        pending?.let { event("switch_cancelled","superseded",it); dispose(it) }
        switchId++; afterCurrent=after; eosReached=false
        event("switch_request","id=$switchId mode=${if(after) "after" else "now"} next=$uri")
        prepare(uri,switchId)
    } } }
    private fun latch(slot: Slot): Long {
        val begin=SystemClock.elapsedRealtimeNanos()
        if (slot.dirty) {
            Trace.beginSection("Sarae.latch")
            slot.input.second.updateTexImage()
            slot.input.second.getTransformMatrix(slot.matrix)
            Trace.endSection()
            slot.timestampNs=slot.input.second.timestamp
            slot.consumed=slot.arrivals; slot.dirty=false; slot.ready=true
            event("source_latched","arrival=${slot.arrivals}",slot)
            if(slot.consumed == 1L) event("texture_transform","matrix=${slot.matrix.joinToString(",")} video=${slot.width}x${slot.height} mediaRotation=${slot.rotation}",slot)
        }
        return SystemClock.elapsedRealtimeNanos()-begin
    }
    private val render=object: Runnable {
        override fun run() { if (!running) return; guard {
            val tickStart=SystemClock.elapsedRealtimeNanos()
            val scheduled=nextNs
            val tickLate=maxOf(0L,System.nanoTime()-scheduled)
            val pendingLatch=pending?.let { latch(it) } ?: 0L
            val candidate=pending
            if (candidate != null && candidate.ready && (active == null || !afterCurrent || eosReached)) {
                event("switch_start","previous=${active?.uri} id=${candidate.id}",candidate)
                val old=active; active=candidate; pending=null
                candidate.decoder.resumePlayback()
                old?.let { dispose(it) }
                event("switch_complete","id=${candidate.id}",candidate)
            }
            var presentNs=0L
            active?.let { current ->
                val fresh=current.dirty
                val activeLatch=latch(current)
                if (current.ready) {
                    val output=requireNotNull(egl)
                    if(geometrySlot !== current) {
                        val matches=(configuration.expectedWidth==0 || configuration.expectedWidth==output.width) &&
                            (configuration.expectedHeight==0 || configuration.expectedHeight==output.height)
                        val applied=if(matches) configuration else RenderConfiguration()
                        output.setGeometry(current.width,current.height,current.rotation,applied)
                        captureNext=applied.diagnosticCaptureLabel?.let { File(context.getExternalFilesDir(null),"diagnostics/egl-$it.png") }
                        event("geometry_applied","source=${current.width}x${current.height} mediaRotation=${current.rotation} output=${output.width}x${output.height} profileMatches=$matches outputRotationDegrees=${applied.outputRotationDegrees} outputMirrorX=${applied.outputMirrorX} applied=$applied",current)
                        geometrySlot=current
                    }
                    if (!fresh) repeated++
                    val now=System.nanoTime()
                    val pts=maxOf(lastPtsNs+1,now)
                    val captured=captureNext
                    val timing=output.draw(current.input.first,current.matrix,pts,captured)
                    captureNext=null
                    if(captured != null) event("diagnostic_capture","file=${captured.name} calibrationOnly=true",current)
                    lastPtsNs=pts; outputFrames++
                    val swap=SystemClock.elapsedRealtimeNanos()
                    val logStart=SystemClock.elapsedRealtimeNanos()
                    event("output_present","gapNs=${if(lastSwapNs==0L) 0 else swap-lastSwapNs} repeated=$repeated skippedTicks=$skipped fresh=$fresh",current,pts,
                        mapOf("tickStartNs" to tickStart,"scheduledNs" to scheduled,"tickLateNs" to tickLate,
                            "pendingLatchNs" to pendingLatch,"activeLatchNs" to activeLatch,"drawNs" to timing.drawNs,"swapNs" to timing.swapNs,
                            "tickToPresentNs" to (swap-tickStart),"previousOutputLogNs" to previousOutputLogNs))
                    previousOutputLogNs=SystemClock.elapsedRealtimeNanos()-logStart
                    presentNs=swap
                    lastSwapNs=swap
                }
            }
            nextNs+=periodNs
            val now=System.nanoTime()
            if (nextNs < now) { val missed=(now-nextNs)/periodNs+1; skipped+=missed; nextNs+=missed*periodNs }
            handler.postDelayed(this,maxOf(1L,(nextNs-System.nanoTime())/1_000_000L))
            if(presentNs==0L || SystemClock.elapsedRealtimeNanos()-tickStart>=20_000_000L)
                event("renderer_tick","presented=${presentNs!=0L}",timings=mapOf("tickStartNs" to tickStart,"tickDurationNs" to (SystemClock.elapsedRealtimeNanos()-tickStart)))
        } }
    }
    private fun dispose(slot: Slot) {
        if (slot.released) return
        slot.released=true
        event("decoder_release_request","id=${slot.id}",slot)
        slot.decoder.closeAsync { handler.post { guard {
            slot.surface.release()
            egl?.releaseInput(slot.input.first,slot.input.second)
            slots.remove(slot)
            event("decoder_released","id=${slot.id}",slot)
            event("slot_released","id=${slot.id}",slot)
            finishStop()
        } } }
    }
    private fun stopOnThread() {
        running=false; handler.removeCallbacks(render)
        active=null; pending=null
        slots.toList().forEach { dispose(it) }
        finishStop()
    }
    private fun finishStop() {
        if (!running && slots.isEmpty()) {
            if (egl != null) { egl?.close(); egl=null; event("egl_released","frames=$outputFrames") }
            if (closing) thread.quitSafely()
        }
    }
    override fun stop() { handler.post { stopOnThread() } }
    override fun close() { handler.post { closing=true; stopOnThread() } }
}
