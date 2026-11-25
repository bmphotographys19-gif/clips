"""Almacenamiento de clips: disco local o S3 compatible (R2, Backblaze B2, MinIO, AWS)."""
from __future__ import annotations

import shutil
from pathlib import Path

from .config import get_settings


def _s3():
    import boto3

    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint_url or None,
        region_name=s.s3_region or None,
        aws_access_key_id=s.s3_access_key_id or None,
        aws_secret_access_key=s.s3_secret_access_key or None,
    )


def put_file(local: Path, key: str, content_type: str) -> str:
    s = get_settings()
    if s.storage_backend == "s3":
        _s3().upload_file(str(local), s.s3_bucket, key, ExtraArgs={"ContentType": content_type})
    else:
        dest = s.clips_dir / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        if local.resolve() != dest.resolve():
            shutil.copyfile(local, dest)
    return key


def url_for(key: str, expires: int = 6 * 3600) -> str:
    if not key:
        return ""
    s = get_settings()
    if s.storage_backend == "s3":
        return _s3().generate_presigned_url(
            "get_object", Params={"Bucket": s.s3_bucket, "Key": key}, ExpiresIn=expires
        )
    return f"/media/{key}"


def fetch_local(key: str, dest: Path) -> Path:
    """Devuelve una ruta local con el archivo (descargándolo si está en S3)."""
    s = get_settings()
    if s.storage_backend == "s3":
        dest.parent.mkdir(parents=True, exist_ok=True)
        _s3().download_file(s.s3_bucket, key, str(dest))
        return dest
    return s.clips_dir / key


def delete(key: str) -> None:
    if not key:
        return
    s = get_settings()
    if s.storage_backend == "s3":
        _s3().delete_object(Bucket=s.s3_bucket, Key=key)
    else:
        (s.clips_dir / key).unlink(missing_ok=True)
