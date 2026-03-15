import logging
import time
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# load_dotenv() MUST be called before any other import that reads os.environ.
# Several modules (sage.py, routers/sage.py, auth.py) call os.environ.get() at
# module-load time or at the top of functions that run during startup. If
# load_dotenv() were called after those imports, the .env values would already
# be missing and the app would silently fall back to empty strings (no API keys).
# Calling it here — before the rest of the import block — guarantees .env is
# populated before any module-level code that depends on it runs.
load_dotenv()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from api.database import init_db
from api.dependencies import get_current_user, get_client_ip
from api.routers import auth, plaid, accounts, net_worth, manual, sage, pdf
from api import security_log

logging.basicConfig(level=logging.INFO)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    security_log.log_server_event("Vaultic API starting")
    init_db()

    from api.auth import seed_user_from_env
    seed_user_from_env()

    from api.sync import sync_all
    # Sync at 02:00 daily — quiet time, avoids Plaid rate-limit windows during peak hours
    scheduler.add_job(sync_all, "cron", hour=2, minute=0, id="daily_sync")
    scheduler.start()
    security_log.log_server_event("Scheduler started — daily sync at 02:00")

    yield

    scheduler.shutdown()
    security_log.log_server_event("Vaultic API stopped")


app = FastAPI(title="Vaultic API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_and_logging_middleware(request: Request, call_next):
    start = time.monotonic()
    ip = get_client_ip(request)

    # Resolve username from the JWT for richer security logs.
    # We do this best-effort in middleware — if the token is missing, expired, or
    # malformed, we just log "anon" instead of raising an error. The real auth
    # check happens in the route dependency (get_current_user), not here.
    username = "anon"
    try:
        from api.auth import decode_token
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            username = decode_token(auth_header[7:]) or "anon"
    except Exception:
        pass

    response: Response = await call_next(request)
    ms = (time.monotonic() - start) * 1000

    path = request.url.path
    method = request.method

    # Skip logging static/health noise
    if path not in ("/api/health",):
        security_log.log_request(ip, method, path, username, response.status_code, ms)

    # Extra warning for 4xx/5xx
    if response.status_code >= 400:
        security_log.log_server_event(
            f"HTTP_{response.status_code}  ip={ip}  user={username}  {method} {path}"
        )

    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response


app.include_router(auth.router)
app.include_router(plaid.router, dependencies=[Depends(get_current_user)])
app.include_router(accounts.router, dependencies=[Depends(get_current_user)])
app.include_router(net_worth.router, dependencies=[Depends(get_current_user)])
app.include_router(manual.router, dependencies=[Depends(get_current_user)])
app.include_router(sage.router, dependencies=[Depends(get_current_user)])
app.include_router(pdf.router, dependencies=[Depends(get_current_user)])


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "vaultic"}
