from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; secrets are read from environment, never persisted by Jarvy."""

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.0-flash", alias="GEMINI_MODEL")
    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    groq_model: str = Field(default="llama-3.3-70b-versatile", alias="GROQ_MODEL")
    groq_base_url: str = Field(default="https://api.groq.com/openai/v1", alias="GROQ_BASE_URL")
    data_dir: Path = Field(default=Path("~/.jarvy"), alias="JARVY_DATA_DIR")
    allowed_dirs: list[Path] = Field(
        default_factory=lambda: [Path("~/Documents"), Path("~/Downloads")],
        alias="JARVY_ALLOWED_DIRS",
    )
    download_dir: Path | None = Field(default=None, alias="JARVY_DOWNLOAD_DIR")
    max_steps: int = Field(default=8, ge=1, le=32, alias="JARVY_MAX_STEPS")
    dry_run: bool = Field(default=False, alias="JARVY_DRY_RUN")
    voice_model_dir: Path | None = Field(default=None, alias="JARVY_VOSK_MODEL_DIR")
    host: str = Field(default="127.0.0.1", alias="JARVY_HOST")
    port: int = Field(default=8000, ge=1, le=65535, alias="JARVY_PORT")
    request_timeout_seconds: float = Field(
        default=30.0, gt=0, le=180, alias="JARVY_REQUEST_TIMEOUT"
    )

    @field_validator("allowed_dirs", mode="before")
    @classmethod
    def parse_allowed_dirs(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    def prepare(self) -> None:
        self.data_dir = self.data_dir.expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.voice_model_dir is not None:
            self.voice_model_dir = self.voice_model_dir.expanduser().resolve()
        self.allowed_dirs = [path.expanduser().resolve() for path in self.allowed_dirs]
        if self.download_dir is None:
            if not self.allowed_dirs:
                raise ValueError("Configure an allowed directory before enabling downloads")
            self.download_dir = self.allowed_dirs[-1] / "JarvyDownloads"
        else:
            self.download_dir = self.download_dir.expanduser().resolve()
        if not any(
            self.download_dir == root or root in self.download_dir.parents
            for root in self.allowed_dirs
        ):
            raise ValueError("JARVY_DOWNLOAD_DIR must be inside a configured allowed directory")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.prepare()
    return settings
