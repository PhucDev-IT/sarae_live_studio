package co.sarae.agent

import android.content.Context
import android.media.MediaPlayer
import android.view.Surface
import org.json.JSONObject
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList

data class PlaybackSnapshot(
    val state: String = "IDLE", val currentId: String? = null, val positionMs: Int = 0,
    val durationMs: Int = 0, val queue: List<String> = emptyList(), val queueIndex: Int = -1,
    val loop: Boolean = true, val autoRotate: Boolean = true, val mockLive: Boolean = false,
    val error: String? = null, val updatedAtMs: Long = System.currentTimeMillis()
) {
    fun json() = JSONObject().put("state", state).put("running", state == "PLAYING")
        .put("current_media_id", currentId).put("position_ms", positionMs).put("duration_ms", durationMs)
        .put("queue", org.json.JSONArray(queue)).put("queue_index", queueIndex).put("loop", loop)
        .put("auto_rotate", autoRotate).put("mock_live", mockLive).put("error", error)
        .put("updated_at_ms", updatedAtMs)
}

class AgentRuntime private constructor(private val context: Context) {
    private val listeners = CopyOnWriteArrayList<(PlaybackSnapshot) -> Unit>()
    private var player: MediaPlayer? = null
    private var surface: Surface? = null
    @Volatile private var snapshot = PlaybackSnapshot()

    fun current() = snapshot
    fun observe(listener: (PlaybackSnapshot) -> Unit) { listeners += listener; listener(snapshot) }
    fun remove(listener: (PlaybackSnapshot) -> Unit) { listeners -= listener }
    fun attachSurface(value: Surface?) { surface = value; player?.setSurface(value) }
    fun videos(): List<RemoteVideo> = File(context.filesDir, "agent-videos").listFiles()
        ?.filter { it.extension.equals("mp4", true) }?.map { RemoteVideo(it.nameWithoutExtension, it.name, it.length(), it) }
        ?.sortedBy { it.filename.lowercase() }.orEmpty()

    @Synchronized fun play(id: String) {
        val index = snapshot.queue.indexOf(id)
        val queue = if (index >= 0) snapshot.queue else listOf(id)
        start(queue, if (index >= 0) index else 0)
    }

    @Synchronized fun switch(id: String, afterCurrent: Boolean) {
        if (!afterCurrent) { play(id); return }
        val queue = snapshot.queue.filterNot { it == id }.toMutableList().also {
            it.add((snapshot.queueIndex + 1).coerceIn(0, it.size), id)
        }
        publish(snapshot.copy(queue = queue, updatedAtMs = System.currentTimeMillis()))
    }

    @Synchronized fun setQueue(ids: List<String>, loop: Boolean, autoRotate: Boolean) {
        require(ids.isNotEmpty()) { "Queue phải có ít nhất một video" }
        val current = snapshot.currentId
        val index = ids.indexOf(current).takeIf { it >= 0 } ?: 0
        val wasPlaying = snapshot.state == "PLAYING"
        publish(snapshot.copy(queue = ids, queueIndex = index, loop = loop, autoRotate = autoRotate,
            updatedAtMs = System.currentTimeMillis()))
        if (wasPlaying || current == null) start(ids, index)
    }

    @Synchronized fun setRotation(enabled: Boolean) = publish(snapshot.copy(autoRotate = enabled, updatedAtMs = System.currentTimeMillis()))
    @Synchronized fun pause() { player?.pause(); publish(snapshot.copy(state = "PAUSED", positionMs = runCatching { player?.currentPosition ?: 0 }.getOrDefault(0), updatedAtMs = System.currentTimeMillis())) }
    @Synchronized fun resume() { if (player == null) snapshot.currentId?.let(::play) else { player?.start(); publish(snapshot.copy(state = "PLAYING", updatedAtMs = System.currentTimeMillis())) } }
    @Synchronized fun stop() { player?.release(); player = null; publish(snapshot.copy(state = "STOPPED", positionMs = 0, updatedAtMs = System.currentTimeMillis())) }
    @Synchronized fun tick() {
        val active = player ?: return
        if (snapshot.state == "PLAYING") runCatching { active.currentPosition }.getOrNull()?.let { position ->
            if (position - snapshot.positionMs >= 400) publish(snapshot.copy(positionMs = position, updatedAtMs = System.currentTimeMillis()))
        }
    }
    @Synchronized fun next() = advance(1)
    @Synchronized fun previous() = advance(-1)
    @Synchronized fun mockLive(enabled: Boolean) = publish(snapshot.copy(mockLive = enabled, updatedAtMs = System.currentTimeMillis()))

    private fun advance(delta: Int) {
        val q = snapshot.queue
        if (q.isEmpty()) return
        var next = snapshot.queueIndex + delta
        if (next < 0) next = if (snapshot.loop) q.lastIndex else 0
        if (next >= q.size) next = if (snapshot.loop) 0 else q.lastIndex
        start(q, next)
    }

    @Synchronized private fun start(queue: List<String>, index: Int) {
        require(index in queue.indices) { "Queue index không hợp lệ" }
        val file = File(context.filesDir, "agent-videos/${queue[index]}.mp4")
        require(file.isFile) { "Video chưa đồng bộ: ${queue[index]}" }
        player?.release()
        val nextPlayer = MediaPlayer()
        player = nextPlayer
        nextPlayer.setOnPreparedListener { mp ->
            mp.start(); publish(snapshot.copy(state = "PLAYING", currentId = queue[index], queue = queue,
                queueIndex = index, durationMs = mp.duration, positionMs = 0, error = null, updatedAtMs = System.currentTimeMillis()))
        }
        nextPlayer.setOnCompletionListener {
            when {
                snapshot.autoRotate -> advance(1)
                snapshot.loop -> runCatching { it.seekTo(0); it.start(); publish(snapshot.copy(state = "PLAYING", positionMs = 0, updatedAtMs = System.currentTimeMillis())) }
                else -> publish(snapshot.copy(state = "ENDED", updatedAtMs = System.currentTimeMillis()))
            }
        }
        nextPlayer.setOnErrorListener { _, what, extra -> publish(snapshot.copy(state = "ERROR", error = "MediaPlayer $what/$extra", updatedAtMs = System.currentTimeMillis())); true }
        try {
            surface?.let(nextPlayer::setSurface)
            nextPlayer.setDataSource(file.absolutePath)
            nextPlayer.prepareAsync()
            publish(snapshot.copy(state = "PREPARING", currentId = queue[index], queue = queue, queueIndex = index,
                error = null, updatedAtMs = System.currentTimeMillis()))
        } catch (t: Throwable) {
            nextPlayer.release(); player = null
            publish(snapshot.copy(state = "ERROR", error = t.message ?: t.javaClass.simpleName, updatedAtMs = System.currentTimeMillis()))
            throw t
        }
    }

    private fun publish(value: PlaybackSnapshot) { snapshot = value; listeners.forEach { it(value) } }

    companion object {
        @Volatile private var instance: AgentRuntime? = null
        fun get(context: Context) = instance ?: synchronized(this) { instance ?: AgentRuntime(context.applicationContext).also { instance = it } }
    }
}
