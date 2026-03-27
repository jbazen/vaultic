import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_log_path = Path(__file__).parent.parent / "data" / "security.log"
_log_path.parent.mkdir(exist_ok=True)

_logger = logging.getLogger("vaultic.security")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    # 10 MB per file, keep 5 backups (50 MB total max)
    fh = RotatingFileHandler(_log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s"))
    _logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s  %(message)s"))
    _logger.addHandler(sh)


def log_login_attempt(ip: str, username: str, success: bool, user_agent: str = ""):
    status = "SUCCESS" if success else "FAILED"
    _logger.info(f"LOGIN_{status}  ip={ip}  user={username}  ua={user_agent[:80]!r}")


def log_auth_failure(ip: str, path: str, reason: str = ""):
    _logger.info(f"AUTH_FAILURE  ip={ip}  path={path}  reason={reason!r}")


def log_request(ip: str, method: str, path: str, username: str, status_code: int = 0, ms: float = 0):
    _logger.info(f"REQUEST  ip={ip}  user={username}  {method} {path}  status={status_code}  ms={ms:.1f}")


def log_server_event(msg: str):
    _logger.info(f"SERVER  {msg}")


def log_2fa_sent(ip: str, username: str, phone: str):
    masked = phone[:3] + "***" + phone[-4:] if len(phone) > 7 else "***"
    _logger.info(f"2FA_SENT  ip={ip}  user={username}  phone={masked}")


def log_2fa_attempt(ip: str, username: str, success: bool):
    status = "SUCCESS" if success else "FAILED"
    _logger.info(f"2FA_{status}  ip={ip}  user={username}")


def log_sync_event(msg: str):
    _logger.info(f"SYNC  {msg}")


def log_plaid_event(msg: str):
    _logger.info(f"PLAID  {msg}")


def log_sage_query(username: str, preview: str):
    _logger.info(f"SAGE_QUERY  user={username}  msg={preview[:60]!r}")


def log_token_event(ip: str, username: str, event: str):
    _logger.info(f"TOKEN_{event.upper()}  ip={ip}  user={username}")


def tail(lines: int = 500) -> list[str]:
    """Return the last N lines of the security log."""
    if not _log_path.exists():
        return []
    with open(_log_path, encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    return [l.rstrip() for l in all_lines[-lines:]]
