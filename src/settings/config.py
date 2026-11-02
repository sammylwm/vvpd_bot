import os
from pathlib import Path
from typing import ClassVar

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

env_path = Path(__file__).parent / ".env"


class ConfigBase(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(env_path), env_file_encoding="utf-8", extra="ignore"
    )


class BotConfig(ConfigBase):
    token: SecretStr = SecretStr("")


class DatabaseConfig(ConfigBase):
    model_config = SettingsConfigDict(env_prefix="POSTGRES_")
    db: str = ""
    host: ClassVar[str] = "db" if os.getenv("APP_ENV", "dev") == "prod" else "localhost"
    user: str = ""
    password: SecretStr = SecretStr("")

    @property
    def async_url(self) -> str:
        return URL.create(
            drivername="postgresql+asyncpg",
            database=self.db,
            host=self.host,
            username=self.user,
            password=self.password.get_secret_value(),
        ).render_as_string(hide_password=False)


class AiConfig(ConfigBase):
    model_config = SettingsConfigDict(env_prefix="AI_")
    key: SecretStr = SecretStr("")


class ApiConfig(ConfigBase):
    model_config = SettingsConfigDict(env_prefix="API_")
    path: str = ""


class Config(BaseSettings):
    bot: BotConfig = BotConfig()
    database: DatabaseConfig = DatabaseConfig()
    ai: AiConfig = AiConfig()
    api: ApiConfig = ApiConfig()


config = Config()
