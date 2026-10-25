package co.sarae.camera.observation

import android.system.Os
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.zip.ZipFile

/** Worker-only ELF identity validation. Never invoked from a native callback. */
object NativeBinding {
    private const val TARGET_SHA="9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de"
    private const val TARGET_BUILD="800d53bec62ddf0a1774842f5a16e1693ab7f0de"
    private const val TARGET_ENTRY="f40f1ef8f37b01a93310019144841c9488b24139e8040034e00314aacaFFFF97"
    fun verify(json:String):LongArray {
        val p=JSONObject(json);val kind=p.getInt("kind");require(kind in 0..1)
        val expected=p.getString("sha256");val build=p.getString("buildId");val rva=java.lang.Long.decode(p.getString("rva"))
        val hex=p.getString("entryHex");require(hex.length==64)
        val bytes=hex.chunked(2).map {it.toInt(16).toByte()}.toByteArray()
        if(kind==1 && (expected!=TARGET_SHA||build!=TARGET_BUILD||rva!=0x6be020L||!hex.equals(TARGET_ENTRY,true)))return NativeHookValidation.reject20(-48)
        val path=NativeHookValidation.path20(kind)
        if(path.isEmpty())return NativeHookValidation.resolve20(kind,rva,build,bytes)
        val expectedName=if(kind==0)"libsarae_native_audio.so" else "liblyrax.so"
        if(path.substringAfterLast('/')!=expectedName)return NativeHookValidation.reject20(-47)
        val backing=File(path.substringBefore("!/"));val before=Os.stat(backing.absolutePath)
        val data=if(path.contains("!/"))ZipFile(backing).use {z->
            val entry=requireNotNull(z.getEntry(path.substringAfter("!/")));require(entry.size in 1..32_000_000)
            z.getInputStream(entry).use {it.readBytes()}
        }else {require(backing.length() in 1..32_000_000);backing.readBytes()}
        val after=Os.stat(backing.absolutePath)
        if(before.st_dev!=after.st_dev||before.st_ino!=after.st_ino||before.st_size!=after.st_size||before.st_mtime!=after.st_mtime)return NativeHookValidation.reject20(-49)
        val actual=MessageDigest.getInstance("SHA-256").digest(data).joinToString("") {"%02x".format(it.toInt() and 255)}
        if(actual!=expected)return NativeHookValidation.reject20(-31)
        return NativeHookValidation.resolve20(kind,rva,build,bytes)
    }
}
