"""Tests for scripts/verify_secrets.py (issue #91): every check reports status
only, and no .env value ever reaches stdout, even when a vendor response,
an exception message or litestream output echoes it back."""
import logging
import sqlite3
import subprocess
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from scripts import verify_secrets as vs


def _pem_one_line() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()).decode()
    return pem.replace("\n", "\\n")


@pytest.fixture
def secrets_env(tmp_path):
    fernet_key = Fernet.generate_key().decode()
    vapid_pem = _pem_one_line()
    env = {
        "AUTH_USERNAME": "jbazen",
        "JWT_SECRET": "JWTSECRET-" + "j" * 54,
        "ENCRYPTION_KEY": fernet_key,
        "PLAID_CLIENT_ID": "PLAIDCLIENT-1234567",
        "PLAID_SECRET": "PLAIDSECRET-abcdef123456",
        "PLAID_ENV": "production",
        "FRED_API_KEY": "FREDKEY-0123456789abcdef",
        "COINBASE_API_KEY_NAME": "organizations/org-1/apiKeys/key-1",
        "COINBASE_API_KEY_PRIVATE": _pem_one_line(),
        "TAVILY_API_KEY": "tvly-TAVILYSECRET-123",
        "ANTHROPIC_API_KEY": "sk-ant-ANTHROPICSECRET-123",
        "LITESTREAM_ACCESS_KEY_ID": "R2KEYID-0123456789",
        "LITESTREAM_SECRET_ACCESS_KEY": "R2SECRET-0123456789abcdef",
        "VAPID_PRIVATE_KEY_PEM": vapid_pem,
        "VAPID_PUBLIC_KEY": vs._public_key_from_pem(vapid_pem),
        "VAPID_EMAIL": "mailto:admin@example.com",
    }
    db = tmp_path / "vaultic.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE plaid_items (access_token_enc TEXT)")
    conn.execute("CREATE TABLE users (totp_secret TEXT)")
    cipher = Fernet(fernet_key.encode())
    for _ in range(3):
        conn.execute("INSERT INTO plaid_items VALUES (?)", (cipher.encrypt(b"access-sandbox-x").decode(),))
    conn.execute("INSERT INTO users VALUES (?)", (cipher.encrypt(b"TOTPSECRET").decode(),))
    conn.execute("INSERT INTO users VALUES (NULL)")
    conn.commit()
    conn.close()
    return env, str(db)


def _echo_all(env):
    """Hostile mock: every response echoes every secret back in its body."""
    blob = " ".join(env.values())

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/push/vapid-public-key":
            return httpx.Response(200, json={"publicKey": env["VAPID_PUBLIC_KEY"], "echo": blob})
        if path == "/api/v3/brokerage/accounts":
            return httpx.Response(200, json={"accounts": [{}, {}], "echo": blob})
        return httpx.Response(200, json={"echo": blob}, headers={"x-echo": env["PLAID_SECRET"]})
    return handler


def _fake_runner(env, wal_stdout=None, rc=0, journal_stdout=""):
    wal = wal_stdout if wal_stdout is not None else (
        "replica  generation  index  offset  size  created\n"
        "file  abc  1  0  10  2026-11-13T20:01:02Z\n"
        "s3    abc  1  0  10  2026-11-13T20:01:01Z\n"
    )
    snaps = (
        "replica  generation  index  size  created\n"
        "s3    abc  1  10  2026-11-13T19:33:40Z\n"
        "file  abc  1  10  2026-11-13T19:33:40Z\n"
    )
    calls = []

    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        leak = env["LITESTREAM_SECRET_ACCESS_KEY"]
        if cmd[0] == "journalctl":
            return SimpleNamespace(returncode=rc, stdout=journal_stdout, stderr=leak)
        out = wal if cmd[1] == "wal" else snaps
        return SimpleNamespace(returncode=rc, stdout=out, stderr=leak)
    runner.calls = calls
    return runner


def _ctx(env, db, handler, runner=None, **kw):
    return vs.Ctx(
        env=env,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        db_path=db,
        runner=runner or _fake_runner(env),
        sleep=lambda s: None,
        mint=lambda user, hours: "minted-token-for-test",
        **kw,
    )


