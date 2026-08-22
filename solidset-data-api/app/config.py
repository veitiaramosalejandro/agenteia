import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SOLIDSET_DATA_API_KEY: str
    SQL_SERVER_HOST: str = "localhost"
    SQL_SERVER_INSTANCE: str | None = None
    SQL_SERVER_PORT: int = 1433
    SQL_SERVER_DATABASE: str
    SQL_SERVER_USERNAME: str
    SQL_SERVER_PASSWORD: str
    SQL_SERVER_LOGIN_TIMEOUT: int = 15
    SQL_SERVER_QUERY_TIMEOUT: int = 120
    SOLIDSET_DATA_API_MAX_ROWS: int = 5000

    def host(self) -> str:
        host = self.SQL_SERVER_HOST.strip().rstrip("\\")
        running_in_docker = os.path.exists("/.dockerenv") or os.getenv("RUNNING_IN_DOCKER") == "1"
        if running_in_docker and host.lower() in {"localhost", "127.0.0.1", "::1"}:
            return "host.docker.internal"
        return host

    def server(self) -> str:
        host = self.host()
        instance = (self.SQL_SERVER_INSTANCE or "").strip().strip("\\")
        return f"{host}\\{instance}" if instance else host


settings = Settings()
