"""
Internal API security for FYPilot AI service.

SEC-1:
Authenticate the .NET -> FastAPI channel using X-Internal-Api-Key.

All AI endpoints require the shared internal key.
Health endpoints are allowed without the key so deployment checks can still work.
"""

import hmac
import os

from fastapi import Header, HTTPException, Request, status


EXEMPT_PATHS = {
    "/health",
    "/ds/health",
    "/health/live",
    "/health/ready",
}


async def verify_api_key(
    request: Request,
    x_internal_api_key: str | None = Header(
        default=None,
        alias="X-Internal-Api-Key",
    ),
):
    path = request.url.path

    if path in EXEMPT_PATHS:
        return

    expected_key = os.getenv("AI_SERVICE_API_KEY")

    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AI_SERVICE_API_KEY is not configured on the FastAPI service.",
        )

    if not x_internal_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Internal-Api-Key header.",
        )

    if not hmac.compare_digest(x_internal_api_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Internal-Api-Key header.",
        )