"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration for the telemetry collector."""

    dsn: str = "postgres://telemetry:telemetry@localhost:5432/telemetry"

    model_config = {"env_prefix": "TELEMETRY_"}
