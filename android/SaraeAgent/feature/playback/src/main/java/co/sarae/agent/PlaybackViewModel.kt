package co.sarae.agent

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

class PlaybackViewModel(application: Application) : AndroidViewModel(application) {
    val runtime: AgentRuntime = AgentRuntime.get(application)
    private val _snapshot = MutableStateFlow(runtime.current())
    val snapshot: StateFlow<PlaybackSnapshot> = _snapshot.asStateFlow()
    private val observer: (PlaybackSnapshot) -> Unit = { _snapshot.value = it }

    init { runtime.observe(observer) }

    override fun onCleared() {
        runtime.remove(observer)
        super.onCleared()
    }
}
