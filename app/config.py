import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # Database Configuration
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/doe_scraper",
        description="PostgreSQL Connection URL. Will automatically convert postgres:// to postgresql+asyncpg://"
    )

    # Scraper & Parser Settings
    MAX_PDF_SIZE_BYTES: int = Field(default=10 * 1024 * 1024, description="Max allowed PDF size in bytes (default 10MB)")
    HTTP_TIMEOUT_SECONDS: float = Field(default=30.0, description="HTTP timeout for web scraping and downloads")
    SYNC_INTERVAL_HOURS: int = Field(default=24, description="Sync frequency in hours")
    ENABLE_SCRAPER_SCHEDULER: bool = Field(default=True, description="Enable running the scraper scheduler internally")
    
    # API Security
    API_KEY: str = Field(description="Secret API key for authenticating requests. Required.")

    # Server Settings
    HOST: str = Field(default="0.0.0.0")
    PORT: int = Field(default=8000)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def get_async_database_url(self) -> str:
        """Converts standard postgres:// or postgresql:// URLs to postgresql+asyncpg:// if needed."""
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and not url.startswith("postgresql+asyncpg://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

settings = Settings()
