package co.sarae.agent

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

class AndroidAgentService : Service() {
    private val executor = Executors.newScheduledThreadPool(3)
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    @Volatile private var socket: WebSocket? = null
    private var api: AgentApi? = null
    private var reconnectDelay = 1L
    private var workersStarted = false
    private val runtime by lazy { AgentRuntime.get(this) }

    override fun onCreate() { super.onCreate(); channel(); startForeground(7, notification("Đang kết nối")) }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> { socket?.close(1000, "stop"); executor.shutdownNow(); stopForeground(STOP_FOREGROUND_REMOVE); stopSelf(); return START_NOT_STICKY }
        }
        serviceScope.launch {
            val config = AgentConfigStore(this@AndroidAgentService).load()
            if (config == null) { stopSelf(); return@launch }
            api = AgentApi(config, AgentApi.clientFor(this@AndroidAgentService))
            connect()
            if (!workersStarted) {
                workersStarted = true
                executor.scheduleWithFixedDelay({ runtime.tick() }, 0, 500, TimeUnit.MILLISECONDS)
                executor.scheduleWithFixedDelay({ runCatching { api?.heartbeat(runtime.current().json()) }
                    .onFailure { Log.w(TAG, "Heartbeat failed", it); update("Heartbeat lỗi: ${it.message?.take(60)}") } }, 0, 5, TimeUnit.SECONDS)
                executor.scheduleWithFixedDelay({ runCatching { api?.syncVideos(this@AndroidAgentService).orEmpty() }
                    .onSuccess { Log.i(TAG, "Sync complete: ${it.size} video") }
                    .onFailure { Log.w(TAG, "Video sync failed", it); api?.reportError("Video sync failed: ${it.message}") } }, 0, 60, TimeUnit.SECONDS)
            }
        }
        return START_STICKY
    }

    @Synchronized private fun connect() {
        val client = api ?: return
        if (socket != null) return
        socket = client.connect(object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                if (socket !== webSocket) return
                reconnectDelay = 1; Log.i(TAG, "Command WebSocket connected"); update("Đã kết nối · ${runtime.current().state}")
            }
            override fun onMessage(webSocket: WebSocket, text: String) {
                val message = runCatching { JSONObject(text) }.getOrNull() ?: return
                when (message.optString("type")) {
                    "ping" -> webSocket.send(JSONObject().put("type", "pong").toString())
                    "command" -> executor.execute { execute(webSocket, message.optJSONObject("command") ?: return@execute) }
                }
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (socket !== webSocket) return
                socket = null
                Log.w(TAG, "Command WebSocket failed", t)
                update("Mất kết nối: ${t.message?.take(80) ?: response?.code}")
                val delay = reconnectDelay
                reconnectDelay = (reconnectDelay * 2).coerceAtMost(30)
                executor.schedule({ connect() }, delay, TimeUnit.SECONDS)
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (socket === webSocket) socket = null
                Log.i(TAG, "Command WebSocket closed: $code")
                update("Ngắt kết nối")
            }
        })
    }

    private fun execute(ws: WebSocket, command: JSONObject) {
        val id = command.optString("command_id"); val token = command.optString("claim_token")
        val payload = command.optJSONObject("payload") ?: JSONObject()
        val result = runCatching {
            when (command.optString("kind")) {
                "android_play" -> runtime.play(payload.getString("media_id"))
                "android_switch" -> runtime.switch(payload.getString("media_id"), payload.optString("mode") == "after_current")
                "android_pause" -> runtime.pause()
                "android_resume" -> runtime.resume()
                "android_next" -> runtime.next()
                "android_previous" -> runtime.previous()
                "android_stop" -> runtime.stop()
                "android_queue_set" -> runtime.setQueue((0 until payload.getJSONArray("media_ids").length()).map { payload.getJSONArray("media_ids").getString(it) }, payload.optBoolean("loop", true), payload.optBoolean("auto_rotate", true))
                "android_rotation_set" -> runtime.setRotation(payload.getBoolean("enabled"))
                "android_mock_live_start" -> runtime.mockLive(true)
                "android_mock_live_stop" -> runtime.mockLive(false)
                else -> error("Unsupported Android command: ${command.optString("kind")}")
            }
            runtime.current().json().toString()
        }
        Log.i(TAG, "Command ${command.optString("kind")} ${if (result.isSuccess) "succeeded" else "failed"}")
        result.exceptionOrNull()?.let { api?.reportError("${command.optString("kind")}: ${it.message}") }
        val ack = JSONObject().put("type", "ack").put("command_id", id).put("claim_token", token)
            .put("success", result.isSuccess).put("result", result.getOrElse { it.message ?: it.javaClass.simpleName })
        ws.send(ack.toString())
        update("${command.optString("kind")}: ${if (result.isSuccess) "OK" else "Lỗi"}")
    }

    private fun channel() {
        if (Build.VERSION.SDK_INT >= 26) getSystemService(NotificationManager::class.java)
            .createNotificationChannel(NotificationChannel(CHANNEL, "Sarae Android Agent", NotificationManager.IMPORTANCE_LOW))
    }
    private fun notification(text: String): Notification = NotificationCompat.Builder(this, CHANNEL)
        .setSmallIcon(android.R.drawable.ic_media_play).setContentTitle("Sarae Android Agent")
        .setContentText(text).setOngoing(true).build()
    private fun update(text: String) { runCatching { getSystemService(NotificationManager::class.java).notify(7, notification(text)) } }
    override fun onBind(intent: Intent?): IBinder? = null
    override fun onDestroy() { socket?.close(1000, "service destroyed"); serviceScope.cancel(); executor.shutdownNow(); super.onDestroy() }
    companion object { const val ACTION_STOP = "co.sarae.agent.STOP"; private const val CHANNEL = "sarae-agent"; private const val TAG = "SaraeAgent" }
}
