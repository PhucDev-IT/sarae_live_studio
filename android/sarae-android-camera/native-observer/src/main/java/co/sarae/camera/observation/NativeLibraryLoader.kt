package co.sarae.camera.observation

import android.content.Context
import android.os.Process
import java.io.File
import java.security.MessageDigest
import java.util.zip.ZipFile

/** Phase 17 loading procedure shared by owned probe and target startup.
 * No recorder creation, symbol discovery, interception, or PCM access.
 */
object NativeLibraryLoader {
    data class Loaded(val api:Long,val path:String,val sha256:String)
    private var loaded:Loaded?=null
    private fun hash(data:ByteArray)=MessageDigest.getInstance("SHA-256").digest(data)
    @Synchronized fun load(context:Context,modulePath:String,log:(String,Map<String,Any?>)->Unit):Loaded {
        loaded?.let {return it}
        require(modulePath.isNotEmpty()) {"LSPatch modulePath unavailable"}
        val content=ZipFile(modulePath).use {zip->
            val entry=requireNotNull(zip.getEntry("lib/arm64-v8a/libsarae_native_observer.so")) {"ARM64 observer absent"}
            require(entry.size in 1..1_000_000)
            zip.getInputStream(entry).use {it.readBytes()}
        }
        val digest=hash(content)
        val sha=digest.joinToString("") {"%02x".format(it.toInt() and 255)}
        val directory=File(context.codeCacheDir,"sarae-native-observer/$sha").apply {check(mkdirs()||isDirectory)}
        val library=File(directory,"libsarae_native_observer.so")
        if(!library.exists()) {
            val temporary=File(directory,"observer.tmp");temporary.writeBytes(content)
            check(temporary.setReadOnly());check(temporary.renameTo(library))
        }
        check(hash(library.readBytes()).contentEquals(digest)) {"Observer code-cache hash mismatch"}
        log("native_load_begin",mapOf("modulePath" to modulePath,"library" to library.absolutePath,"sha256" to sha,
            "moduleClassLoader" to (NativeObservation::class.java.classLoader?.toString() ?: "bootstrap"),
            "hostClassLoader" to context.classLoader.toString()))
        System.load(library.absolutePath)
        val api=NativeObservation.apiAddress();check(api!=0L)
        val native=NativeObservation.diagnostics()
        check(native.size==12 && native[0]==0x53415241454f4253L && native[1]==1L)
        check(native[2]==Process.myPid().toLong() && native[3]==Process.myTid().toLong() && native[4]==native[2] && native[5]==native[3])
        check(native[7]==0L && native[8]==0L) {"Unexpected recorder relays during load"}
        val value=Loaded(api,library.absolutePath,sha);loaded=value
        log("native_loaded",mapOf("api" to api,"sha256" to sha,"library" to value.path,"nativeStats" to native.toList(),
            "javaThread" to Thread.currentThread().name,"pcmModified" to false,"targetInterceptionInstalled" to false))
        return value
    }
}
