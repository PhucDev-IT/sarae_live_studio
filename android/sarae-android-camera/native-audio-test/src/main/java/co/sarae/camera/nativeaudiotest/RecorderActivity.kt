package co.sarae.camera.nativeaudiotest

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.widget.TextView
import co.sarae.camera.audio.AudioCursor
import co.sarae.camera.audio.MediaAudioEngine
import co.sarae.camera.audio.PcmBufferWriter
import co.sarae.camera.audio.PcmEncoding
import co.sarae.camera.audio.PcmFormat
import co.sarae.camera.nativeaudio.NativeRecorder as Native
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** Test launcher, not a controller feature. Owns only its own OpenSL recorder. */
class RecorderActivity : Activity() {
    companion object {
        /** Only this owned-lab method is hooked by the observation module. */
        @JvmStatic fun observationApi():Long=0L
        @JvmStatic fun hookControl(command:Int):LongArray=longArrayOf(command.toLong())
        @JvmStatic fun hookEvents():LongArray=longArrayOf()
        @JvmStatic fun bindingCheck(profile:String):LongArray=longArrayOf(profile.length.toLong())
        @JvmStatic fun hookEvents20():LongArray=longArrayOf()
        @JvmStatic fun hookCode20():ByteArray=byteArrayOf()
        @JvmStatic fun bindingError():String=""
        @JvmStatic fun ownerPin(handle:Long) {require(handle!=0L)}
    }
    private val cancelled=AtomicBoolean()
    private lateinit var status:TextView
    override fun onCreate(savedInstanceState:Bundle?) {
        super.onCreate(savedInstanceState)
        status=TextView(this).apply {text="Native PCM verification"};setContentView(status)
        if(checkSelfPermission(Manifest.permission.RECORD_AUDIO)!=PackageManager.PERMISSION_GRANTED) {
            status.text="RECORD_AUDIO permission required before launch";return
        }
        val seconds=intent.getIntExtra("seconds",12)
        require(seconds in 10..20)
        Thread({if(intent.getBooleanExtra("phase20",false))runPhase20(seconds) else runSuite(seconds)},"native-audio-test").start()
    }
    override fun onDestroy() {cancelled.set(true);super.onDestroy()}
    override fun onStop() {cancelled.set(true);super.onStop()}
    private fun runSuite(seconds:Int) {
        val runId=intent.getStringExtra("runId") ?: "manual-${System.currentTimeMillis()}"
        require(runId.matches(Regex("[a-zA-Z0-9-]{1,48}")))
        val switchAba=intent.getBooleanExtra("bridgeAba",false)
        val loopOnly=intent.getBooleanExtra("loopOnly",false)
        val phase19=intent.getBooleanExtra("phase19",false)
        val phase17=phase19||intent.getBooleanExtra("phase17",false)
        require(!(switchAba || loopOnly) || !phase17) {"bridge tests use the ordinary owned recorder"}
        require(!(switchAba && loopOnly))
        val directory=File(getExternalFilesDir(null),"${if(switchAba || loopOnly) "native-bridge" else if(phase19) "phase19" else if(phase17) "phase17" else "phase15"}/$runId").apply {mkdirs()}
        val logFile=File(directory,"events.jsonl")
        logFile.bufferedWriter().use {writer->
            val lock=Any()
            val log:(String,Map<String,Any?>)->Unit={event,fields->
                val row=JSONObject().put("event",event).put("emissionNs",SystemClock.elapsedRealtimeNanos()).put("thread",Thread.currentThread().name)
                fields.forEach {(key,value)->row.put(key,JSONObject.wrap(value))}
                synchronized(lock) {writer.write(row.toString());writer.newLine()}
            }
            try {
                val source=File(getExternalFilesDir(null),"source.mp4");check(source.isFile)
                val extractor=MediaExtractor()
                val trackDurationNs=try {
                    extractor.setDataSource(source.absolutePath)
                    val track=(0 until extractor.trackCount).first {extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/")==true}
                    val format=extractor.getTrackFormat(track)
                    log("source",mapOf("path" to source.absolutePath,"format" to format.toString()))
                    // Fixed callback blocks use the complete 10 ms portion of this short fixture.
                    format.getLong(MediaFormat.KEY_DURATION)/10_000L*10_000_000L
                } finally {extractor.release()}
                // In this owned harness the video timeline is synthetic. A clipped
                // audio-only MP4 can expose an edit-list-adjusted track duration
                // that differs from the actual output/video timeline. Production
                // MediaAudioEngine receives real source video PTS instead.
                val suppliedLoopNs=intent.getLongExtra("loopDurationNs",0L)
                val durationNs=if(suppliedLoopNs>0L) suppliedLoopNs else trackDurationNs
                log("loop_clock",mapOf("trackDurationNs" to trackDurationNs,"videoTimelineLoopNs" to durationNs,
                    "explicitVideoTimeline" to (suppliedLoopNs>0L)))
                require(durationNs>=1_000_000_000L)
                log("suite_start",mapOf("secondsPerMode" to seconds,"loopNs" to durationNs,"rate" to Native.RATE,"channels" to Native.CHANNELS,
                    "runId" to runId,"clock" to "CLOCK_BOOTTIME / elapsedRealtimeNanos",
                    "pcmEncoding" to "S16_LE","framesPerBuffer" to Native.FRAMES,"bufferBytes" to Native.PCM_BYTES,"queueBuffers" to 2,
                    "replacementCapacityBlocks" to 32,"captureCapacityBlocks" to 128,"timeline" to "controlled callback-block timeline; no video pipeline",
                    "bridgeCapabilities" to Native.bridgeCapabilities().toList(),"switchAba" to switchAba))
                // Invoke the actual @JvmStatic bridge that the module hooks,
                // rather than Kotlin's direct Companion call.
                val api=if(phase17) (RecorderActivity::class.java.getMethod("observationApi").invoke(null) as Long).also {check(it!=0L) {"LSPatch observation module not attached"}} else 0L
                if(phase17)log("observer_library_initialized",mapOf("stats" to Native.observerLibraryStats(api).toList(),"api" to api))
                if(phase19) {
                    val initial=hook(0);log("native_hook_initial",mapOf("stats" to initial.toList()))
                    check(initial.size==21 && initial[0]==2L && initial[1]==1L && initial[2]==1L && initial[3]==1L) {"Pinned native API v2 / owned library notification unavailable"}
                    val rejected=hook(2);check(rejected[5]==-22L && rejected[18]==0L)
                    log("hook_candidate_rejected",mapOf("stats" to rejected.toList(),"reason" to "invalid candidate rejected before framework call"))
                    Native.probeEnable(true)
                }
                val cases=if(loopOnly) listOf("VIDEO_LOOP" to 1) else if(switchAba) listOf("VIDEO_ABA" to 1) else if(phase17) listOf("BASELINE" to 0,"OBSERVED" to 0) else listOf("MICROPHONE" to 0,"VIDEO" to 1,"MUTED" to 2)
                for((name,mode) in cases) {
                    check(!cancelled.get()) {"Activity stopped"}
                    if(phase19 && name=="OBSERVED") {
                        val installed=hook(1);log("native_hook_installed",mapOf("stats" to installed.toList(),"probeBefore" to Native.probeStats().toList()))
                        check(installed[4]==1L && installed[5]==0L && installed[14]!=0L && installed[18]==1L) {"Owned native hook installation failed"}
                        val test=Native.probeSelfTest();log("trampoline_reentrant_test",mapOf("result" to test.toList(),"stats" to hook(0).toList()))
                        check(test[1]-test[0]==128L && test[2]==0L)
                        drainHooks {event,fields->log(event,fields+mapOf("mode" to "REENTRANCY"))}
                    }
                    runOnUiThread {status.text="Recording $name locally; no TikTok / no LIVE"}
                    record(name,mode,seconds,source,durationNs,directory,if(name=="OBSERVED") api else 0L,switchAba) {event,fields->log(event,fields+mapOf("mode" to name))}
                }
                if(phase19) {
                    val removed=hook(3);log("native_hook_removed",mapOf("stats" to removed.toList()))
                    check(removed[4]==0L && removed[5]==0L && removed[8]==0L && removed[20]==1L) {"Unhook / original bytes restoration failed"}
                    val before=hook(0)[6];val test=Native.probeSelfTest();val after=hook(0)[6]
                    log("post_unhook_test",mapOf("result" to test.toList(),"entriesBefore" to before,"entriesAfter" to after))
                    check(test[1]-test[0]==128L && test[2]==0L && before==after)
                    Native.probeEnable(false)
                }
                if(phase17) {
                    Native.observerShutdown(api)
                    log("observer_library_cleanup",mapOf("stats" to Native.observerLibraryStats(api).toList(),"note" to "all relays freed; JNI library remains mapped until process exit"))
                }
                log("suite_complete",emptyMap())
                File(directory,"DONE").writeText("PASS: completed; analyze MP4 files before claiming audio gates")
                runOnUiThread {status.text="Complete: MP4 outputs ready for offline verification"}
            } catch(t:Throwable) {
                log("suite_error",mapOf("error" to t.stackTraceToString()))
                File(directory,"ERROR").writeText(t.stackTraceToString())
                runOnUiThread {status.text="Failed: ${t.message}"}
            } finally {
                if(phase19)runCatching {Native.probeEnable(false);val final=hook(3);log("native_hook_final",mapOf("stats" to final.toList()))}
                synchronized(lock) {writer.flush()}
            }
        }
    }

    private fun hook(command:Int)=RecorderActivity::class.java.getMethod("hookControl",Int::class.javaPrimitiveType).invoke(null,command) as LongArray
    private fun binding(profile:JSONObject)=RecorderActivity::class.java.getMethod("bindingCheck",String::class.java).invoke(null,profile.toString()) as LongArray
    private fun drain20(log:(String,Map<String,Any?>)->Unit) {
        val values=RecorderActivity::class.java.getMethod("hookEvents20").invoke(null) as LongArray
        check(values.size%8==0)
        for(offset in values.indices step 8)log("binding_callback",mapOf("values" to values.slice(offset until offset+8),"receiptNs" to SystemClock.elapsedRealtimeNanos()))
    }
    private fun runPhase20(seconds:Int) {
        val id=requireNotNull(intent.getStringExtra("runId"));require(id.matches(Regex("[a-zA-Z0-9-]{1,48}")))
        val dir=File(getExternalFilesDir(null),"phase20/$id").apply {mkdirs()}
        File(dir,"events.jsonl").bufferedWriter().use {writer->
            val log:(String,Map<String,Any?>)->Unit={event,fields->
                val row=JSONObject().put("event",event).put("emissionNs",SystemClock.elapsedRealtimeNanos())
                fields.forEach {(k,v)->row.put(k,JSONObject.wrap(v))};writer.write(row.toString());writer.newLine()
            }
            try {
                val api=RecorderActivity::class.java.getMethod("observationApi").invoke(null) as Long
                check(api!=0L);log("observer_library_initialized",mapOf("stats" to Native.observerLibraryStats(api).toList(),"api" to api))
                log("binding_initial",mapOf("stats" to hook(20).toList()))
                val target=JSONObject(File(getExternalFilesDir(null),"phase20-target.json").readText())
                val absent=binding(target);log("target_missing",mapOf("stats" to absent.toList()));check(absent[6]==-40L && absent[11]==0L && absent[30]==0L)
                val profile=JSONObject(File(getExternalFilesDir(null),"phase20-owned.json").readText())
                fun verify(p:JSONObject,event:String,code:Long) {val s=binding(p);log(event,mapOf("stats" to s.toList()));check(s[6]==code && s[11]==0L)}
                verify(JSONObject(profile.toString()).put("sha256","0".repeat(64)),"reject_hash",-31)
                verify(JSONObject(profile.toString()).put("buildId","0".repeat(40)),"reject_build",-32)
                verify(JSONObject(profile.toString()).put("entryHex","0".repeat(64)),"reject_fingerprint",-33)
                val ambiguous=hook(29);log("ambiguous_policy_unit",mapOf("stats" to ambiguous.toList(),"synthetic" to true));check(ambiguous[6]==-34L)
                val direct=binding(profile)
                log("direct_module_pin",mapOf("stats" to direct.toList(),"error" to RecorderActivity::class.java.getMethod("bindingError").invoke(null)))
                if(direct[6]==-43L) {
                    val pin=Native.pin20(true);check(pin!=0L)
                    RecorderActivity::class.java.getMethod("ownerPin",Long::class.javaPrimitiveType).invoke(null,pin)
                    log("owned_owner_pin",mapOf("handle" to pin,"scope" to "owned cooperative lifetime only; not a TikTok fix"))
                }else check(direct[6]==0L)
                verify(profile,"binding_valid",0)
                val rejected=hook(22);log("engine_null_rejection",mapOf("stats" to rejected.toList()));check(rejected[18]==-1L && rejected[19]==1L)
                val baseline=Native.probe20(3);log("unhooked_pattern_test",mapOf("values" to baseline.toList()));check(baseline[1]-baseline[0]==128L && baseline[5]==0L)
                val installed=hook(21);log("pattern_installed",mapOf("stats" to installed.toList()));check(installed[11]==1L && installed[6]==0L)
                val duplicate=hook(27);log("engine_duplicate_rejection",mapOf("stats" to duplicate.toList()));check(duplicate[20]==-1L && duplicate[21]==1L && duplicate[11]==1L)
                val nested=Native.probe20(3);log("nested_pattern_test",mapOf("values" to nested.toList(),"stats" to hook(20).toList()));check(nested[1]-nested[0]==128L && nested[5]==0L)
                drain20 {event,fields->log(event,fields+mapOf("mode" to "REENTRANCY"))}
                Native.probe20(1)
                val source=File(getExternalFilesDir(null),"source.mp4")
                record("OBSERVED",0,seconds,source,1_000_000_000L,dir,api) {event,fields->log(event,fields+mapOf("mode" to "OBSERVED"))}
                Native.probe20(2)
                val removed=hook(23);log("pattern_removed",mapOf("stats" to removed.toList()));check(removed[11]==0L && removed[22]==1L)
                val before=removed[12];val after=Native.probe20(3);val final=hook(20)
                log("pattern_after_unhook",mapOf("values" to after.toList(),"stats" to final.toList()));check(after[5]==0L && final[12]==before)
                check(Native.probe20(4)[5]==0L)
                val branch=hook(25);log("branch_installed",mapOf("stats" to branch.toList()));check(branch[6]==0L)
                val branchTest=Native.probe20(4);log("branch_test",mapOf("values" to branchTest.toList(),"stats" to hook(20).toList()));check(branchTest[5]==0L)
                val branchRemoved=hook(28);log("branch_removed",mapOf("stats" to branchRemoved.toList()));check(branchRemoved[33]==1L)
                val code=RecorderActivity::class.java.getMethod("hookCode20").invoke(null) as ByteArray;File(dir,"code.bin").writeBytes(code)
                Native.observerShutdown(api);log("observer_library_cleanup",mapOf("stats" to Native.observerLibraryStats(api).toList()))
                val cleanup=hook(30);log("binding_cleanup",mapOf("stats" to cleanup.toList()));check(cleanup[32]==0L && cleanup[11]==0L)
                Native.pin20(false)
                File(dir,"DONE").writeText("complete; analyze owned outputs, not a TikTok PASS")
            }catch(t:Throwable){log("suite_error",mapOf("error" to t.stackTraceToString()));File(dir,"ERROR").writeText(t.stackTraceToString())}
            finally {runCatching {Native.probe20(2);hook(26);hook(23);hook(28);hook(30);Native.pin20(false)};writer.flush()}
        }
    }
    private fun drainHooks(log:(String,Map<String,Any?>)->Unit) {
        val values=RecorderActivity::class.java.getMethod("hookEvents").invoke(null) as LongArray
        check(values.size%11==0)
        for(offset in values.indices step 11)log("native_hook_event",mapOf("values" to values.slice(offset until offset+11),"receiptNs" to SystemClock.elapsedRealtimeNanos()))
    }

    private fun record(name:String,mode:Int,seconds:Int,source:File,loopNs:Long,directory:File,observerApi:Long=0L,
                       switchAba:Boolean=false,log:(String,Map<String,Any?>)->Unit) {
        val hooked=intent.getBooleanExtra("phase19",false)&&name=="OBSERVED"
        val bindingHook=intent.getBooleanExtra("phase20",false)&&name=="OBSERVED"
        val handle=if(observerApi!=0L)Native.createObserved(mode,observerApi) else Native.create(mode)
        val observerToken=if(observerApi!=0L)Native.observerToken(handle) else 0L
        val observation=ByteBuffer.allocateDirect(80).order(ByteOrder.LITTLE_ENDIAN)
        fun drainObserver() {
            if(observerToken==0L)return
            var count=0
            while(count<256 && Native.pollObservation(observerApi,observerToken,observation)) {
                val receipt=SystemClock.elapsedRealtimeNanos();observation.position(0)
                log("observer_callback",mapOf("sequence" to observation.long,"entryNs" to observation.long,"originalStartNs" to observation.long,
                    "originalEndNs" to observation.long,"exitNs" to observation.long,"queue" to observation.long,"forwardedContext" to observation.long,
                    "originalFunction" to observation.long,"nativeTid" to observation.long,"flags" to observation.long,"receiptNs" to receipt))
                count++
            }
        }
        val feederRunning=AtomicBoolean(false)
        val feederFailure=AtomicReference<Throwable?>()
        var feeder:Thread?=null
        var engine:MediaAudioEngine?=null
        var encoder:AacOutput?=null
        try {
            if(observerToken!=0L) {
                log("observer_registered",mapOf("stats" to Native.observerStats(observerApi,observerToken).toList(),"queueCapacity" to 256,"pcmModified" to false))
                val rejection=runCatching {Native.observerShutdown(observerApi)}.exceptionOrNull()
                check(rejection is IllegalStateException) {"Active observer shutdown was not rejected"}
                log("expected_native_error",mapOf("error" to rejection.message,"intentional" to true))
            }
            log("recorder_created",mapOf("mode" to name,"stats" to Native.stats(handle).toList(),"registeredCallback" to true,
                "initialEnqueues" to 2,"ownership" to "OpenSL until FIFO completion; snapshot before re-enqueue",
                "bridge" to Native.bridgeStats(handle).toList()))
            encoder=AacOutput(File(directory,"$name.mp4"),log)
            if(mode==1) {
                val uri=Uri.fromFile(source).toString()
                val alternate=File(getExternalFilesDir(null),"sourceB.mp4")
                val alternateUri=if(switchAba) {check(alternate.isFile) {"sourceB.mp4 required for A/B/A"};Uri.fromFile(alternate).toString()} else ""
                val alternateLoopNs=if(switchAba) {
                    val extractor=MediaExtractor()
                    try {
                        extractor.setDataSource(alternate.absolutePath)
                        val track=(0 until extractor.trackCount).first {extractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/")==true}
                        extractor.getTrackFormat(track).getLong(MediaFormat.KEY_DURATION)/10_000L*10_000_000L
                    } finally {extractor.release()}
                } else 0L
                val audio=MediaAudioEngine(this) {event,fields->log(event,fields+mapOf("mode" to name))};engine=audio
                val prepared=CountDownLatch(1)
                audio.beginVideo(15);audio.prepareBeforeSwitch(15,1,uri) {prepared.countDown()}
                check(prepared.await(2,TimeUnit.SECONDS)) {"decoder preparation callback timeout"}
                if(switchAba) {
                    audio.videoFrame(15,1,uri,0L,SystemClock.elapsedRealtimeNanos())
                    val nextPrepared=CountDownLatch(1)
                    audio.prepareBeforeSwitch(15,2,alternateUri) {nextPrepared.countDown()}
                    check(nextPrepared.await(2,TimeUnit.SECONDS)) {"B decoder preparation callback timeout"}
                    log("bridge_next_prepared",mapOf("source" to "B","loopNs" to alternateLoopNs))
                }
                feederRunning.set(true)
                feeder=Thread({
                    try {
                        val cursor=AudioCursor();val pcm=ShortArray(Native.FRAMES*Native.CHANNELS)
                        var block=0L
                        var sourceBlock=0L
                        var currentId=1L
                        var currentUri=uri
                        var currentLoopNs=loopNs
                        var generation=1L
                        val firstSwitch=seconds*100L/3
                        val secondSwitch=seconds*200L/3
                        while(feederRunning.get()) {
                            if(Native.inputDepth(handle)>=8) {Thread.sleep(2);continue}
                            val now=SystemClock.elapsedRealtimeNanos()
                            if(switchAba && (block==firstSwitch || block==secondSwitch)) {
                                // A's old deck has advanced several seconds. A fresh key lets
                                // the existing engine preload its head before returning to A.
                                val nextId=if(block==firstSwitch) 2L else 3L
                                val nextUri=if(nextId==2L) alternateUri else uri
                                currentId=nextId;currentUri=nextUri;currentLoopNs=if(nextId==2L) alternateLoopNs else loopNs
                                sourceBlock=0L;generation++
                                audio.videoFrame(15,currentId,currentUri,0L,now-10_000_000L)
                                check(Native.commitGeneration(handle,generation)) {"generation commit rejected"}
                                log("bridge_source_commit",mapOf("block" to block,"source" to if(nextId==2L) "B" else "A",
                                    "generation" to generation,"commitNs" to now,"bridge" to Native.bridgeStats(handle).toList()))
                                if(nextId==2L) audio.prepareBeforeSwitch(15,3,uri) {
                                    log("bridge_next_prepared",mapOf("source" to "A","generation" to 3))
                                }
                            }
                            val mediaNs=sourceBlock*10_000_000L%currentLoopNs
                            audio.videoFrame(15,currentId,currentUri,mediaNs,now-10_000_000L)
                            val result=audio.read(PcmFormat(Native.RATE,Native.CHANNELS),Native.FRAMES,cursor,now)
                            PcmBufferWriter.write(pcm,0,result.pcm,PcmEncoding.S16,pcm.size)
                            check(Native.pushGeneration(handle,pcm,generation)) {"unexpected full replacement queue"}
                            log("feeder_block",mapOf("block" to block,"mediaNs" to mediaNs,"epoch" to result.epoch,
                                "missingFrames" to result.missingFrames,"depth" to Native.inputDepth(handle),"driftNs" to result.driftNs,
                                "source" to if(currentId==2L) "B" else "A","generation" to generation))
                            block++;sourceBlock++
                        }
                    } catch(t:Throwable) {feederFailure.set(t)}
                },"native-pcm-feeder").apply {start()}
                val deadline=SystemClock.elapsedRealtime()+1500
                while(Native.inputDepth(handle)<8 && SystemClock.elapsedRealtime()<deadline && feederFailure.get()==null) Thread.sleep(5)
                check(Native.inputDepth(handle)==8) {"PCM prefill unavailable: ${feederFailure.get()}"}
                log("pcm_prefilled",mapOf("blocks" to 8,"frames" to 3840))
            }
            val packet=ByteBuffer.allocateDirect(Native.PACKET_BYTES).order(ByteOrder.LITTLE_ENDIAN)
            val mic=ByteArray(Native.PCM_BYTES);val delivered=ByteArray(Native.PCM_BYTES)
            val start=SystemClock.elapsedRealtimeNanos()
            if(hooked) {
                hook(4);val denied=hook(3);check(denied[5]==-16L && denied[4]==1L)
                log("active_unhook_rejected",mapOf("stats" to denied.toList()))
            }
            if(bindingHook)hook(24)
            Native.start(handle)
            log("record_state",mapOf("mode" to name,"state" to "RECORDING","stats" to Native.stats(handle).toList()))
            var count=0L
            File(directory,"$name-mic.s16").outputStream().buffered().use {micFile->
                File(directory,"$name-delivered.s16").outputStream().buffered().use {deliveredFile->
                    fun drain() {
                        drainObserver()
                        if(hooked)drainHooks(log)
                        if(bindingHook)drain20(log)
                        var drained=0
                        while(drained<128 && Native.poll(handle,packet)) {
                            val receipt=SystemClock.elapsedRealtimeNanos()
                            packet.position(0)
                            val sequence=packet.long;val callback=packet.long;val work=packet.long;val address=packet.long
                            val index=packet.int;val missing=packet.int;val enqueue=packet.int;val depth=packet.int
                            packet.get(mic);packet.get(delivered)
                            micFile.write(mic);deliveredFile.write(delivered)
                            encoder!!.write(delivered,sequence*10_000L)
                            log("buffer_complete",mapOf("mode" to name,"sequence" to sequence,"callbackNs" to callback,
                                "receiptNs" to receipt,"callbackWorkNs" to work,"address" to address,"index" to index,
                                "missingFrames" to missing,"enqueueResult" to enqueue,"replacementDepth" to depth,"encodedPtsUs" to sequence*10_000L))
                            count++
                            drained++
                        }
                    }
                    while(SystemClock.elapsedRealtimeNanos()-start<seconds*1_000_000_000L) {
                        check(!cancelled.get()) {"Activity stopped during capture"}
                        feederFailure.get()?.let {throw it}
                        drain();Thread.sleep(2)
                    }
                    Native.stop(handle)
                    log("record_state",mapOf("mode" to name,"state" to "STOPPED","stats" to Native.stats(handle).toList()))
                    if(observerToken!=0L) {
                        val stopped=Native.observerStats(observerApi,observerToken)
                        Thread.sleep(200)
                        val later=Native.observerStats(observerApi,observerToken)
                        log("observer_after_stop",mapOf("before" to stopped.toList(),"after" to later.toList(),"windowMs" to 200))
                        check(stopped[0]==later[0] && later[5]==0L) {"Callbacks continued after STOP"}
                    }
                    if(hooked)log("hook_after_stop",mapOf("stats" to hook(0).toList(),"probe" to Native.probeStats().toList()))
                    if(bindingHook)log("binding_after_stop",mapOf("stats" to hook(20).toList(),"probe" to Native.probe20(0).toList()))
                    feederRunning.set(false);feeder?.join(2000);check(feeder?.isAlive!=true)
                    drain()
                }
            }
            encoder.finish()
            log("record_complete",mapOf("mode" to name,"blocks" to count,"durationNs" to SystemClock.elapsedRealtimeNanos()-start,
                "stats" to Native.stats(handle).toList(),"bridge" to Native.bridgeStats(handle).toList(),"probe" to Native.probeStats().toList()))
        } finally {
            Native.stop(handle)
            feederRunning.set(false);feeder?.join(2000)
            // A live feeder must never retain a pointer to freed native storage.
            if(feeder?.isAlive==true) {
                log("teardown_error",mapOf("error" to "feeder did not exit; retained native storage instead of use-after-free"))
            } else {
                Native.destroy(handle);log("recorder_destroyed",mapOf("mode" to name))
                if(observerToken!=0L) {
                    val destroyed=Native.observerStats(observerApi,observerToken)
                    Thread.sleep(200)
                    val later=Native.observerStats(observerApi,observerToken)
                    drainObserver()
                    log("observer_after_destroy",mapOf("before" to destroyed.toList(),"after" to later.toList(),"windowMs" to 200))
                    check(destroyed[0]==later[0] && later[5]==0L && later[6]==0L && later[7]==1L) {"Invalid callback lifecycle after DESTROY"}
                    Native.releaseObservation(observerApi,observerToken)
                    log("observer_released",mapOf("globalStats" to Native.observerLibraryStats(observerApi).toList()))
                }
                if(hooked) {
                    log("hook_after_destroy",mapOf("stats" to hook(0).toList(),"probe" to Native.probeStats().toList()))
                    hook(5)
                }
                if(bindingHook){log("binding_after_destroy",mapOf("stats" to hook(20).toList(),"probe" to Native.probe20(0).toList()));hook(26)}
            }
            engine?.close();encoder?.close()
        }
    }
}

/** AAC encoding is downstream of the completed-buffer handoff, never on OpenSL's thread. */
private class AacOutput(file:File,private val log:(String,Map<String,Any?>)->Unit):AutoCloseable {
    private val codec=MediaCodec.createEncoderByType("audio/mp4a-latm")
    private val muxer=MediaMuxer(file.absolutePath,MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
    private val info=MediaCodec.BufferInfo()
    private var track=-1
    private var muxStarted=false
    private var lastPts=0L
    private var finished=false
    init {
        val format=MediaFormat.createAudioFormat("audio/mp4a-latm",Native.RATE,Native.CHANNELS).apply {
            setInteger(MediaFormat.KEY_AAC_PROFILE,MediaCodecInfo.CodecProfileLevel.AACObjectLC)
            setInteger(MediaFormat.KEY_BIT_RATE,192000)
            setInteger(MediaFormat.KEY_MAX_INPUT_SIZE,Native.PCM_BYTES*4)
        }
        codec.configure(format,null,null,MediaCodec.CONFIGURE_FLAG_ENCODE);codec.start()
        log("encoder_created",mapOf("codec" to codec.name,"format" to format.toString(),"output" to file.name))
    }
    fun write(pcm:ByteArray,ptsUs:Long) {
        val deadline=SystemClock.elapsedRealtime()+1000
        var index=codec.dequeueInputBuffer(0)
        while(index<0) {drain(false);check(SystemClock.elapsedRealtime()<deadline) {"AAC input starvation"};index=codec.dequeueInputBuffer(5000)}
        val buffer=requireNotNull(codec.getInputBuffer(index));buffer.clear();check(buffer.remaining()>=pcm.size);buffer.put(pcm)
        codec.queueInputBuffer(index,0,pcm.size,ptsUs,0);lastPts=ptsUs+10_000L;drain(false)
    }
    private fun drain(wait:Boolean):Boolean {
        while(true) {
            val index=codec.dequeueOutputBuffer(info,if(wait) 10_000 else 0)
            if(index==MediaCodec.INFO_TRY_AGAIN_LATER) return false
            if(index==MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                check(!muxStarted);track=muxer.addTrack(codec.outputFormat);muxer.start();muxStarted=true
                log("encoder_format",mapOf("format" to codec.outputFormat.toString()));continue
            }
            if(index>=0) {
                val eos=info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM!=0
                try {
                    if(info.size>0 && info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG==0) {
                        check(muxStarted)
                        val out=requireNotNull(codec.getOutputBuffer(index));out.position(info.offset);out.limit(info.offset+info.size)
                        muxer.writeSampleData(track,out,info)
                    }
                } finally {codec.releaseOutputBuffer(index,false)}
                if(eos)return true
            }
        }
    }
    fun finish() {
        val deadline=SystemClock.elapsedRealtime()+2000
        var index=codec.dequeueInputBuffer(10_000)
        while(index<0) {drain(false);check(SystemClock.elapsedRealtime()<deadline);index=codec.dequeueInputBuffer(10_000)}
        codec.queueInputBuffer(index,0,0,lastPts,MediaCodec.BUFFER_FLAG_END_OF_STREAM)
        while(!drain(true))check(SystemClock.elapsedRealtime()<deadline) {"AAC EOS timeout"}
        finished=true
        log("encoder_eos",mapOf("inputEndPtsUs" to lastPts))
    }
    override fun close() {
        try {codec.stop()} finally {codec.release()}
        try {if(muxStarted)muxer.stop()} finally {muxer.release()}
        log("encoder_released",mapOf("finished" to finished))
    }
}
