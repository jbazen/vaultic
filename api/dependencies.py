from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from api.auth import decode_token
from api import security_log

_bearer = HTTPBearer(auto_error=False)


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
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
