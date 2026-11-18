from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    ENV: str = "dev"
    PROJECT_NAME: str = "GLANCE API"
    DATABASE_URL: str
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    AES_ENCRYPTION_KEY: str
    STORAGE_BACKEND: str = "local"
    LOCAL_STORAGE_PATH: str = "/app/local_storage"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
