from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEFAULT_SECRET = "dev-only-insecure-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "NeuroBridge AI"
    app_version: str = "0.1.0"
    environment: str = "development"  # development | test | production

    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "neurobridge"
    secret_key: str = INSECURE_DEFAULT_SECRET
    encryption_key: str = ""  # Fernet key; derived from secret_key in dev only
    hmac_key: str = ""  # key for blind indexes (searchable hashes of PHI); falls back to secret_key in dev

    storage_dir: str = "./storage"  # originals (encrypted), de-identified copies, upload staging
    sedation_model: str = "sedation_v2"  # folder name under model_dir used by the AI analysis page
    model_dir: str = "../ml/models"  # trained models (each in <name>/model.joblib + model_card.json); loaded only after a SHA-256 check
    max_upload_bytes: int = 2 * 1024**3
    chunk_size: int = 8 * 1024 * 1024
    max_open_uploads_per_user: int = 5

    site_timezone: str = "UTC"  # EDF start times carry no time zone; they are read in this zone (IANA name)
    reminder_window_days: int = 10  # repeated-assessment reminder (literature suggests several CRS-R in a short window)
    reminder_min_assessments: int = 5
    line_freq_hz: int = 50  # mains frequency of this site (50 or 60); recorded in every processing run
    jobs_inline: bool = False  # run background jobs synchronously (tests)
    job_workers: int = 2
    max_process_bytes: int = 3 * 1024**3  # refuse to load more than this into memory for one run

    access_token_minutes: int = 15
    refresh_token_days: int = 7

    max_failed_logins: int = 5
    lockout_minutes: int = 15
    min_password_length: int = 12

    cookie_secure: bool = False
    cors_origins: str = "http://localhost:5173"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate_for_production(self) -> None:
        if not self.is_production:
            return
        problems = []
        if self.secret_key == INSECURE_DEFAULT_SECRET or len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be set to a random value of at least 32 characters")
        if not self.encryption_key:
            problems.append("ENCRYPTION_KEY (Fernet) must be set")
        if not self.hmac_key:
            problems.append("HMAC_KEY must be set (separate from SECRET_KEY)")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true")
        if "@" not in self.mongo_url:
            problems.append("MONGO_URL must include credentials (authentication enabled)")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
