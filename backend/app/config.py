from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "development"

    DATABASE_URL: str = "mysql+asyncmy://app_user:password@mysql:3306/buzzer"
    REDIS_URL: str = "redis://redis:6379/0"

    JWT_PRIVATE_KEY: str = ""
    JWT_PUBLIC_KEY: str = ""

    CORS_ORIGINS: str = (
        "http://localhost:8080,http://localhost:8000,http://localhost:5173"
    )
    MAX_ROOMS: int = 50
    # Host-disconnect grace period before an active room is ended (seconds). Tests set it
    # short so the disconnect timer can be exercised.
    HOST_GRACE_SECONDS: int = 300
    FRONTEND_URL: str = "http://localhost:8080"

    # Bootstrap — set once to create the first admin; ignored once an admin exists
    ADMIN_USERNAME: str = ""
    ADMIN_PASSWORD: str = ""

    # Stress testing — set to a long random secret to enable rate-limit bypass and
    # verbose logging. Remove from Portainer when not actively stress-testing.
    STRESS_TEST_KEY: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def is_stress_test_mode(self) -> bool:
        return bool(self.STRESS_TEST_KEY)


settings = Settings()
