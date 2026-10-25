package co.sarae.camera.poc

import android.app.Application
import android.content.Context
import android.os.Process
import android.os.SystemClock
import co.sarae.camera.observation.NativeLibraryLoader
import co.sarae.camera.observation.NativeObservation
import de.robv.android.xposed.XC_MethodHook
import de.robv.android.xposed.XposedHelpers
import org.json.JSONObject
import java.io.File

/** Target-scoped startup loading and file-map metadata only. No native hooks. */
internal object TargetNativeObservation {
    fun install(modulePath:String) {
        XposedHelpers.findAndHookMethod(Application::class.java,"attach",Context::class.java,object:XC_MethodHook(){
            override fun afterHookedMethod(param:MethodHookParam) {
                val context=param.args[0] as Context
                Thread({run(context,modulePath)},"sarae-native-observation").start()
            }
        })
    }
    private fun run(context:Context,modulePath:String) {
        val directory=File(context.getExternalFilesDir(null),"phase18").apply {mkdirs()}
        val file=File(directory,"native-${Process.myPid()}.jsonl")
        fun log(event:String,fields:Map<String,Any?>) {
            val row=JSONObject().put("event",event).put("emissionNs",SystemClock.elapsedRealtimeNanos())
                .put("pid",Process.myPid()).put("tid",Process.myTid())
            fields.forEach {(key,value)->row.put(key,JSONObject.wrap(value))}
            file.appendText(row.toString()+"\n")
        }
        try {
            log("target_observer_start",mapOf("revision" to "phase18-load-only","package" to context.packageName,"modulePath" to modulePath))
            NativeLibraryLoader.load(context,modulePath,::log)
            val start=SystemClock.elapsedRealtime()
            for(at in listOf(0L,5_000L,15_000L,30_000L)) {
                val wait=start+at-SystemClock.elapsedRealtime();if(wait>0)Thread.sleep(wait)
                val maps=File("/proc/self/maps").readLines()
                log("native_map_snapshot",mapOf("observerMaps" to maps.filter {it.contains("libsarae_native_observer.so")}.take(16),
                    "targetLibraryMaps" to maps.filter {it.contains("liblyrax.so")}.take(16),"nativeStats" to NativeObservation.diagnostics().toList(),
                    "registrationRva" to "0x6bdab4","callbackCandidateRva" to "0x6be020",
                    "targetInterceptionInstalled" to false,"targetRegistrationObserved" to null,"targetCallbackCount" to null))
            }
            log("observation_worker_complete",mapOf("nativeStats" to NativeObservation.diagnostics().toList(),
                "note" to "no native hook, relay, recorder or decoder created by this component; JNI library remains mapped"))
        } catch(t:Throwable) {log("native_observer_error",mapOf("error" to t.stackTraceToString()))}
    }
}
