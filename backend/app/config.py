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
    sarae_cloud_api_url: str = ""
    sarae_agent_id: str = ""
    sarae_agent_autostart_monitor: bool = False
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