def _assert_no_secrets(out, env):
    for frag in vs.secret_fragments(env):
        assert frag not in out, "a secret fragment reached stdout"


def test_all_checks_pass_and_print_no_secret(secrets_env, capsys):
    env, db = secrets_env
    rc = vs.run(list(vs.CHECKS), _ctx(env, db, _echo_all(env)))
    out = capsys.readouterr().out
    assert rc == 0
    for line in (
        "HEALTH: HTTP 200",
        "PLAID_SECRET: HTTP 200",
        "FRED_API_KEY: HTTP 200",
        "COINBASE_API_KEY: HTTP 200 (accounts=2)",
        "TAVILY_API_KEY: HTTP 200",
        "ANTHROPIC_API_KEY: HTTP 200",
        "JWT_SECRET: HTTP 200 (/api/auth/me)",
        "VAPID_KEYPAIR: match ok",
        "VAPID_ENDPOINT: HTTP 200 len=87 matches_env=yes",
        "ENCRYPTION_KEY: plaid_items decrypt 3/3, totp_secret decrypt 1/1",
        "LITESTREAM_R2: latest WAL 2026-11-13T20:01:01Z, lag vs local replica 1s, "
        "latest snapshot 2026-11-13T19:33:40Z",
        "LITESTREAM_R2_ERRORS: count=0 since '24 hours ago'",
    ):
        assert line in out
    _assert_no_secrets(out, env)


def test_http_failures_print_status_only(secrets_env, capsys):
    env, db = secrets_env

    def handler(request):
        return httpx.Response(401, text="invalid key " + " ".join(env.values()))
    rc = vs.run(["plaid", "fred", "coinbase", "tavily", "anthropic", "jwt"], _ctx(env, db, handler))
    out = capsys.readouterr().out
    assert rc == 1
    assert out.count("HTTP 401") == 6
    _assert_no_secrets(out, env)


def test_plaid_error_code_enum_only(secrets_env, capsys):
    env, db = secrets_env
    codes = iter(["INVALID_API_KEYS", env["PLAID_SECRET"]])

    def handler(request):
        assert request.url.host == "production.plaid.com"
        return httpx.Response(400, json={"error_code": next(codes)})
    vs.run(["plaid", "plaid"], _ctx(env, db, handler))
    out = capsys.readouterr().out
    assert "PLAID_SECRET: HTTP 400 (INVALID_API_KEYS)" in out
    assert out.count("PLAID_SECRET: HTTP 400\n") == 1  # a non-enum code is dropped
    _assert_no_secrets(out, env)


def test_plaid_unknown_env_fails_without_request(secrets_env, capsys):
    env, db = secrets_env
    env["PLAID_ENV"] = "prod"

    def handler(request):
        raise AssertionError("no request expected")
    assert vs.run(["plaid"], _ctx(env, db, handler)) == 1
    assert "PLAID_SECRET: PLAID_ENV is not sandbox/development/production" in capsys.readouterr().out


def test_exception_reports_class_name_only(secrets_env, capsys):
    env, db = secrets_env

    def handler(request):
        # httpx errors can carry the full URL, query-string API key included
        raise httpx.ConnectError(f"failed {request.url}", request=request)
    rc = vs.run(["fred", "plaid"], _ctx(env, db, handler))
    out = capsys.readouterr().out
    assert rc == 1
    assert "FRED_API_KEY: error ConnectError" in out
    assert "PLAID_SECRET: error ConnectError" in out
    _assert_no_secrets(out, env)


def test_nothing_on_stderr_or_in_logs_with_info_logging(secrets_env, capsys, caplog):
    """httpx logs each request URL at INFO, and FRED's URL carries the API key."""
    env, db = secrets_env
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.INFO, logger="httpx")
    caplog.set_level(logging.INFO, logger="httpcore")
    vs.run(list(vs.CHECKS), _ctx(env, db, _echo_all(env)))
    captured = capsys.readouterr()
    _assert_no_secrets(captured.out, env)
    for stream in (captured.err, caplog.text):
        assert env["FRED_API_KEY"] not in stream
        assert "api.stlouisfed.org" not in stream


