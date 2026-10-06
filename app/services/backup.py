"""Database backup, taken immediately before a destructive operation.

Project O2 has no other backup: no scheduled dump, no volume snapshot, nothing
in CI. So for the data-erase endpoint this module is the only thing standing
between a mistaken tap and permanent loss of the company's financial records.

It is therefore written to fail closed. Every step is verified, and any failure
raises rather than returning a partial result, so the caller cannot proceed to
the delete on a dump that does not exist or cannot be read back.

Requires ``pg_dump`` and ``pg_restore`` on PATH (see backend/Dockerfile).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from app.config import settings
from app.services import storage

logger = logging.getLogger("o2.backup")

# pg_dump on a small finance database is seconds, not minutes. A cap stops a
# hung dump from holding the request open indefinitely.
DUMP_TIMEOUT_SECONDS = 600

# A custom-format dump of an empty database is still ~1KB of header. Anything
# smaller means pg_dump produced nothing useful, whatever its exit code said.
MIN_PLAUSIBLE_DUMP_BYTES = 512


class BackupError(RuntimeError):
    """Raised when a backup could not be produced or could not be verified."""


@dataclass(frozen=True)
class BackupManifest:
    """Provenance for one dump. Stored next to it, and returned to the caller."""

    reference: str
    filename: str
    bytes: int
    sha256: str
    table_count: int
    created_at: str
    actor_email: str | None
    reason: str


def _libpq_dsn() -> str:
    """Convert the SQLAlchemy URL into one libpq understands.

    ``settings.database_url`` carries a driver suffix (``postgresql+psycopg://``)
    that psycopg needs and pg_dump does not.
    """
    parts = urlsplit(settings.database_url)
    scheme = parts.scheme.split("+", 1)[0] or "postgresql"
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))


def _run(cmd: list[str], *, what: str) -> subprocess.CompletedProcess[bytes]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=DUMP_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        # The binary is missing from the image entirely.
        raise BackupError(
            f"{what} is not available on this server, so no backup can be taken."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise BackupError(f"{what} timed out after {DUMP_TIMEOUT_SECONDS}s.") from exc

    if proc.returncode != 0:
        # stderr can name the database and host; keep it out of the HTTP response.
        logger.error("%s failed (exit %s): %s", what, proc.returncode, proc.stderr[-2000:])
        raise BackupError(f"{what} failed. The operation was stopped; nothing was deleted.")
    return proc


def _verify(path: str) -> int:
    """Read the dump back with pg_restore --list. Returns the table count.

    Exit code 0 from pg_dump is not sufficient evidence that a restorable file
    exists, so the dump is parsed before anything is destroyed on its promise.
    """
    size = os.path.getsize(path)
    if size < MIN_PLAUSIBLE_DUMP_BYTES:
        raise BackupError(f"The backup is implausibly small ({size} bytes); refusing to continue.")

    listing = _run(["pg_restore", "--list", path], what="pg_restore --list").stdout.decode(
        "utf-8", "replace"
    )
    tables = [ln for ln in listing.splitlines() if " TABLE DATA " in ln]
    if not tables:
        raise BackupError("The backup contains no table data; refusing to continue.")
    return len(tables)


def create_backup(*, actor_email: str | None, reason: str) -> BackupManifest:
    """Dump the database, verify it, store it, and return its manifest.

    Raises BackupError if a verified copy could not be stored. The caller must
    treat any exception as "do not proceed".
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"projecto2-{stamp}.dump"

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, filename)

        # --format=custom so pg_restore can read it selectively; --no-owner and
        # --no-privileges so it restores into a differently-owned database.
        _run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--file",
                path,
                _libpq_dsn(),
            ],
            what="pg_dump",
        )

        table_count = _verify(path)

        with open(path, "rb") as fh:
            blob = fh.read()

    digest = hashlib.sha256(blob).hexdigest()
    reference = storage.save(blob, filename, content_type="application/octet-stream")

    manifest = BackupManifest(
        reference=reference,
        filename=filename,
        bytes=len(blob),
        sha256=digest,
        table_count=table_count,
        created_at=datetime.now(timezone.utc).isoformat(),
        actor_email=actor_email,
        reason=reason,
    )

    # The manifest is stored beside the dump because it has to outlive the
    # database: after an erase, this file and the application log are the only
    # places the event is recorded that the erase itself could not reach.
    storage.save(
        json.dumps(asdict(manifest), indent=2).encode("utf-8"),
        f"{filename}.manifest.json",
        content_type="application/json",
    )

    if not storage.s3_enabled():
        # Local disk shares a failure domain with the database it is protecting.
        logger.warning(
            "Backup %s was written to local disk, not object storage. It will not "
            "survive loss of this host. Set aws_s3_bucket for off-host backups.",
            reference,
        )

    logger.info(
        "Backup created: %s (%s bytes, sha256=%s, %s tables, actor=%s, reason=%s)",
        reference,
        manifest.bytes,
        digest,
        table_count,
        actor_email,
        reason,
    )
    return manifest
