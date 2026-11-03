from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Sarae Live Studio API"
    app_version: str = "0.1.0"
    gemini_api_key: str = ""
    openai_api_key: str = ""
    sarae_telegram_bot_token: str = ""
    sarae_agent_key: str = ""
    sarae_admin_key: str = ""
    sarae_saas_mode: bool = False
    # HMAC key for signed image URLs; set the same value on every server behind a load balancer.
    sarae_url_signing_secret: str = ""
    # Fernet key for OWNER AI credentials at rest (else backend/data/credential.key is generated).
    sarae_credential_key: str = ""
    sarae_cloud_api_url: str = ""
    sarae_agent_id: str = ""
    sarae_agent_autostart_monitor: bool = False
    # Centralized Media Library storage (system-owned). Provider for NEW uploads; existing media keep theirs.
    sarae_storage_provider: str = "R2"
    sarae_r2_account_id: str = ""
    sarae_r2_bucket: str = ""
    sarae_r2_access_key_id: str = ""
    sarae_r2_secret_access_key: str = ""
    sarae_r2_endpoint: str = ""
    sarae_r2_region: str = "auto"
    # Google OAuth web client of the Sarae Google Cloud project (system account, connected by a SUPER_ADMIN).
    sarae_google_client_id: str = ""
    sarae_google_client_secret: str = ""
    # Backend URL Google redirects to, e.g. https://api.example.com/api/storage-admin/google/callback
    sarae_google_redirect_uri: str = ""
    # Drive folder name Sarae creates as its root (per-owner folders go inside it).
    sarae_google_root_folder_name: str = "Sarae Media Storage"
    # Sarae Web origin the admin callback returns the browser to.
    sarae_web_app_url: str = "http://localhost:5173"
    sarae_media_max_upload_bytes: int = 4 * 1024 ** 3
    # Per-owner storage quota in bytes; 0 = unlimited.
    sarae_media_owner_quota_bytes: int = 0
    # ffmpeg/ffprobe used to cut Media Library videos into clips; empty = found on PATH.
    sarae_ffmpeg_path: str = ""
    sarae_ffprobe_path: str = ""
    # AI Timeline Analyzer: default local Faster-Whisper model, device and precision (owners can override the model).
    sarae_whisper_model: str = "small"
    sarae_whisper_device: str = "auto"
    sarae_whisper_compute_type: str = "int8"
    # G-OmniVoice TTS Backend (voice-server/, docs/TTS_SERVER.md). Empty URL = the omnivoice provider is unavailable.
    # The orchestrator token stays on this server: never sent to the Web or to an Agent.
    sarae_tts_server_url: str = ""
    sarae_tts_server_token: str = ""
    # Base URL a Windows Agent may use to download audio directly (LAN/VPN). Empty = Agents use the Backend relay only.
    sarae_tts_agent_url: str = ""
    sarae_tts_timeout_seconds: float = 10.0
    sarae_tts_lease_ttl_seconds: int = 60
    sarae_tts_relay_max_bytes: int = 32 * 1024 * 1024
    obs_host: str = "127.0.0.1"
    obs_port: int = 4455
    obs_password: str = ""
    obs_input_name: str = "Sarae Video"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