def test_fred_key_goes_in_query_not_output(secrets_env, capsys):
    env, db = secrets_env
    seen = []

    def handler(request):
        seen.append(request.url.params.get("series_id"))
        return httpx.Response(200, json={})
    vs.run(["fred"], _ctx(env, db, handler))
    assert seen == ["DGS10"]
    _assert_no_secrets(capsys.readouterr().out, env)


def test_missing_values_reported_by_name(secrets_env, capsys):
    env, db = secrets_env
    env.pop("FRED_API_KEY")
    env.pop("COINBASE_API_KEY_PRIVATE")
    rc = vs.run(["fred", "coinbase"], _ctx(env, db, _echo_all(env)))
    out = capsys.readouterr().out
    assert rc == 1
    assert "FRED_API_KEY: not set (FRED_API_KEY)" in out
    assert "COINBASE_API_KEY: not set (COINBASE_API_KEY_PRIVATE)" in out


def test_vapid_mismatch_and_wrong_served_key(secrets_env, capsys):
    env, db = secrets_env
    env["VAPID_PUBLIC_KEY"] = vs._public_key_from_pem(_pem_one_line())
    rc = vs.run(["vapid"], _ctx(env, db, _echo_all({**env, "VAPID_PUBLIC_KEY": "short"})))
    out = capsys.readouterr().out
    assert rc == 1
    assert "VAPID_KEYPAIR: MISMATCH" in out
    assert "VAPID_ENDPOINT: HTTP 200 len=5 matches_env=no" in out
    _assert_no_secrets(out, env)


def test_encryption_counts_undecryptable_rows(secrets_env, capsys):
    env, db = secrets_env
    conn = sqlite3.connect(db)
    other = Fernet(Fernet.generate_key())
    conn.execute("INSERT INTO plaid_items VALUES (?)", (other.encrypt(b"x").decode(),))
    conn.commit()
    conn.close()
    rc = vs.run(["encryption"], _ctx(env, db, _echo_all(env)))
    out = capsys.readouterr().out
    assert rc == 1
    assert "plaid_items decrypt 3/4" in out
    _assert_no_secrets(out, env)


def test_litestream_lag_and_exit_code(secrets_env, capsys):
    env, db = secrets_env
    stale = (
        "replica  generation  index  offset  size  created\n"
        "file  abc  2  0  10  2026-11-13T20:10:00Z\n"
        "s3    abc  1  0  10  2026-11-13T20:00:00Z\n"
    )
    assert vs.run(["litestream"], _ctx(env, db, _echo_all(env), runner=_fake_runner(env, stale))) == 1
    assert "lag vs local replica 600s" in capsys.readouterr().out
    assert vs.run(["litestream"], _ctx(env, db, _echo_all(env), runner=_fake_runner(env, rc=1))) == 1
    out = capsys.readouterr().out
    assert "LITESTREAM_R2: litestream wal exit 1" in out
    _assert_no_secrets(out, env)


def test_litestream_gets_credentials_via_env_not_argv(secrets_env):
    env, db = secrets_env
    runner = _fake_runner(env)
    vs.run(["litestream"], _ctx(env, db, _echo_all(env), runner=runner))
    for cmd, kwargs in runner.calls:
        assert env["LITESTREAM_SECRET_ACCESS_KEY"] not in " ".join(cmd)
        assert kwargs["env"]["LITESTREAM_SECRET_ACCESS_KEY"] == env["LITESTREAM_SECRET_ACCESS_KEY"]


