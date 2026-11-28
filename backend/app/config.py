"""Server settings, read from the environment and backend/.env (see backend/.env.example)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # sarae-voice-server on this PC. The orchestrator token stays on the server: never sent to a Studio.
    sarae_tts_server_url: str = ""
    sarae_tts_server_token: str = ""
    sarae_tts_timeout_seconds: float = 10.0
    # Sentences the GPU makes at once; others wait in the gateway queue (priority accounts first).
    sarae_gateway_tts_concurrency: int = 1
    # /docs and /openapi.json on the gateway (off in production).
    sarae_gateway_docs: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
