package co.sarae.agent

import android.content.Context
import android.content.pm.ApplicationInfo
import android.os.Build
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import java.security.KeyStore
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

data class RemoteVideo(val mediaId: String, val filename: String, val sizeBytes: Long, val localFile: File)

class AgentApi(val config: AgentConfig, private val client: OkHttpClient = defaultClient) {
    private val base = config.apiUrl.trimEnd('/')

    init { require(base.startsWith("https://", ignoreCase = true)) { "Backend URL must use HTTPS" } }

    fun heartbeat(playback: JSONObject) {
        val body = JSONObject().put("agent_id", config.agentId).put("hostname", config.hostname)
            .put("playback", playback).put("local_playback", playback)
        request("POST", "/api/agents/heartbeat", body)
    }

    fun syncVideos(context: Context): List<RemoteVideo> {
        val catalog = request("GET", "/api/android-agents/${config.agentId}/sync")
            .optJSONArray("videos") ?: JSONArray()
        val directory = File(context.filesDir, "agent-videos").apply { mkdirs() }
        val liveIds = mutableSetOf<String>()
        val result = mutableListOf<RemoteVideo>()
        for (index in 0 until catalog.length()) {
            val item = catalog.getJSONObject(index)
            val mediaId = item.getString("media_id")
            val filename = item.getString("filename")
            val size = item.getLong("size_bytes")
            require(mediaId.matches(Regex("^[a-f0-9]{64}$"))) { "Invalid media id" }
            val target = File(directory, "$mediaId.mp4")
            if (!target.isFile || target.length() != size || sha256(target) != mediaId) {
                val temporary = File(directory, "$mediaId.download")
                try {
                    val url = "/api/android-agents/${config.agentId}/videos/$mediaId/content"
                    val request = Request.Builder().url(base + url).header("X-Sarae-Agent-Key", config.credential).get().build()
                    client.newCall(request).execute().use { response ->
                        check(response.isSuccessful) { "Download $filename returned HTTP ${response.code}" }
                        val body = checkNotNull(response.body) { "Empty download for $filename" }
                        FileOutputStream(temporary).use { output -> body.byteStream().copyTo(output) }
                    }
                    check(temporary.length() == size && sha256(temporary) == mediaId) { "Checksum/size mismatch: $filename" }
                    check(temporary.renameTo(target)) { "Could not finalize $filename" }
                } finally { temporary.delete() }
            }
            liveIds += mediaId
            result += RemoteVideo(mediaId, filename, size, target)
        }
        directory.listFiles()?.filter { it.extension == "mp4" && it.nameWithoutExtension !in liveIds }?.forEach(File::delete)
        return result
    }

    fun connect(listener: WebSocketListener): WebSocket {
        val wsBase = base.replaceFirst("https://", "wss://", ignoreCase = true)
        val request = Request.Builder().url("$wsBase/api/agents/ws/${config.agentId}")
            .header("X-Sarae-Agent-Key", config.credential).build()
        return client.newWebSocket(request, listener)
    }

    fun reportError(detail: String) {
        runCatching {
            val event = JSONObject().put("seq", System.currentTimeMillis())
                .put("type", "ERROR").put("detail", JSONObject().put("message", detail.take(500)))
            request("POST", "/api/agents/events", JSONObject().put("agent_id", config.agentId)
                .put("events", JSONArray().put(event)))
        }
    }

    private fun request(method: String, path: String, json: JSONObject? = null): JSONObject {
        val builder = Request.Builder().url(base + path).header("X-Sarae-Agent-Key", config.credential)
        if (method == "GET") builder.get() else builder.method(method,
            (json ?: JSONObject()).toString().toRequestBody("application/json".toMediaType()))
        client.newCall(builder.build()).execute().use { response ->
            val text = response.body?.string().orEmpty()
            check(response.isSuccessful) { "HTTP ${response.code}: ${text.take(300)}" }
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        }
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().buffered().use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val n = input.read(buffer)
                if (n < 0) break
                digest.update(buffer, 0, n)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    companion object {
        val defaultClient: OkHttpClient by lazy {
            OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS)
                .pingInterval(20, TimeUnit.SECONDS).build()
        }

        fun clientFor(context: Context): OkHttpClient {
            val builder = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS)
                .pingInterval(20, TimeUnit.SECONDS)
            if (context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE == 0) return builder.build()
            val caStream = runCatching { context.resources.openRawResource(co.sarae.agent.R.raw.sarae_test_ca) }.getOrNull()
                ?: return builder.build()
            caStream.use { input ->
                val testCa = CertificateFactory.getInstance("X.509").generateCertificate(input) as X509Certificate
                val roots = KeyStore.getInstance(KeyStore.getDefaultType()).apply { load(null) }
                val system = KeyStore.getInstance("AndroidCAStore").apply { load(null) }
                val aliases = system.aliases()
                while (aliases.hasMoreElements()) {
                    val alias = aliases.nextElement()
                    system.getCertificate(alias)?.let { roots.setCertificateEntry("system-$alias", it) }
                }
                roots.setCertificateEntry("sarae-debug-test-ca", testCa)
                val factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(roots) }
                val trust = factory.trustManagers.filterIsInstance<X509TrustManager>().single()
                val ssl = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), null) }
                builder.sslSocketFactory(ssl.socketFactory, trust)
            }
            return builder.build()
        }

        fun activate(apiUrl: String, activationKey: String, hostname: String, client: OkHttpClient = defaultClient): AgentConfig {
            require(apiUrl.trim().startsWith("https://", ignoreCase = true)) { "Backend URL must use HTTPS" }
            val body = JSONObject().put("activation_key", activationKey.trim()).put("hostname", hostname.trim())
                .put("platform", "ANDROID")
            val request = Request.Builder().url(apiUrl.trimEnd('/') + "/api/identity/activate")
                .post(body.toString().toRequestBody("application/json".toMediaType())).build()
            client.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                check(response.isSuccessful) { "HTTP ${response.code}: ${text.take(300)}" }
                val json = JSONObject(text)
                check(json.optString("platform") == "ANDROID") { "Backend did not register Android platform" }
                return AgentConfig(apiUrl.trimEnd('/'), json.getString("agent_id"),
                    json.getString("agent_credential"), hostname.trim().ifBlank { Build.MODEL })
            }
        }
    }
}
