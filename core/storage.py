"""Resolver do backend de arquivos. Sem I/O de rede e sem logar secrets."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from .runtime_env import env_bool

FILESYSTEM_BACKEND = "django.core.files.storage.FileSystemStorage"
S3_BACKEND = "storages.backends.s3.S3Storage"

# Mapping Railway Storage Bucket / S3-compatible → django-storages OPTIONS
# AWS_ACCESS_KEY_ID           → access_key
# AWS_SECRET_ACCESS_KEY       → secret_key
# AWS_STORAGE_BUCKET_NAME     → bucket_name  (alias: AWS_S3_BUCKET_NAME)
# AWS_S3_ENDPOINT_URL         → endpoint_url (alias: AWS_ENDPOINT_URL)
# AWS_S3_REGION_NAME          → region_name  (alias: AWS_DEFAULT_REGION, AWS_REGION)
# AWS_S3_ADDRESSING_STYLE     → addressing_style
# DJANGO_OBJECT_STORAGE_REQUIRED=true → exige config completa mesmo em DEBUG


def _first(environ: dict, *names: str) -> str:
    for name in names:
        value = str(environ.get(name) or "").strip()
        if value:
            return value
    return ""


def s3_config_from_environ(environ: dict) -> dict:
    required = env_bool(
        environ.get("DJANGO_OBJECT_STORAGE_REQUIRED"),
        default=False,
        name="DJANGO_OBJECT_STORAGE_REQUIRED",
    )
    cfg = {
        "bucket": _first(environ, "AWS_STORAGE_BUCKET_NAME", "AWS_S3_BUCKET_NAME"),
        "access_key": _first(environ, "AWS_ACCESS_KEY_ID", "AWS_S3_ACCESS_KEY_ID"),
        "secret_key": _first(
            environ, "AWS_SECRET_ACCESS_KEY", "AWS_S3_SECRET_ACCESS_KEY"
        ),
        "endpoint_url": _first(environ, "AWS_S3_ENDPOINT_URL", "AWS_ENDPOINT_URL"),
        "region_name": _first(
            environ, "AWS_S3_REGION_NAME", "AWS_DEFAULT_REGION", "AWS_REGION"
        ),
        "addressing_style": _first(environ, "AWS_S3_ADDRESSING_STYLE"),
        "required": required,
    }
    present = any(
        cfg[k]
        for k in (
            "bucket",
            "access_key",
            "secret_key",
            "endpoint_url",
            "addressing_style",
        )
    )
    cfg["declared"] = bool(present or required)
    cfg["complete"] = bool(cfg["bucket"] and cfg["access_key"] and cfg["secret_key"])
    return cfg


def _s3_options(cfg: dict) -> dict:
    options = {
        "bucket_name": cfg["bucket"],
        "access_key": cfg["access_key"],
        "secret_key": cfg["secret_key"],
        "file_overwrite": False,
        "default_acl": None,
        "querystring_auth": True,
        "signature_version": "s3v4",
        "custom_domain": None,
    }
    if cfg["endpoint_url"]:
        options["endpoint_url"] = cfg["endpoint_url"]
    if cfg["region_name"]:
        options["region_name"] = cfg["region_name"]
    if cfg["addressing_style"]:
        options["addressing_style"] = cfg["addressing_style"]
    return options


def resolve_default_storage(*, environ: dict) -> dict:
    """STORAGES['default']. FileSystemStorage se S3 não foi declarado."""
    cfg = s3_config_from_environ(environ)
    if cfg["complete"]:
        return {"BACKEND": S3_BACKEND, "OPTIONS": _s3_options(cfg)}
    if cfg["declared"]:
        raise ImproperlyConfigured(
            "Object storage incompleto. Defina AWS_STORAGE_BUCKET_NAME, "
            "AWS_ACCESS_KEY_ID e AWS_SECRET_ACCESS_KEY."
        )
    backend = str(environ.get("DJANGO_DEFAULT_FILE_STORAGE") or "").strip() or (
        FILESYSTEM_BACKEND
    )
    if "storages.backends.s3" in backend:
        raise ImproperlyConfigured(
            "DJANGO_DEFAULT_FILE_STORAGE aponta para S3 sem configuração completa."
        )
    return {"BACKEND": backend}
