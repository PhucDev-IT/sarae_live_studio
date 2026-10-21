package co.sarae.agent

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class AgentViewModel(application: Application) : AndroidViewModel(application) {
    private val store = AgentConfigStore(application)
    val config: StateFlow<AgentConfig?> = store.config.stateIn(
        viewModelScope, SharingStarted.WhileSubscribed(5_000), null
    )

    private val _status = MutableStateFlow("Đang đọc đăng ký…")
    val status: StateFlow<String> = _status.asStateFlow()
    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    init {
        viewModelScope.launch {
            config.collect { saved ->
                if (_status.value == "Đang đọc đăng ký…") {
                    _status.value = if (saved == null) "Chưa đăng ký" else "Đã đăng ký · ${saved.agentId}"
                }
            }
        }
    }

    fun setStatus(value: String) { _status.value = value }

    fun activate(apiUrl: String, activationKey: String, hostname: String) {
        viewModelScope.launch {
            _busy.value = true
            _status.value = "Đang kích hoạt…"
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    AgentApi.activate(apiUrl, activationKey, hostname,
                        AgentApi.clientFor(getApplication()))
                }
            }
            result.onSuccess { activated ->
                runCatching { store.save(activated) }
                    .onSuccess { _status.value = "Đã kích hoạt · ${activated.agentId}" }
                    .onFailure { error -> _status.value = "Không lưu được đăng ký: ${error.message}" }
            }.onFailure { _status.value = "Kích hoạt lỗi: ${it.message}" }
            _busy.value = false
        }
    }

    fun clearRegistration() {
        viewModelScope.launch {
            _busy.value = true
            runCatching { store.clear() }
                .onSuccess { _status.value = "Đã xóa đăng ký trên máy" }
                .onFailure { _status.value = "Không xóa được đăng ký: ${it.message}" }
            _busy.value = false
        }
    }

    fun syncVideos(onComplete: (Result<List<RemoteVideo>>) -> Unit) {
        val current = config.value ?: return
        viewModelScope.launch {
            _busy.value = true
            _status.value = "Đang đồng bộ video…"
            val result = withContext(Dispatchers.IO) {
                runCatching { AgentApi(current, AgentApi.clientFor(getApplication())).syncVideos(getApplication()) }
            }
            result.onSuccess { _status.value = "Đã đồng bộ ${it.size} video" }
                .onFailure { _status.value = "Đồng bộ lỗi: ${it.message}" }
            _busy.value = false
            onComplete(result)
        }
    }
}
