"""S3 attachment upload helper.

This module provides a thin wrapper around boto3 to upload email attachments to a
(public) S3 bucket and return an HTTPS URL suitable for embedding in Notion
"files" properties.

Design goals:
- Only perform work if S3_ATTACHMENTS_BUCKET env set.
- Avoid raising exceptions to calling code; log and return None on failure.
- Generate reasonably unique object keys using date + uuid.
- Allow overriding public base URL via S3_PUBLIC_BASE_URL (e.g. CDN domain).

Security / Privacy:
Assumes bucket (or CDN) is publicly readable. Do not enable this for sensitive
attachments unless access controls are enforced (feature intentionally simple).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import mimetypes
import os
import re
import uuid
from typing import Optional

import boto3

from notion_automation.http_async import get_session

logger = logging.getLogger(__name__)

_DEF_REGION = "us-east-1"


def s3_enabled(url: str | None = None) -> bool:
    """Return True if S3 attachment uploading is enabled via env configuration.

    Currently requires ``S3_ATTACHMENTS_BUCKET`` to be set (other vars like
    region are optional). A separate feature flag variable can be added later
    if needed; for now bucket presence implies enablement.
    """
    bucket = os.getenv("S3_ATTACHMENTS_BUCKET")
    return bool(bucket) and not (url and url.startswith(public_url('')))


def _client():  # pragma: no cover simple wrapper
    """Internal boto3 client factory honoring region env var.

    Raises:
        RuntimeError: if boto3 is not installed but uploads attempted.
    """
    if not boto3:
        raise RuntimeError("boto3 not available - install dependency to enable S3 uploads")
    region = os.getenv("S3_ATTACHMENTS_REGION") or _DEF_REGION
    return boto3.client("s3", region_name=region)


def ensure_filename(fname: str, ctype: Optional[str]) -> str:
    ctype = ctype or mimetypes.guess_type(fname)[0] or "application/octet-stream"
    fname = fname.split("?")[0]
    fname = fname.split("#")[0]
    fname = fname.split("/")[-1]
    if not fname:
        fname = "attachment"
    ext = ''
    if '.' not in fname and "/" in ctype:
        _, mt_sub = ctype.split("/", 1)
        ext = f".{mt_sub.split('+')[0][:8]}"
    if "." in fname:
        fname, ext = fname.rsplit(".", 1)
    fname = re.sub(r"[^a-zA-Z0-9_-]", "-", fname)
    fname = fname[:80 - len(ext)]
    if ext:
        fname = f"{fname}.{ext}"
    return fname


def build_key(filename: str) -> str:
    """Generate an object key path for an uploaded file.

    Layout: ``<prefix>/<YYYY>/<MM>/<DD>/<uuid>-<sanitizedName>[.ext]``.
    Name portion is truncated to keep overall key length manageable.
    """
    prefix = os.getenv("S3_ATTACHMENTS_PREFIX", "email-attachments/")
    today = _dt.datetime.utcnow().strftime("%Y/%m/%d")
    return f"{prefix}{today}/{uuid.uuid4().hex}-{filename}"


def public_url(key: str) -> str:
    """Return a public HTTP(S) URL for an uploaded object key.

    Prefers ``S3_PUBLIC_BASE_URL`` when set (allowing CDN/domain mapping),
    otherwise constructs a virtual-hosted–style S3 URL using region logic.
    """
    base = os.getenv("S3_PUBLIC_BASE_URL")
    bucket = os.getenv("S3_ATTACHMENTS_BUCKET")
    region = os.getenv("S3_ATTACHMENTS_REGION") or _DEF_REGION
    if base:
        if not base.endswith("/"):
            base += "/"
        return f"{base}{key}"
    # Virtual-hosted–style URL (works for public buckets in most regions).
    # For us-east-1 the global endpoint variant is also acceptable.
    if region == "us-east-1":
        return f"https://{bucket}.s3.amazonaws.com/{key}"
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


def _s3_upload(filename: str, data: bytes, ctype: Optional[str] = None) -> Optional[str]:
    """Upload raw bytes to S3 (if enabled) and return a public URL.

    Args:
        filename: Original filename (used to derive key & extension heuristics).
        data: Raw file bytes.
        ctype: Optional MIME type hint.

    Returns:
        Public URL string or ``None`` on failure / feature disabled.
    """
    if not s3_enabled():
        return None
    bucket = os.getenv("S3_ATTACHMENTS_BUCKET")
    if not bucket:
        logger.warning("S3 attachments enabled but S3_ATTACHMENTS_BUCKET not set")
        return None
    try:
        ctype = ctype or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        filename = ensure_filename(filename, ctype)
        key = build_key(filename)
        extra_args = {}
        extra_args["ContentType"] = ctype
        # Mark public read (if bucket policy allows) so URL works immediately
        _client().put_object(Bucket=bucket, Key=key, Body=data, **extra_args)
        url = public_url(key)
        logger.debug("Uploaded attachment %s -> s3://%s/%s (%s)", filename, bucket, key, url)
        return url
    except Exception as e:  # pragma: no cover network
        logger.warning("S3 upload failed for %s: %s", filename, getattr(e, 'message', e))
    return None


async def s3_upload(filename: str, data: bytes, ctype: Optional[str] = None) -> Optional[str]:
    return await asyncio.to_thread(_s3_upload, filename, data, ctype)


async def s3_upload_url(url: str) -> Optional[str]:
    try:
        sess = await get_session()
        async with sess.get(url) as resp:
            if resp.status < 300:
                ctype = resp.headers.get("Content-Type")
                data = await resp.read()
                return await s3_upload(url, data, ctype)
    except Exception as e:  # pragma: no cover network
        logger.warning("S3 upload from URL failed for %s: %s", url, getattr(e, 'message', e))
    return None


__all__ = [
    "s3_enabled",
    "s3_upload",
    "s3_upload_url",
]
