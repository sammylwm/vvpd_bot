from pathlib import Path

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


class ApiConfig(ConfigBase):
    model_config = SettingsConfigDict(env_prefix="API_")
    path: str = "http://localhost:8000"


class DatabaseConfig(ConfigBase):
    model_config = SettingsConfigDict(env_prefix="POSTGRES_")
    db: str = "vvpd_bot"
    host: str = "localhost"
    user: str = "vvpd_bot"
    password: SecretStr = SecretStr("")

    @property
    def url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.user,
            password=self.password.get_secret_value(),
            host=self.host,
            database=self.db,
        )


class Config(BaseSettings):
    bot: BotConfig = BotConfig()
    api: ApiConfig = ApiConfig()
    database: DatabaseConfig = DatabaseConfig()


config = Config()
