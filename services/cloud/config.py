"""Explicit cloud-only configuration; exception text never contains values."""

from dataclasses import dataclass, field
import base64
import json
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
    release_sha: str | None = None
    evidence_mode: str = "METADATA_ONLY"
    evidence_policy: str | None = None
    evidence_keks: tuple[tuple[str, bytes], ...] = field(default=(), repr=False)
    evidence_kek_version: str | None = None
    evidence_store_backend: str | None = None
    evidence_store_path: Path | None = field(default=None, repr=False)
    r2_account_id: str | None = None
    r2_jurisdiction: str | None = None
    r2_bucket: str | None = None
    r2_access_key_id: str | None = field(default=None, repr=False)
    r2_secret_access_key: str | None = field(default=None, repr=False)
    r2_prefix: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CloudSettings":
        values = os.environ if env is None else env
        environment = values.get("CLOUD_ENV", "staging")
        if environment not in {"production", "staging", "development"}:
            raise ConfigurationError("CLOUD_ENV must be production, staging or development.")
        on_render = values.get("RENDER") == "true"
        if on_render and environment == "development":
            raise ConfigurationError("Render requires CLOUD_ENV=production or staging.")
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
            raise ConfigurationError("Database TLS is required outside development.")
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
                    or (environment != "development" and not parsed.password)
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
        render_release_sha = values.get("RENDER_GIT_COMMIT") or None
        explicit_release_sha = values.get("CLOUD_RELEASE_SHA") or None
        if render_release_sha and not re.fullmatch(r"[0-9a-f]{40}", render_release_sha):
            raise ConfigurationError("RENDER_GIT_COMMIT must be a lowercase 40-hex commit SHA.")
        if explicit_release_sha and not re.fullmatch(r"[0-9a-f]{40}", explicit_release_sha):
            raise ConfigurationError("CLOUD_RELEASE_SHA must be a lowercase 40-hex commit SHA.")
        if on_render and explicit_release_sha:
            raise ConfigurationError("Render release identity must use RENDER_GIT_COMMIT.")
        release_sha = render_release_sha if on_render else explicit_release_sha
        if environment == "production" and (not database_url or not auth_key or not release_sha):
            raise ConfigurationError("Production requires DATABASE_URL, CLOUD_AUTH_KEY and a release commit SHA.")
        evidence_mode = values.get("CLOUD_EVIDENCE_MODE", "METADATA_ONLY")
        if evidence_mode not in {"METADATA_ONLY", "ENCRYPTED"}:
            raise ConfigurationError("CLOUD_EVIDENCE_MODE must be METADATA_ONLY or ENCRYPTED.")
        evidence_policy = values.get("CLOUD_EVIDENCE_POLICY") or None
        evidence_kek_version = values.get("CLOUD_EVIDENCE_KEK_VERSION") or None
        evidence_backend = values.get("CLOUD_EVIDENCE_STORE_BACKEND") or None
        evidence_store = values.get("CLOUD_EVIDENCE_STORE_PATH") or None
        r2_account = values.get("CLOUD_R2_ACCOUNT_ID") or None
        r2_jurisdiction = values.get("CLOUD_R2_JURISDICTION") or None
        r2_bucket = values.get("CLOUD_R2_BUCKET") or None
        r2_access = values.get("CLOUD_R2_ACCESS_KEY_ID") or None
        r2_secret = values.get("CLOUD_R2_SECRET_ACCESS_KEY") or None
        r2_prefix = values.get("CLOUD_R2_PREFIX", "")
        if values.get("CLOUD_EVIDENCE_KEK"):
            raise ConfigurationError("CLOUD_EVIDENCE_KEK is obsolete; configure the versioned CLOUD_EVIDENCE_KEKS keyring.")
        encoded_keks = values.get("CLOUD_EVIDENCE_KEKS") or None
        evidence_keks: tuple[tuple[str, bytes], ...] = ()
        if encoded_keks:
            try:
                def unique_object(pairs):
                    result = {}
                    for name, value in pairs:
                        if name in result:
                            raise ValueError
                        result[name] = value
                    return result

                keyring = json.loads(encoded_keks, object_pairs_hook=unique_object)
                if type(keyring) is not dict or not 1 <= len(keyring) <= 8:
                    raise ValueError
                decoded = []
                for version, encoded in keyring.items():
                    if (type(version) is not str or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", version)
                            or type(encoded) is not str):
                        raise ValueError
                    key = base64.urlsafe_b64decode(encoded)
                    if len(key) != 32 or base64.urlsafe_b64encode(key).decode() != encoded:
                        raise ValueError
                    decoded.append((version, key))
                evidence_keks = tuple(sorted(decoded))
            except (json.JSONDecodeError, ValueError, TypeError):
                raise ConfigurationError("CLOUD_EVIDENCE_KEKS must be a JSON object of unique versions and canonical 32-byte base64url keys.") from None
        if evidence_mode == "ENCRYPTED":
            if (evidence_policy != "SHORT_LIVED_V1" or not evidence_keks
                    or not evidence_kek_version or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", evidence_kek_version)
                    or evidence_kek_version not in dict(evidence_keks)
                    or evidence_backend not in {"FILESYSTEM", "R2"}):
                raise ConfigurationError("Encrypted cloud evidence requires policy, a current key present in the keyring, and an explicit store backend.")
            if evidence_backend == "FILESYSTEM":
                if on_render or not evidence_store or not Path(evidence_store).is_absolute() or any(
                        (r2_account, r2_jurisdiction, r2_bucket, r2_access, r2_secret, r2_prefix)):
                    raise ConfigurationError("Filesystem evidence requires an absolute development path and is unavailable on Render.")
            elif (evidence_store or not re.fullmatch(r"[0-9a-f]{32}", r2_account or "")
                    or r2_jurisdiction != "eu"
                    or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?", r2_bucket or "")
                    or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", r2_access or "")
                    or not r2_secret or not 32 <= len(r2_secret) <= 256
                    or any(ord(character) < 33 or ord(character) > 126 for character in r2_secret)
                    or (r2_prefix and not re.fullmatch(r"[a-z0-9](?:[a-z0-9/_-]{0,126}[a-z0-9])?", r2_prefix))):
                raise ConfigurationError("R2 evidence requires a private EU bucket and valid scoped S3 credentials.")
        elif any((evidence_policy, evidence_keks, evidence_kek_version, evidence_backend, evidence_store,
                  r2_account, r2_jurisdiction, r2_bucket, r2_access, r2_secret, r2_prefix)):
            raise ConfigurationError("Evidence configuration requires CLOUD_EVIDENCE_MODE=ENCRYPTED.")
        return cls(
            environment=environment,
            allowed_hosts=allowed_hosts,
            database_url=database_url,
            database_sslmode=sslmode,
            bind_host="0.0.0.0" if on_render else "127.0.0.1",
            port=port,
            auth_key=auth_key,
            bootstrap_token=bootstrap_token,
            release_sha=release_sha,
            evidence_mode=evidence_mode,
            evidence_policy=evidence_policy,
            evidence_keks=evidence_keks,
            evidence_kek_version=evidence_kek_version,
            evidence_store_backend=evidence_backend,
            evidence_store_path=Path(evidence_store) if evidence_store else None,
            r2_account_id=r2_account,
            r2_jurisdiction=r2_jurisdiction,
            r2_bucket=r2_bucket,
            r2_access_key_id=r2_access,
            r2_secret_access_key=r2_secret,
            r2_prefix=r2_prefix,
        )
