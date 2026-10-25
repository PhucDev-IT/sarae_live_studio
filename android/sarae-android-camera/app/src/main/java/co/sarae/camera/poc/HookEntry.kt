package co.sarae.camera.poc

import android.content.pm.ApplicationInfo
import co.sarae.camera.bridge.Camera2HookPrototype
import co.sarae.camera.engine.StableFrameProvider
import co.sarae.camera.bridge.CameraFrameProvider
import co.sarae.camera.bridge.FrameEvent
import co.sarae.camera.audiobridge.AudioRecordBridge
import android.view.Surface
import android.net.Uri
import de.robv.android.xposed.IXposedHookLoadPackage
import de.robv.android.xposed.IXposedHookZygoteInit
import de.robv.android.xposed.callbacks.XC_LoadPackage

/** Composition only: interception is in bridge; the existing engine supplies all video frames. */
class HookEntry : IXposedHookLoadPackage, IXposedHookZygoteInit {
    private var modulePath=""
    override fun initZygote(startupParam:IXposedHookZygoteInit.StartupParam) {modulePath=startupParam.modulePath}
    override fun handleLoadPackage(param: XC_LoadPackage.LoadPackageParam) {
        val ownConsumer = param.packageName == "co.sarae.camera.consumer"
        val labTikTok = param.packageName == "com.ss.android.ugc.trill" &&
            param.processName == param.packageName &&
            (param.appInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
        if (ownConsumer || labTikTok) {
            if(labTikTok) TargetNativeObservation.install(modulePath)
            val audio=if(labTikTok) AudioRecordBridge().also {it.install()} else null
            Camera2HookPrototype(param.packageName, submissionRemap=ownConsumer || labTikTok) { context ->
                val stable=StableFrameProvider(context)
                if(audio==null) stable else {
                    val token=audio.nextVideoToken()
                    object:CameraFrameProvider by stable {
                        private val requestLock=Any()
                        private var requestTicket=0L
                        private var issuedId=0L
                        private var alive=true
                        private fun request(source:Uri,after:Boolean) = synchronized(requestLock) {
                            val ticket=++requestTicket;val id=issuedId+1
                            audio.prepareBeforeSwitch(token,id,source.toString()) {
                                synchronized(requestLock) {
                                    if(alive && ticket==requestTicket) {
                                        issuedId=id
                                        if(after)stable.switchAfterCurrent(source) else stable.switchNow(source)
                                    }
                                }
                            }
                        }
                        override fun switchSource(source:Uri) {request(source,false)}
                        override fun switchNow(source:Uri) {request(source,false)}
                        override fun switchAfterCurrent(source:Uri) {request(source,true)}
                        override fun stop() {synchronized(requestLock){alive=false;requestTicket++};stable.stop()}
                        override fun close() {synchronized(requestLock){alive=false;requestTicket++};stable.close()}
                        override fun start(output:Surface,source:Uri,events:(FrameEvent)->Unit) {
                            stable.start(output,source) {frame->
                                when(frame.kind) {
                                    "egl_created"->audio.beginVideo(token)
                                    "preload_start"->frame.source?.let {audio.prepare(token,frame.switchId ?: 0,it)}
                                    "output_present"->frame.source?.let {uri->frame.surfaceTextureTimestampNs?.let {pts->audio.videoFrame(token,frame.switchId ?: 0,uri,pts,frame.elapsedRealtimeNs)}}
                                    "egl_released"->audio.endVideo(token)
                                }
                                events(frame)
                            }
                        }
                    }
                }
            }.install()
        }
    }
}
