"""Explicit cloud-only configuration; exception text never contains values."""

from dataclasses import dataclass, field
import base64
import os
import re
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlsplit
from pathlib import Path


class ConfigurationError(ValueError):
    pass


def _hostname(value: str) -> str:
    if len(value) > 253 or not re.fullmatch(
        r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", value
    ) or any(not part or len(part) > 63 or part.startswith("-") or part.endswith("-")
             for part in value.split(".")):
        raise ConfigurationError("Cloud allowed hosts must be explicit hostnames.")
    return value.lower()


@dataclass(frozen=True)
class CloudSettings:
    environment: str
    allowed_hosts: tuple[str, ...]
    database_url: str | None = field(repr=False)
    database_sslmode: str
    bind_host: str
    port: int
    auth_key: str | None = field(default=None, repr=False)
    bootstrap_token: str | None = field(default=None, repr=False)
    evidence_mode: str = "METADATA_ONLY"
    evidence_policy: str | None = None
    evidence_kek: bytes | None = field(default=None, repr=False)
    evidence_kek_version: str | None = None
    evidence_store_path: Path | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CloudSettings":
        values = os.environ if env is None else env
        environment = values.get("CLOUD_ENV", "staging")
        if environment not in {"staging", "development"}:
            raise ConfigurationError("CLOUD_ENV must be staging or development.")
        on_render = values.get("RENDER") == "true"
        if on_render and environment != "staging":
            raise ConfigurationError("Render requires CLOUD_ENV=staging.")
        hosts = [v.strip() for v in values.get("CLOUD_ALLOWED_HOSTS", "").split(",") if v.strip()]
        if hostname := values.get("RENDER_EXTERNAL_HOSTNAME"):
            hosts.append(hostname)
        if not hosts and environment == "development":
            hosts = ["localhost", "127.0.0.1"]
        if not hosts or len(hosts) > 16:
            raise ConfigurationError("Configure CLOUD_ALLOWED_HOSTS or RENDER_EXTERNAL_HOSTNAME.")
        allowed_hosts = tuple(dict.fromkeys(_hostname(host) for host in hosts))
        try:
            port = int(values.get("PORT", "10000"))
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise ConfigurationError("PORT must be an integer between 1024 and 65535.") from None

        sslmode = values.get("CLOUD_DATABASE_SSLMODE", "require")
        database_url = values.get("DATABASE_URL") or None
        if sslmode not in {"require", "disable"}:
            raise ConfigurationError("CLOUD_DATABASE_SSLMODE must be require or development-only disable.")
        if sslmode == "disable" and environment != "development":
            raise ConfigurationError("Database TLS is required in staging.")
        if database_url:
            try:
                parsed = urlsplit(database_url)
                # Keep libpq's service/file/environment indirection out of this scaffold.
                query = parse_qsl(parsed.query, strict_parsing=True, max_num_fields=1)
                if (
                    len(database_url) > 8192
                    or any(ord(c) < 32 for c in database_url)
                    or parsed.scheme not in {"postgres", "postgresql"}
                    or not parsed.hostname
                    or not parsed.username
                    or not parsed.path.startswith("/")
                    or len(parsed.path) < 2
                    or "/" in parsed.path[1:]
                    or parsed.fragment
                    or (query and query != [("sslmode", sslmode)])
                    or (parsed.port is not None and not 1 <= parsed.port <= 65535)
                    or (environment == "staging" and not parsed.password)
                    or (sslmode == "disable" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
                ):
                    raise ValueError
                _hostname(parsed.hostname) if parsed.hostname != "::1" else None
            except (ValueError, UnicodeError):
                raise ConfigurationError("DATABASE_URL must be a valid PostgreSQL URL with the configured TLS mode.") from None
        auth_key = values.get("CLOUD_AUTH_KEY") or None
        bootstrap_token = values.get("CLOUD_BOOTSTRAP_TOKEN") or None
        if auth_key:
            try:
                decoded = base64.urlsafe_b64decode(auth_key)
                if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode() != auth_key:
                    raise ValueError
            except (ValueError, TypeError):
                raise ConfigurationError("CLOUD_AUTH_KEY must be a canonical base64url-encoded 32-byte secret.") from None
        if bootstrap_token and (not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", bootstrap_token) or not auth_key):
            raise ConfigurationError("CLOUD_BOOTSTRAP_TOKEN requires a random URL-safe secret and CLOUD_AUTH_KEY.")
        evidence_mode = values.get("CLOUD_EVIDENCE_MODE", "METADATA_ONLY")
        if evidence_mode not in {"METADATA_ONLY", "ENCRYPTED"}:
            raise ConfigurationError("CLOUD_EVIDENCE_MODE must be METADATA_ONLY or ENCRYPTED.")
        evidence_policy = values.get("CLOUD_EVIDENCE_POLICY") or None
        evidence_kek_version = values.get("CLOUD_EVIDENCE_KEK_VERSION") or None
        evidence_store = values.get("CLOUD_EVIDENCE_STORE_PATH") or None
        encoded_kek = values.get("CLOUD_EVIDENCE_KEK") or None
        evidence_kek = None
        if encoded_kek:
            try:
                evidence_kek = base64.urlsafe_b64decode(encoded_kek)
                if len(evidence_kek) != 32 or base64.urlsafe_b64encode(evidence_kek).decode() != encoded_kek:
                    raise ValueError
            except (ValueError, TypeError):
                raise ConfigurationError("CLOUD_EVIDENCE_KEK must be a canonical base64url-encoded 32-byte secret.") from None
        if evidence_mode == "ENCRYPTED":
            if (evidence_policy != "SHORT_LIVED_V1" or evidence_kek is None
                    or not evidence_kek_version or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", evidence_kek_version)
                    or not evidence_store or not Path(evidence_store).is_absolute()):
                raise ConfigurationError("Encrypted cloud evidence requires policy, key version, KEK and an absolute store path.")
        elif any((evidence_policy, evidence_kek, evidence_kek_version, evidence_store)):
            raise ConfigurationError("Evidence policy, KEK and store require CLOUD_EVIDENCE_MODE=ENCRYPTED.")
        return cls(
            environment=environment,
            allowed_hosts=allowed_hosts,
            database_url=database_url,
            database_sslmode=sslmode,
            bind_host="0.0.0.0" if on_render else "127.0.0.1",
            port=port,
            auth_key=auth_key,
            bootstrap_token=bootstrap_token,
            evidence_mode=evidence_mode,
            evidence_policy=evidence_policy,
            evidence_kek=evidence_kek,
            evidence_kek_version=evidence_kek_version,
            evidence_store_path=Path(evidence_store) if evidence_store else None,
        )
