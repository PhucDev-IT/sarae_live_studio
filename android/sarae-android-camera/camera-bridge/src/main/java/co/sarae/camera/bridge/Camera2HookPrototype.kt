package co.sarae.camera.bridge

import android.app.Application
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.ImageFormat
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CaptureResult
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.media.ImageReader
import android.net.Uri
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.util.Log
import android.util.SparseArray
import android.view.Surface
import de.robv.android.xposed.XC_MethodHook
import de.robv.android.xposed.XposedBridge
import de.robv.android.xposed.XposedHelpers
import java.io.File
import java.io.BufferedWriter
import org.json.JSONObject
import java.lang.ref.WeakReference

/** Scoped experiment: one default Camera2 preview output in an explicitly allowed lab app. */
class Camera2HookPrototype(private val scopePackage: String = "co.sarae.camera.consumer",
                           private val submissionRemap: Boolean = false,
                           private val factory: (Context) -> CameraFrameProvider) {
    private var context: Context? = null
    private var original: Surface? = null
    private var dummy: ImageReader? = null
    private var provider: CameraFrameProvider? = null
    private val lifecycleLock = Any()
    private var redirectedDevice: CameraDevice? = null
    private var retiredDevice: WeakReference<CameraDevice>? = null
    private var sessionGeneration=0
    private var partialResultCount=1
    private var captureStarts=0L
    private var captureResults=0L
    private var observerErrors=0L
    private val worker = HandlerThread("sarae-hook-drain").apply { start() }
    private val handler = Handler(worker.looper)
    private var source = "A"
    private var jsonLog: BufferedWriter? = null
    private var logCount=0L
    private var cameraConfiguration=RenderConfiguration()

    @Synchronized private fun event(kind: String, detail: String, frame: FrameEvent?=null) {
        val ns=SystemClock.elapsedRealtimeNanos()
        val row=JSONObject().put("kind",kind).put("ns",ns).put("detail",detail)
            .put("pid",android.os.Process.myPid()).put("source",frame?.source)
            .put("outputPtsNs",frame?.outputPresentationTimeNs).put("sourceTextureNs",frame?.surfaceTextureTimestampNs)
            .put("switchId",frame?.switchId).put("sourcePtsUs",frame?.presentationTimeUs)
            .put("emittedNs",frame?.elapsedRealtimeNs ?: ns).put("receivedNs",frame?.receivedRealtimeNs ?: ns)
            .put("timingsNs",JSONObject(frame?.timingsNs ?: emptyMap<String,Long>()))
            .put("videoWidth",frame?.videoWidth).put("videoHeight",frame?.videoHeight).put("mediaRotationDegrees",frame?.mediaRotationDegrees)
        val serialized=row.toString()
        val writeStart=SystemClock.elapsedRealtimeNanos()
        val shouldFlush=(logCount+1)%30L==0L || kind.startsWith("switch") || kind.contains("error") || kind in listOf("egl_released","slot_released","camera_close")
        jsonLog?.apply { write(serialized); newLine(); logCount++ }
        val flushStart=SystemClock.elapsedRealtimeNanos()
        if(shouldFlush) jsonLog?.flush()
        val logcatStart=SystemClock.elapsedRealtimeNanos()
        if (kind !in listOf("output_present","source_arrival","source_latched","frame_rendered","decoder_tick","renderer_tick") || logCount%300L==0L)
            Log.i("SaraeBridge", "ns=$ns event=$kind $detail")
        val end=SystemClock.elapsedRealtimeNanos()
        if(shouldFlush || end-ns>=1_000_000L) {
            val timing=JSONObject().put("kind","logger_timing").put("ns",end).put("relatedKind",kind).put("relatedNs",ns)
                .put("serializeNs",writeStart-ns).put("writeNs",flushStart-writeStart)
                .put("flushNs",logcatStart-flushStart).put("logcatNs",end-logcatStart).put("totalNs",end-ns)
                .put("tid",android.os.Process.myTid())
            jsonLog?.apply { write(timing.toString()); newLine() }
        }
    }

    private fun renderConfiguration(ctx: Context): RenderConfiguration {
        val file=File(ctx.getExternalFilesDir(null),"sarae-render.json")
        if(!file.exists()) return cameraConfiguration
        return try {
            val value=JSONObject(file.readText())
            cameraConfiguration.copy(rotationOverride=if(value.has("rotationOverride") && !value.isNull("rotationOverride")) value.getInt("rotationOverride") else null,
                scaleMode=RenderConfiguration.ScaleMode.valueOf(value.optString("scaleMode","FIT")),mirrorX=value.optBoolean("mirrorX",false),
                expectedWidth=value.optInt("expectedWidth",0),expectedHeight=value.optInt("expectedHeight",0),
                autoCameraOrientation=value.optBoolean("autoCameraOrientation",cameraConfiguration.autoCameraOrientation),
                diagnosticCaptureLabel=if(value.has("diagnosticCaptureLabel")) value.getString("diagnosticCaptureLabel") else null)
        } catch(t: Exception) { event("configuration_error",t.toString()); cameraConfiguration }
    }

    fun install() {
        XposedHelpers.findAndHookMethod(Application::class.java, "attach", Context::class.java,
            object : XC_MethodHook() {
                override fun afterHookedMethod(param: MethodHookParam) {
                    val ctx = param.args[0] as Context
                    context = ctx
                    val dir=File(ctx.getExternalFilesDir(null),if(scopePackage == "co.sarae.camera.consumer") "phase3" else "phase6").apply { mkdirs() }
                    jsonLog=File(dir,"bridge-${android.os.Process.myPid()}.jsonl").bufferedWriter()
                    ctx.registerReceiver(object : BroadcastReceiver() {
                        override fun onReceive(context: Context, intent: Intent) {
                            if(intent.action == "co.sarae.camera.TEST_RETIRED_CLOSE") {
                                val retired=synchronized(lifecycleLock) {
                                    retiredDevice?.get()?.takeIf {provider!=null && redirectedDevice!=null && it!==redirectedDevice}
                                }
                                if(retired==null)event("retired_close_unavailable","generation=$sessionGeneration noLiveOwnerOrRetiredObject=true")
                                else {
                                    event("retired_close_replay","generation=$sessionGeneration closing=${System.identityHashCode(retired)} owner=${redirectedDevice?.let{System.identityHashCode(it)}} alreadyClosed=true")
                                    retired.close()
                                }
                                return
                            }
                            if(intent.action == "co.sarae.camera.TEST_RENDER_CONFIG") {
                                provider?.configureRender(renderConfiguration(ctx))
                                event("configuration_reload","sameSurface=${original?.hashCode()}")
                                return
                            }
                            val next = intent.getStringExtra("source") ?: return
                            if (next !in listOf("A", "B")) return
                            source = next
                            val mode=intent.getStringExtra("mode") ?: "now"
                            if (mode == "after") provider?.switchAfterCurrent(uri(ctx,next)) else provider?.switchNow(uri(ctx,next))
                            event("switch", "source=$next mode=$mode surface=${original?.hashCode()}")
                        }
                    }, IntentFilter("co.sarae.camera.TEST_SWITCH").apply { addAction("co.sarae.camera.TEST_RENDER_CONFIG");addAction("co.sarae.camera.TEST_RETIRED_CLOSE") }, Context.RECEIVER_EXPORTED)
                    event("attached", "package=${ctx.packageName} revision=phase14 submissionRemap=$submissionRemap")
                }
            })
        val device = XposedHelpers.findClass("android.hardware.camera2.impl.CameraDeviceImpl", null)
        XposedBridge.hookAllMethods(device, "createCaptureSession", object : XC_MethodHook() {
            override fun beforeHookedMethod(param: MethodHookParam) {
                val config = param.args.firstOrNull() as? SessionConfiguration
                val output: Surface
                if (config != null) {
                    val outputs = config.outputConfigurations
                    event("session_observed", "overload=${param.method} sessionType=${config.sessionType} input=${config.inputConfiguration} outputs=${outputs.size} surfaces=${outputs.map { it.surfaces.toString() }}")
                    val candidate = outputs.singleOrNull()
                    val candidateSurface = candidate?.surface
                    // Android 14 OutputConfiguration.equals checks physical ID, sharing,
                    // stream use case, dynamic range, timestamp base and other metadata.
                    // Accept only the default output we can faithfully redirect.
                    if (config.sessionType != SessionConfiguration.SESSION_REGULAR ||
                        config.inputConfiguration != null || candidateSurface == null ||
                        candidate.surfaces.size != 1 || candidate != OutputConfiguration(candidateSurface)) {
                        event("session_unsupported", "method=SessionConfiguration defaultSingleOutputRequired=true")
                        return
                    }
                    output = candidateSurface
                } else {
                    val surfaces = param.args.firstOrNull() as? List<*> ?: return
                    event("session_observed", "overload=${param.method} outputs=${surfaces.size} surfaces=$surfaces")
                    if (surfaces.size != 1 || surfaces[0] !is Surface) {
                        event("session_unsupported", "method=List singleSurfaceRequired=true")
                        return
                    }
                    output = surfaces[0] as Surface
                }
                val ctx = requireNotNull(context)
                val id=(param.thisObject as CameraDevice).id
                val characteristics=(ctx.getSystemService(Context.CAMERA_SERVICE) as CameraManager).getCameraCharacteristics(id)
                val displayRotation=(ctx.getSystemService(Context.WINDOW_SERVICE) as android.view.WindowManager).defaultDisplay.rotation
                cameraConfiguration=RenderConfiguration(autoCameraOrientation=scopePackage == "com.ss.android.ugc.trill",
                    sensorOrientation=characteristics.get(CameraCharacteristics.SENSOR_ORIENTATION),
                    frontFacing=characteristics.get(CameraCharacteristics.LENS_FACING)==CameraCharacteristics.LENS_FACING_FRONT,
                    displayRotationDegrees=when(displayRotation){Surface.ROTATION_90->90; Surface.ROTATION_180->180; Surface.ROTATION_270->270; else->0},
                    // Phase 8 pixel comparison confirmed reflection after rotation in this lab target.
                    consumerMirrorsFrontPreview=scopePackage == "com.ss.android.ugc.trill")
                event("camera_geometry","id=$id sensorOrientation=${characteristics.get(CameraCharacteristics.SENSOR_ORIENTATION)} lensFacing=${characteristics.get(CameraCharacteristics.LENS_FACING)} displayRotation=$displayRotation")
                synchronized(lifecycleLock) {
                    check(dummy == null) { "Unexpected session recreation" }
                    redirectedDevice = param.thisObject as CameraDevice
                    sessionGeneration++;captureStarts=0;captureResults=0
                    partialResultCount=characteristics.get(CameraCharacteristics.REQUEST_PARTIAL_RESULT_COUNT) ?: 1
                    original = output
                    dummy = ImageReader.newInstance(640, 480, ImageFormat.YUV_420_888, 2).also {
                        it.setOnImageAvailableListener({ reader -> reader.acquireLatestImage()?.close() }, handler)
                    }
                    if (config == null) {
                        param.args[0] = listOf(dummy!!.surface)
                    } else {
                        val replacement = SessionConfiguration(config.sessionType,
                            listOf(OutputConfiguration(dummy!!.surface)), config.executor, config.stateCallback)
                        config.sessionParameters?.let { replacement.sessionParameters = it }
                        param.args[0] = replacement
                        event("session_config_redirect", "executorSame=${replacement.executor === config.executor} callbackSame=${replacement.stateCallback === config.stateCallback} parametersSame=${replacement.sessionParameters === config.sessionParameters}")
                    }
                    provider = factory(ctx).also { engine ->
                        engine.configureRender(renderConfiguration(ctx))
                        engine.start(original!!, uri(ctx, source)) { frame ->
                            event(frame.kind, "source=${frame.source} frame=${frame.frameIndex} ptsUs=${frame.presentationTimeUs} fps=${frame.fps} ${frame.detail}",frame)
                        }
                    }
                    event("session_redirect", "method=${if(config == null) "List" else "SessionConfiguration"} original=${original!!.hashCode()} dummy=${dummy!!.surface.hashCode()} device=${System.identityHashCode(param.thisObject)} generation=$sessionGeneration")
                }
            }
        })
        XposedHelpers.findAndHookMethod(CaptureRequest.Builder::class.java, "addTarget", Surface::class.java,
            object : XC_MethodHook() {
                override fun beforeHookedMethod(param: MethodHookParam) {
                    if (param.args[0] == original) {
                        param.args[0] = dummy!!.surface
                        event("target_redirect", "original=${original!!.hashCode()}")
                    }
                }
            })
        // A request may already exist before createCaptureSession is intercepted. Resolve its
        // original Surface to the dummy stream ID at Android's submission conversion boundary.
        // Use a private lookup copy: never mutate the caller's immutable request or real outputs.
        // Phase 14 extends the tested default-single-output conversion to the allowlisted lab.
        if (submissionRemap && scopePackage in listOf("co.sarae.camera.consumer","com.ss.android.ugc.trill")) {
            XposedHelpers.findAndHookMethod(CaptureRequest::class.java, "convertSurfaceToStreamId", SparseArray::class.java,
                object : XC_MethodHook() {
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        val request = param.thisObject as CaptureRequest
                        val output = original ?: return
                        val drain = dummy?.surface ?: return
                        @Suppress("UNCHECKED_CAST")
                        val targets = XposedHelpers.callMethod(request, "getTargets") as Collection<Surface>
                        if (targets.size != 1 || targets.single() != output) return
                        @Suppress("UNCHECKED_CAST")
                        val configured = param.args[0] as SparseArray<OutputConfiguration>
                        if (configured.size() != 1 || configured.valueAt(0).surfaces.singleOrNull() != drain) return
                        val disabled = scopePackage == "co.sarae.camera.consumer" && File(context!!.getExternalFilesDir(null), "phase13-disable-submit-remap").exists()
                        param.setObjectExtra("saraeMappingObserved", true)
                        event("request_conversion", "prebuiltOriginal=true enabled=${!disabled} request=${System.identityHashCode(request)} tag=${request.tag} stream=${configured.keyAt(0)}")
                        if (!disabled) {
                            val lookup = configured.clone()
                            lookup.setValueAt(0, OutputConfiguration(output))
                            param.args[0] = lookup
                            event("request_submit_remap", "stream=${lookup.keyAt(0)} original=${output.hashCode()} dummy=${drain.hashCode()} requestUnchanged=true configuredOutputsUnchanged=true")
                        }
                    }
                    override fun afterHookedMethod(param: MethodHookParam) {
                        if (param.getObjectExtra("saraeMappingObserved") == true) {
                            event("request_conversion_result", "error=${param.throwable?.toString() ?: "none"}")
                        }
                    }
                })
        }
        XposedBridge.hookAllMethods(device,"submitCaptureRequest",object:XC_MethodHook(){
            override fun beforeHookedMethod(param:MethodHookParam) {
                if(param.thisObject !== redirectedDevice)return
                val requests=param.args[0] as? List<*> ?: return
                param.setObjectExtra("saraeSubmitObserved",true)
                val targets=requests.map {r->(XposedHelpers.callMethod(r,"getTargets") as Collection<*>).map {System.identityHashCode(it)}}
                event("capture_submit","device=${System.identityHashCode(param.thisObject)} generation=$sessionGeneration count=${requests.size} repeating=${param.args.lastOrNull()} callbackPresent=${param.args.getOrNull(1)!=null} targets=$targets")
            }
            override fun afterHookedMethod(param:MethodHookParam) {
                if(param.getObjectExtra("saraeSubmitObserved")==true)event("capture_submit_result","generation=$sessionGeneration requestId=${param.result} error=${param.throwable?.toString() ?: "none"}")
            }
        })
        // Observe real framework/Binder capture delivery; never synthesize metadata or callbacks.
        val callbacks=XposedHelpers.findClass("android.hardware.camera2.impl.CameraDeviceImpl\$CameraDeviceCallbacks",null)
        for(method in listOf("onCaptureStarted","onResultReceived","onDeviceError","onRepeatingRequestError")) {
            XposedBridge.hookAllMethods(callbacks,method,object:XC_MethodHook(){
                override fun beforeHookedMethod(param:MethodHookParam) {
                    try {
                        val owner=XposedHelpers.getObjectField(param.thisObject,"this\$0")
                        if(owner !== redirectedDevice)return
                        if(method=="onCaptureStarted") {
                            captureStarts++
                            if(captureStarts==1L || captureStarts%30L==0L)event("capture_started_observed","device=${System.identityHashCode(owner)} generation=$sessionGeneration count=$captureStarts frame=${XposedHelpers.callMethod(param.args[0],"getFrameNumber")} sensorNs=${param.args[1]}")
                        } else if(method=="onResultReceived") {
                            val extras=param.args[1]
                            val partial=XposedHelpers.callMethod(extras,"getPartialResultCount") as Int
                            if(partial>=partialResultCount) {
                                captureResults++
                                if(captureResults==1L || captureResults%30L==0L) {
                                    val timestamp=XposedHelpers.callMethod(param.args[0],"get",CaptureResult.SENSOR_TIMESTAMP)
                                    event("capture_result_observed","device=${System.identityHashCode(owner)} generation=$sessionGeneration count=$captureResults frame=${XposedHelpers.callMethod(extras,"getFrameNumber")} partial=$partial sensorNs=$timestamp frameworkFinal=true")
                                }
                            }
                        } else event("capture_device_error","method=$method device=${System.identityHashCode(owner)} generation=$sessionGeneration code=${param.args.firstOrNull()}")
                    } catch(t:Throwable) {
                        if(observerErrors++==0L)event("capture_observer_error",t.toString())
                    }
                }
            })
        }
        XposedBridge.hookAllMethods(device, "close", object : XC_MethodHook() {
            override fun afterHookedMethod(param: MethodHookParam) {
                synchronized(lifecycleLock) {
                    val closing = param.thisObject as CameraDevice
                    event("camera_close_observed", "closing=${System.identityHashCode(closing)} owner=${redirectedDevice?.let{System.identityHashCode(it)}} providerActive=${provider!=null} stack=${Throwable().stackTrace.take(10).joinToString(" <- ")}")
                    // A retired CameraDevice can be closed again (including by its finalizer).
                    // Its id may equal the new device's id: compare object ownership, not camera id.
                    val guardDisabled = scopePackage == "co.sarae.camera.consumer" &&
                        File(context!!.getExternalFilesDir(null), "phase13-disable-owner-guard").exists()
                    if (!guardDisabled && closing !== redirectedDevice) {
                        event("camera_close_ignored", "closing=${System.identityHashCode(closing)} owner=${redirectedDevice?.let {System.identityHashCode(it)}}")
                        return
                    }
                    if(!param.hasThrowable())retiredDevice=WeakReference(closing)
                    redirectedDevice=null
                    provider?.close(); provider = null
                    // Close drain reader after the camera stops producing to it.
                    val old=dummy; dummy=null
                    handler.postDelayed({ old?.close() }, 500)
                    event("camera_close", "source=$source")
                }
            }
        })
        event("installed", "scope=$scopePackage defaultSingleSurface=true sessionConfiguration=true")
    }

    private fun uri(context: Context, source: String): Uri =
        Uri.fromFile(File(context.getExternalFilesDir(null), "$source.mp4"))
}
