package co.sarae.camera.observationmodule

import android.app.Application
import android.content.Context
import android.content.pm.ApplicationInfo
import android.os.Process
import android.os.SystemClock
import co.sarae.camera.observation.NativeObservation
import co.sarae.camera.observation.NativeHookValidation
import co.sarae.camera.observation.NativeBinding
import de.robv.android.xposed.*
import de.robv.android.xposed.callbacks.XC_LoadPackage
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.zip.ZipFile

/** Only the owned Phase 15 harness is allowlisted. Never installed into TikTok. */
class ObservationEntry:IXposedHookLoadPackage,IXposedHookZygoteInit {
    private var modulePath=""
    private var api=0L
    override fun initZygote(startupParam:IXposedHookZygoteInit.StartupParam) {modulePath=startupParam.modulePath}
    override fun handleLoadPackage(param:XC_LoadPackage.LoadPackageParam) {
        val target="co.sarae.camera.nativeaudiotest"
        if(param.packageName!=target||param.processName!=target||param.appInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE==0)return
        XposedHelpers.findAndHookMethod(Application::class.java,"attach",Context::class.java,object:XC_MethodHook(){
            override fun afterHookedMethod(p:MethodHookParam) {
                val context=p.args[0] as Context
                XposedHelpers.findAndHookMethod("co.sarae.camera.nativeaudiotest.RecorderActivity",param.classLoader,"observationApi",object:XC_MethodHook(){
                    override fun afterHookedMethod(call:MethodHookParam) {
                        try {call.result=load(context)}
                        catch(t:Throwable){log(context,"native_load_error",mapOf("error" to t.stackTraceToString()));throw t}
                    }
                })
                XposedHelpers.findAndHookMethod("co.sarae.camera.nativeaudiotest.RecorderActivity",param.classLoader,"hookControl",Int::class.javaPrimitiveType,object:XC_MethodHook(){
                    override fun afterHookedMethod(call:MethodHookParam) {call.result=NativeHookValidation.control(call.args[0] as Int)}
                })
                XposedHelpers.findAndHookMethod("co.sarae.camera.nativeaudiotest.RecorderActivity",param.classLoader,"hookEvents",object:XC_MethodHook(){
                    override fun afterHookedMethod(call:MethodHookParam) {call.result=NativeHookValidation.poll()}
                })
                XposedHelpers.findAndHookMethod("co.sarae.camera.nativeaudiotest.RecorderActivity",param.classLoader,"bindingCheck",String::class.java,object:XC_MethodHook(){
                    override fun afterHookedMethod(call:MethodHookParam) {call.result=NativeBinding.verify(call.args[0] as String)}
                })
                XposedHelpers.findAndHookMethod("co.sarae.camera.nativeaudiotest.RecorderActivity",param.classLoader,"hookEvents20",object:XC_MethodHook(){
                    override fun afterHookedMethod(call:MethodHookParam) {call.result=NativeHookValidation.poll20()}
                })
                XposedHelpers.findAndHookMethod("co.sarae.camera.nativeaudiotest.RecorderActivity",param.classLoader,"hookCode20",object:XC_MethodHook(){
                    override fun afterHookedMethod(call:MethodHookParam) {call.result=NativeHookValidation.code20()}
                })
                XposedHelpers.findAndHookMethod("co.sarae.camera.nativeaudiotest.RecorderActivity",param.classLoader,"bindingError",object:XC_MethodHook(){
                    override fun afterHookedMethod(call:MethodHookParam) {call.result=NativeHookValidation.pinError20()}
                })
                XposedHelpers.findAndHookMethod("co.sarae.camera.nativeaudiotest.RecorderActivity",param.classLoader,"ownerPin",Long::class.javaPrimitiveType,object:XC_MethodHook(){
                    override fun afterHookedMethod(call:MethodHookParam) {NativeHookValidation.ownerPin20(call.args[0] as Long)}
                })
                log(context,"module_attached",mapOf("modulePath" to modulePath,"package" to target,"classLoader" to (javaClass.classLoader?.toString() ?: "bootstrap")))
            }
        })
    }
    @Synchronized private fun load(context:Context):Long {
        if(api!=0L)return api
        require(modulePath.isNotEmpty()) {"LSPatch modulePath unavailable"}
        val content=ZipFile(modulePath).use {zip->
            val entry=requireNotNull(zip.getEntry("lib/arm64-v8a/libsarae_native_observer.so")) {"ARM64 observer absent from module"}
            require(entry.size in 1..1_000_000)
            zip.getInputStream(entry).use {it.readBytes()}
        }
        val hash=MessageDigest.getInstance("SHA-256").digest(content).joinToString("") {"%02x".format(it.toInt() and 255)}
        val dir=File(context.codeCacheDir,"sarae-phase17/$hash").apply {mkdirs()}
        val library=File(dir,"libsarae_native_observer.so")
        if(!library.exists()) {
            val temporary=File(dir,"observer.tmp");temporary.writeBytes(content)
            check(temporary.setReadOnly());check(temporary.renameTo(library))
        }
        check(MessageDigest.getInstance("SHA-256").digest(library.readBytes()).contentEquals(MessageDigest.getInstance("SHA-256").digest(content)))
        System.load(library.absolutePath)
        val validation=ZipFile(modulePath).use {it.getInputStream(it.getEntry("lib/arm64-v8a/libsarae_hook_validation.so")).use {input->input.readBytes()}}
        val validationHash=MessageDigest.getInstance("SHA-256").digest(validation).joinToString("") {"%02x".format(it.toInt() and 255)}
        val validationDir=File(context.codeCacheDir,"sarae-phase19/$validationHash").apply {check(mkdirs()||isDirectory)}
        val validationLibrary=File(validationDir,"libsarae_hook_validation.so")
        if(!validationLibrary.exists()) {
            val temp=File(validationDir,"validation.tmp");temp.writeBytes(validation);check(temp.setReadOnly());check(temp.renameTo(validationLibrary))
        }
        check(validationLibrary.readBytes().contentEquals(validation))
        System.load(validationLibrary.absolutePath)
        log(context,"native_hook_component_loaded",mapOf("sha256" to validationHash,"library" to validationLibrary.absolutePath,
            "stats" to NativeHookValidation.control(0).toList()))
        api=NativeObservation.apiAddress();check(api!=0L)
        log(context,"native_loaded",mapOf("sha256" to hash,"library" to library.absolutePath,"api" to api,
            "javaThread" to Thread.currentThread().name,"nativeLoading" to "module APK -> own private code cache -> System.load"))
        return api
    }
    private fun log(context:Context,event:String,fields:Map<String,Any?>) {
        val row=JSONObject().put("event",event).put("emissionNs",SystemClock.elapsedRealtimeNanos()).put("pid",Process.myPid()).put("tid",Process.myTid())
        fields.forEach {(key,value)->row.put(key,JSONObject.wrap(value))}
        File(context.getExternalFilesDir(null),"phase17-module.jsonl").appendText(row.toString()+"\n")
    }
}