def test_subprocess_calls_have_a_timeout(secrets_env, capsys):
    env, db = secrets_env
    runner = _fake_runner(env)
    vs.run(["litestream", "journal"], _ctx(env, db, _echo_all(env), runner=runner))
    assert len(runner.calls) == 3
    for _, kwargs in runner.calls:
        assert kwargs["timeout"] == vs.SUBPROCESS_TIMEOUT_SECONDS

    def hung(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"], stderr=env["LITESTREAM_SECRET_ACCESS_KEY"])
    capsys.readouterr()
    assert vs.run(["litestream", "journal"], _ctx(env, db, _echo_all(env), runner=hung)) == 1
    out = capsys.readouterr().out
    assert "LITESTREAM_R2: error TimeoutExpired" in out
    assert "LITESTREAM_R2_ERRORS: error TimeoutExpired" in out
    _assert_no_secrets(out, env)


def test_jwt_mints_with_the_checked_env_value(secrets_env, capsys, monkeypatch):
    """With no injected mint, the real create_token is used, and only when
    api.auth holds the same JWT_SECRET as the .env being checked."""
    import jwt as pyjwt
    from api import auth

    env, db = secrets_env
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"].removeprefix("Bearer "))
        return httpx.Response(200, json={"echo": " ".join(env.values())})

    ctx = _ctx(env, db, handler)
    ctx.mint = None
    assert auth.SECRET_KEY != env["JWT_SECRET"]
    assert vs.run(["jwt"], ctx) == 1
    out = capsys.readouterr().out
    assert "JWT_SECRET: api.auth loaded a different value than the .env being checked" in out
    assert seen == []  # no token was minted or sent
    _assert_no_secrets(out, env)

    monkeypatch.setattr(auth, "SECRET_KEY", env["JWT_SECRET"])
    ctx = _ctx(env, db, handler)
    ctx.mint = None
    assert vs.run(["jwt"], ctx) == 0
    out = capsys.readouterr().out
    assert "JWT_SECRET: HTTP 200 (/api/auth/me)" in out
    assert pyjwt.decode(seen[0], env["JWT_SECRET"], algorithms=["HS256"])["sub"] == "jbazen"
    _assert_no_secrets(out, env)
    assert seen[0] not in out


def test_jwt_secret_missing_is_reported(secrets_env, capsys):
    env, db = secrets_env
    env.pop("JWT_SECRET")
    assert vs.run(["jwt"], _ctx(env, db, _echo_all(env))) == 1
    assert "JWT_SECRET: not set (JWT_SECRET)" in capsys.readouterr().out


def test_every_check_has_a_label():
    assert set(vs.LABELS) == set(vs.CHECKS)


def test_journal_counts_r2_errors(secrets_env, capsys):
    env, db = secrets_env
    journal = (
        "INFO: GET /api/health 200\n"
        "/home/ubuntu/vaultic/data/vaultic.db(s3): monitor error: AccessDenied\n"
        "s3: sync error: InvalidAccessKeyId\n"
        "sync failed for item 2\n"
    )
    rc = vs.run(["journal"], _ctx(env, db, _echo_all(env), runner=_fake_runner(env, journal_stdout=journal)))
    out = capsys.readouterr().out
    assert rc == 1
    assert "LITESTREAM_R2_ERRORS: count=2 since '24 hours ago'" in out


def test_redact_masks_values_and_pem_parts(secrets_env):
    env, _ = secrets_env
    frags = vs.secret_fragments(env)
    pem_body_line = env["VAPID_PRIVATE_KEY_PEM"].split("\\n")[1]
    text = f"a {env['PLAID_SECRET']} b {pem_body_line} c"
    assert vs.redact(text, frags) == f"a {vs.REDACTED} b {vs.REDACTED} c"
    assert "jbazen" not in frags  # short non-secret values are not masked


def test_read_env_parses_like_load_dotenv(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n\nA=1\nPEM=-----BEGIN-----\\nabc\\n-----END-----\\n\n"
        "B = two=parts \nPLAID_ENV=production   # sandbox | development | production\n"
    )
    assert vs.read_env(str(path)) == {
        "A": "1",
        "PEM": "-----BEGIN-----\\nabc\\n-----END-----\\n",
        "B": "two=parts",
        "PLAID_ENV": "production",
    }


def test_main_rejects_unknown_check():
    with pytest.raises(SystemExit) as exc:
        vs.main(["nope"])
    assert exc.value.code == 2
