from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/task_manager"
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_issuer: str = "task-manager"
    jwt_audience: str = "task-manager-clients"
    access_token_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_days: int = Field(default=7, ge=1, le=30)
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)

    @model_validator(mode="after")
    def production_settings(self):
        if self.environment == "production":
            if self.bcrypt_rounds < 12:
                raise ValueError("Production requires BCRYPT_ROUNDS >= 12")
            if not self.database_url.startswith("postgresql+asyncpg://"):
                raise ValueError("Production requires PostgreSQL with asyncpg")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
