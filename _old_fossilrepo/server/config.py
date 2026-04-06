"""Server configuration for Fossil repository hosting."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class ServerConfig(BaseSettings):
    """Configuration for the Fossil server infrastructure.

    Values are loaded from environment variables prefixed with FOSSILREPO_.
    For example, FOSSILREPO_DATA_DIR sets data_dir.
    """

    model_config = {"env_prefix": "FOSSILREPO_"}

    data_dir: Path = Field(
        default=Path("/data/repos"),
        description="Directory where .fossil repository files are stored.",
    )

    caddy_domain: str = Field(
        default="localhost",
        description="Base domain for subdomain routing (e.g., fossilrepos.io).",
    )

    caddy_config_path: Path = Field(
        default=Path("/etc/caddy/Caddyfile"),
        description="Path to the Caddy configuration file.",
    )

    fossil_port: int = Field(
        default=8080,
        description="Port the fossil server listens on.",
    )

    s3_bucket: str = Field(
        default="",
        description="S3 bucket for Litestream replication.",
    )

    s3_endpoint: str = Field(
        default="",
        description="S3-compatible endpoint URL (for MinIO, R2, etc.).",
    )

    s3_access_key_id: str = Field(
        default="",
        description="AWS access key ID for S3 replication.",
    )

    s3_secret_access_key: str = Field(
        default="",
        description="AWS secret access key for S3 replication.",
    )

    s3_region: str = Field(
        default="us-east-1",
        description="AWS region for S3 bucket.",
    )

    litestream_config_path: Path = Field(
        default=Path("/etc/litestream.yml"),
        description="Path to the Litestream configuration file.",
    )
