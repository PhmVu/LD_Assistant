from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings


_BACKEND_DIR = Path(__file__).resolve().parents[1]
_PROJECT_DIR = _BACKEND_DIR.parent


class Settings(BaseSettings):
    APP_NAME: str = "LD Backend"
    VERSION: str = "0.1.0"
    CORS_ORIGINS: list[str] = ["*"]

    # Storage
    DATA_DIR: Path = _BACKEND_DIR / "storage"
    DOCS_DIR: Path = _BACKEND_DIR / "storage" / "docs"
    INDEX_PATH: Path = _BACKEND_DIR / "storage" / "docs_index.json"
    DOCS_ASSETS_DIR: Path = _BACKEND_DIR / "storage" / "docs" / "_assets"
    DOCS_SOURCE_DIR: Path = _PROJECT_DIR / "docs"

    # Appen/Xiaomi LD auth
    LD_LOGIN_URL: str = "http://global-autolabeling-service.evad.xiaomi.srv/appen/ui/api/account/login"
    APPEN_SHARED_PASSWORD: str = "Biaozhu123"
    LD_LOGIN_TIMEOUT_SECONDS: float = 8.0
    LD_ADMIN_USERS: list[str] = ["jr-nguyenthanhtuan-ty"]

    # AI settings
    LD_AI_NAME: str = "LD"
    LD_AI_VERSION: str = "1.0"
    LD_LLM_ENABLED: bool = True
    LD_LLM_PROVIDER: str = "siliconflow"          # siliconflow | ollama
    LD_TEXT_MODEL: str = "Qwen/Qwen2.5-7B-Instruct"
    LD_VISION_MODEL: str = "Qwen/Qwen3-VL-8B-Instruct"
    LD_TEMPERATURE: float = 0.35
    LD_MAX_TOKENS: int = 1000                     # ~5-8s với Qwen2.5-7B free tier
    LD_ENABLE_THINKING: bool = False

    # SiliconFlow endpoint
    LD_LLM_BASE_URL: str = "https://api.siliconflow.com/v1"
    LD_LLM_API_KEY: str | None = None

    class Config:
        env_file = str(_BACKEND_DIR / ".env")
        extra = "ignore"


settings = Settings()
