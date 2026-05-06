"""
Service-token auth dependency.

CLAUDE.md s3 says Node is the real auth boundary; this is
defence-in-depth so we don't blindly trust the network. A single shared
``SERVICE_TOKEN`` is checked via :func:`secrets.compare_digest` against
the ``X-Service-Token`` header. Mismatch / missing -> 401.

Apply via :func:`require_service_token` as a FastAPI dependency on every
router *except* ``/health`` (which k8s probes need to call without a
header).
"""

from __future__ import annotations

import secrets

from fastapi import Depends, Header, HTTPException, status

from flashback.http.deps import get_http_config


def require_service_token(
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    cfg=Depends(get_http_config),
) -> None:
    """Validate the ``X-Service-Token`` header.

    Returns ``None`` on success — the dependency is purely a guard;
    handlers don't need the token value.
    """
    if cfg.service_token_auth_disabled:
        return
    if x_service_token is None or not _constant_time_equal(
        x_service_token, cfg.service_token
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid service token",
        )


def require_admin_service_token(
    x_admin_service_token: str | None = Header(
        default=None, alias="X-Admin-Service-Token"
    ),
    cfg=Depends(get_http_config),
) -> None:
    """Validate the admin-only token for privileged operations."""
    if cfg.service_token_auth_disabled:
        return
    if x_admin_service_token is None or not _constant_time_equal(
        x_admin_service_token, cfg.admin_service_token
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid admin service token",
        )


def _constant_time_equal(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
