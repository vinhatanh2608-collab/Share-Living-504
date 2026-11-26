from functools import lru_cache
import re
from typing import Literal
from urllib.parse import urlsplit
from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True, populate_by_name=True)
    environment: Literal["development", "production"] = "development"
    mysql_url: str
    mongo_url: str
    mongo_database: str = "dorm"
    jwt_secret: SecretStr = Field(validation_alias=AliasChoices("JWT_SECRET", "SECRET_KEY"))
    owner_username: str
    cors_origins: list[str] = []
    s3_endpoint: str
    s3_public_endpoint: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "dorm"
    s3_access_key: SecretStr
    s3_secret_key: SecretStr
    legacy_room: bool = False
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None

    @field_validator("jwt_secret")
    @classmethod
    def strong_signing_key(cls, value):
        key = value.get_secret_value()
        if (len(key.encode()) < 32 or len(set(key)) < 12 or key != key.strip()
                or re.fullmatch(r"(.{1,24})\1+", key)
                or any(marker in key.lower() for marker in ("replace-me", "changeme", "change-me", "development-only"))):
            raise ValueError("Set a unique random JWT signing key of at least 32 bytes")
        return value

    @field_validator("cors_origins")
    @classmethod
    def explicit_origins(cls, origins):
        for origin in origins:
            url = urlsplit(origin)
            if (url.scheme not in {"http", "https", "tauri"} or not url.hostname or url.username
                    or url.password or url.path or url.query or url.fragment or "*" in origin):
                raise ValueError("CORS origins must be explicit origins")
        return origins

    @field_validator("s3_endpoint", "s3_public_endpoint")
    @classmethod
    def storage_endpoint(cls, value):
        if value is None:
            return None
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in {"", "/"}):
            raise ValueError("Use an HTTP(S) storage endpoint without credentials or a path")
        return value.rstrip("/")

    @model_validator(mode="after")
    def production_guard(self):
        if self.environment == "development":
            self.cors_origins = list(dict.fromkeys([*self.cors_origins,
                "http://127.0.0.1:1420", "http://localhost:1420"]))
        public_endpoint = self.s3_public_endpoint or self.s3_endpoint
        if self.environment == "production" and (
            not (self.s3_endpoint.startswith("https://") or self.s3_endpoint == "http://minio:9000")
            or not public_endpoint.startswith("https://")
        ):
            raise ValueError("Production needs HTTPS storage and a unique JWT key")
        return self


settings = lru_cache(Settings)
