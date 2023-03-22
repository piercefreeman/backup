from pydantic import (
    BaseSettings,
    AnyHttpUrl,
)
from enum import Enum


class BackupBackend(Enum):
    LOCAL = "LOCAL"
    B2 = "B2"


class Settings(BaseSettings):
    class Config:
        env_nested_delimiter = '__'

    icloud_photos_username: str

    backup_backend: BackupBackend

    b2_endpoint: AnyHttpUrl | None = None
    b2_key_id: str | None = None
    b2_application_key: str | None = None
    b2_bucket_name: str | None = None
