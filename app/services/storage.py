"""File storage abstraction: Amazon S3 when configured, local disk otherwise.

Uploaded bank statements (and, later, report attachments) are persisted through
this module. When the S3 settings are present the bytes are written to
``s3://<aws_s3_bucket>/<aws_s3_prefix>...`` and downloads are served via
short-lived presigned URLs. With no S3 settings the files are written under
``settings.upload_dir`` on local disk (single-box / dev), preserving the
original behaviour.

The stored *reference* returned by :func:`save` is what the caller persists on
the model (e.g. ``BankStatement.file_path``):
  * S3   -> ``s3://<bucket>/<key>``
  * local-> an absolute/relative filesystem path
:func:`resolve_url` turns that reference back into a downloadable URL (S3) or
signals that the caller should stream the local file (returns ``None``).
"""

from __future__ import annotations

import os
import uuid

from app.config import settings

try:  # boto3 is only required when S3 is actually enabled.
    import boto3
except ImportError:  # pragma: no cover - optional dependency
    boto3 = None


def s3_enabled() -> bool:
    """True when uploads should go to S3 (bucket configured and boto3 present)."""
    return bool(settings.aws_s3_bucket) and boto3 is not None


def _client():
    # Credentials fall back to the standard AWS chain (env / instance role) when
    # the explicit keys are blank, so the same code works with an IAM role too.
    return boto3.client(
        "s3",
        region_name=settings.aws_region or None,
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
    )


def _unique_name(filename: str) -> str:
    return f"{uuid.uuid4().hex}_{filename}"


def _s3_key(filename: str) -> str:
    prefix = (settings.aws_s3_prefix or "").strip("/")
    name = _unique_name(filename)
    return f"{prefix}/{name}" if prefix else name


def save(content: bytes, filename: str, content_type: str | None = None) -> str:
    """Persist ``content`` and return a storage reference (see module docstring)."""
    safe_filename = filename or "upload"

    if s3_enabled():
        key = _s3_key(safe_filename)
        extra = {"ContentType": content_type} if content_type else {}
        _client().put_object(
            Bucket=settings.aws_s3_bucket, Key=key, Body=content, **extra
        )
        return f"s3://{settings.aws_s3_bucket}/{key}"

    os.makedirs(settings.upload_dir, exist_ok=True)
    path = os.path.join(settings.upload_dir, _unique_name(safe_filename))
    with open(path, "wb") as fh:
        fh.write(content)
    return path


def resolve_url(reference: str, expires_in: int = 900) -> str | None:
    """Return a downloadable URL for a reference, or ``None`` for a local path.

    S3 references resolve to a presigned GET URL valid for ``expires_in`` seconds.
    Local references have no public URL; the caller should stream the file.
    """
    if reference and reference.startswith("s3://") and s3_enabled():
        _, _, rest = reference.partition("s3://")
        bucket, _, key = rest.partition("/")
        return _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    return None
