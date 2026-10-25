"""Write and read blobs under a local folder or an s3://bucket/prefix URI."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache
def _s3() -> Any:
    import boto3  # only the archive path needs it

    return boto3.client("s3")


def _split(uri: str) -> tuple[str, str]:
    bucket, _, prefix = uri.removeprefix("s3://").partition("/")
    return bucket, prefix.strip("/")


def put_bytes(base: str, key: str, data: bytes) -> str:
    if base.startswith("s3://"):
        bucket, prefix = _split(base)
        full = f"{prefix}/{key}" if prefix else key
        _s3().put_object(Bucket=bucket, Key=full, Body=data)
        return f"s3://{bucket}/{full}"
    path = Path(base) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def get_bytes(base: str, key: str) -> bytes:
    if base.startswith("s3://"):
        bucket, prefix = _split(base)
        full = f"{prefix}/{key}" if prefix else key
        return _s3().get_object(Bucket=bucket, Key=full)["Body"].read()
    return (Path(base) / key).read_bytes()


def exists(base: str, key: str) -> bool:
    if base.startswith("s3://"):
        bucket, prefix = _split(base)
        full = f"{prefix}/{key}" if prefix else key
        try:
            _s3().head_object(Bucket=bucket, Key=full)
            return True
        except Exception:
            return False
    return (Path(base) / key).exists()


def list_keys(base: str, prefix: str = "") -> list[str]:
    if base.startswith("s3://"):
        bucket, root = _split(base)
        full = f"{root}/{prefix}".strip("/")
        keys: list[str] = []
        for page in _s3().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=full):
            keys += [o["Key"].removeprefix(root).lstrip("/") for o in page.get("Contents", [])]
        return sorted(keys)
    root_path = Path(base)
    if not root_path.exists():
        return []
    return sorted(str(p.relative_to(root_path)) for p in (root_path / prefix).rglob("*") if p.is_file())
