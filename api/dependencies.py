from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from api.auth import decode_token
from api import security_log

_bearer = HTTPBearer(auto_error=False)


def get_client_ip(request: Request) -> str:
    """Extract real client IP from X-Forwarded-For set by nginx.

    Only the LAST entry (rightmost) is trusted — it's the one added by our
    nginx reverse proxy. Earlier entries can be spoofed by the client.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Rightmost = set by our trusted nginx proxy, not by the client
        parts = [p.strip() for p in forwarded.split(",")]
        return parts[-1] if parts else (request.client.host if request.client else "unknown")
    return request.client.host if request.client else "unknown"


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    ip = get_client_ip(request)
    token = None

    if credentials is None:
        # Try to extract manually (needed because Depends isn't used directly here)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    else:
        token = credentials.credentials

    if not token:
        security_log.log_auth_failure(ip, request.url.path)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    username = decode_token(token)
    if not username:
        security_log.log_auth_failure(ip, request.url.path)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    security_log.log_request(ip, request.method, request.url.path, username)
    return username


async def admin_required(username: str = Depends(get_current_user)) -> str:
    """Require the authenticated user to have is_admin=1."""
    from api.database import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT is_admin FROM users WHERE username = ? AND is_active = 1", (username,)
        ).fetchone()
    if not row or not row["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return username
