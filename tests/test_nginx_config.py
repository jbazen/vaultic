"""Tests for deploy/vaultic-nginx.conf and how the deploy installs it (#155).

nginx and the app must count the same client. Behind Cloudflare that is the
visitor's address from CF-Connecting-IP, trusted only on connections from
Cloudflare's ranges. The static tests check the config text on every platform.
The live tests start a real nginx (non-root, on 127.0.0.1) with the repo config
and an echo upstream; they run wherever nginx is installed, which includes CI.
"""
import datetime
import http.server
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parent.parent
NGINX_CONF = REPO / "deploy" / "vaultic-nginx.conf"
DEPLOY_YML = REPO / ".github" / "workflows" / "deploy.yml"

# https://www.cloudflare.com/ips-v4 and /ips-v6, checked 2026-11-15
CLOUDFLARE_RANGES = {
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
}


def _conf_text() -> str:
    return NGINX_CONF.read_text(encoding="utf-8")


def _directives(text: str) -> list[str]:
    """Config lines with comments and blank lines removed."""
    lines = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def _proxy_locations(text: str) -> dict[str, str]:
    """Map each location that has proxy_pass to its body (comments removed)."""
    blocks = {}
    stripped = "\n".join(_directives(text))
    for m in re.finditer(r"location\s+([^{]+?)\s*\{(.*?)\}", stripped, re.S):
        if "proxy_pass" in m.group(2):
            blocks[m.group(1).strip()] = m.group(2)
    return blocks


# ── Static checks ────────────────────────────────────────────────────────────

class TestNginxConfigText:
    def test_real_ip_comes_from_cf_connecting_ip(self):
        assert "real_ip_header CF-Connecting-IP;" in _directives(_conf_text())

    def test_real_ip_is_not_recursive(self):
        # Recursive mode would walk a client-supplied header chain.
        assert not any(d.startswith("real_ip_recursive") for d in _directives(_conf_text()))

    def test_trusted_ranges_are_exactly_cloudflares(self):
        ranges = [
            d.split()[1].rstrip(";")
            for d in _directives(_conf_text())
            if d.startswith("set_real_ip_from")
        ]
        assert len(ranges) == len(set(ranges))
        # Adding or dropping a range must be a deliberate change in both places.
        assert set(ranges) == CLOUDFLARE_RANGES
        nets = [ipaddress.ip_network(r) for r in ranges]
        # The Cloudflare edges seen in prod's security log must be covered.
        for edge in ("104.23.195.68", "172.69.74.196"):
            assert any(ipaddress.ip_address(edge) in n for n in nets), edge

    def test_limit_zones_key_on_real_client_ip(self):
        zones = [d for d in _directives(_conf_text()) if d.startswith("limit_req_zone")]
        assert {re.search(r"zone=(\w+):", z).group(1) for z in zones} == {"login", "api"}
        for zone in zones:
            assert zone.split()[1] == "$binary_remote_addr", zone

    def test_no_directive_reads_cf_header_directly(self):
        # The header is only trusted through real_ip; reading it elsewhere
        # would let a direct origin request choose its own address.
        for d in _directives(_conf_text()):
            assert "$http_cf_connecting_ip" not in d, d

    def test_rejections_return_429(self):
        assert "limit_req_status 429;" in _directives(_conf_text())

    def test_every_proxy_location_forwards_remote_addr(self):
        blocks = _proxy_locations(_conf_text())
        assert set(blocks) == {"/api/auth/login", "/api/auth/verify-2fa", "/api/"}
        for name, body in blocks.items():
            assert re.search(r"proxy_set_header\s+X-Forwarded-For\s+\$remote_addr;", body), name

    def test_every_proxy_location_is_rate_limited(self):
        blocks = _proxy_locations(_conf_text())
        assert "zone=login" in blocks["/api/auth/login"]
        assert "zone=login" in blocks["/api/auth/verify-2fa"]
        assert "zone=api" in blocks["/api/"]

    def test_api_timeout_matches_previous_prod_config(self):
        body = _proxy_locations(_conf_text())["/api/"]
        assert "proxy_read_timeout 180s;" in body
        assert "proxy_send_timeout 180s;" in body


class TestDeployInstallsConfig:
    def _script(self) -> str:
        return DEPLOY_YML.read_text(encoding="utf-8")

    # String checks: they catch the steps being dropped or reordered, not a
    # wrong branch. The steps themselves run on every deploy.

    def test_sites_enabled_is_symlinked_to_installed_copy(self):
        script = self._script()
        assert "NGINX_SITE=/etc/nginx/sites-available/vaultic" in script
        assert "NGINX_ENABLED=/etc/nginx/sites-enabled/vaultic" in script
        assert 'sudo cp deploy/vaultic-nginx.conf "$NGINX_SITE"' in script
        assert 'sudo ln -sfn "$NGINX_SITE" "$NGINX_ENABLED"' in script

    def test_previous_config_is_kept_outside_sites_enabled(self):
        script = self._script()
        backup = script.index('sudo cp "$NGINX_ENABLED" "$NGINX_SITE.prev"')
        install = script.index("sudo cp deploy/vaultic-nginx.conf")
        restore = script.index('sudo cp "$NGINX_SITE.prev" "$NGINX_SITE"')
        assert backup < install < restore
        assert 'if [ -e "$NGINX_ENABLED" ]; then' in script
        assert "if sudo nginx -t; then" in script

    def test_nginx_failure_fails_deploy_after_health_check(self):
        script = self._script()
        flagged = script.index("NGINX_FAILED=1")
        health = script.index('if [ "$HEALTHY" -ne 1 ]; then')
        fail = script.index('if [ "$NGINX_FAILED" -eq 1 ]; then')
        assert flagged < health < fail


# ── Live checks against a real nginx ─────────────────────────────────────────

NGINX = shutil.which("nginx") or next(
    (p for p in ("/usr/sbin/nginx", "/usr/local/sbin/nginx") if os.path.exists(p)), None
)


def _require_nginx():
    if NGINX is None:
        if os.environ.get("CI"):
            pytest.fail("nginx must be installed in CI so the config is tested for real")
        pytest.skip("nginx not installed")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _write_self_signed_cert(cert_path: Path, key_path: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "vaulticsage.com")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


class _EchoHandler(http.server.BaseHTTPRequestHandler):
    """Upstream stand-in: replies with the X-Forwarded-For nginx sent."""

    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = (self.headers.get("X-Forwarded-For") or "").encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _reply
    do_POST = _reply

    def log_message(self, *args):
        pass


def _replace_all(text: str, old: str, new: str, count: int = 1) -> str:
    assert text.count(old) == count, f"expected {count} of {old!r} in the config"
    return text.replace(old, new)


class _Nginx:
    """Runs the repo config on loopback ports, pointed at an echo upstream."""

    def __init__(self, workdir: Path, trust_loopback_as_cloudflare: bool):
        self.dir = workdir
        self.https_port = _free_port()
        self.upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _EchoHandler)
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()

        cert, key = workdir / "cert.pem", workdir / "key.pem"
        _write_self_signed_cert(cert, key)
        (workdir / "dist").mkdir()

        site = _conf_text()
        site = _replace_all(site, "listen 80;", f"listen 127.0.0.1:{_free_port()};")
        site = _replace_all(site, "listen 443 ssl;", f"listen 127.0.0.1:{self.https_port} ssl;")
        site = _replace_all(site, "/etc/ssl/cloudflare/cert.pem", cert.as_posix())
        site = _replace_all(site, "/etc/ssl/cloudflare/key.pem", key.as_posix())
        site = _replace_all(site, "/home/ubuntu/vaultic/ui/dist", (workdir / "dist").as_posix())
        site = _replace_all(
            site,
            "http://127.0.0.1:8000",
            f"http://127.0.0.1:{self.upstream.server_address[1]}",
            count=3,
        )
        if trust_loopback_as_cloudflare:
            # Stand in for a Cloudflare edge: the test client connects from loopback.
            site = _replace_all(
                site,
                "real_ip_header CF-Connecting-IP;",
                "real_ip_header CF-Connecting-IP;\nset_real_ip_from 127.0.0.1;",
            )
        (workdir / "site.conf").write_text(site, encoding="utf-8")

        d = workdir.as_posix()
        (workdir / "nginx.conf").write_text(
            f"""
pid {d}/nginx.pid;
error_log {d}/error.log;
events {{ worker_connections 64; }}
http {{
    access_log off;
    client_body_temp_path {d}/client_body;
    proxy_temp_path {d}/proxy;
    fastcgi_temp_path {d}/fastcgi;
    uwsgi_temp_path {d}/uwsgi;
    scgi_temp_path {d}/scgi;
    include {d}/site.conf;
}}
""",
            encoding="utf-8",
        )
        self.cmd = [NGINX, "-p", d, "-c", f"{d}/nginx.conf", "-e", f"{d}/error.log"]
        self.proc = None

    def config_test(self) -> subprocess.CompletedProcess:
        return subprocess.run(self.cmd + ["-t"], capture_output=True, text=True)

    def start(self):
        self.proc = subprocess.Popen(
            self.cmd + ["-g", "daemon off;"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.https_port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        self.stop()
        raise RuntimeError((self.dir / "error.log").read_text(errors="replace"))

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        self.upstream.shutdown()
        self.upstream.server_close()

    def request(self, method: str, path: str, headers: dict | None = None) -> httpx.Response:
        with httpx.Client(verify=False, timeout=5) as client:
            return client.request(
                method,
                f"https://127.0.0.1:{self.https_port}{path}",
                headers={"Host": "vaulticsage.com", **(headers or {})},
            )


@pytest.fixture
def nginx_factory(tmp_path):
    _require_nginx()
    started = []

    def make(trust_loopback_as_cloudflare: bool) -> _Nginx:
        d = tmp_path / f"n{len(started)}"
        d.mkdir()
        server = _Nginx(d, trust_loopback_as_cloudflare)
        started.append(server)
        return server

    yield make
    for server in started:
        server.stop()


class TestNginxLive:
    def test_repo_config_passes_nginx_test(self, nginx_factory):
        server = nginx_factory(trust_loopback_as_cloudflare=False)
        result = server.config_test()
        assert result.returncode == 0, result.stderr

    def test_cloudflare_connection_forwards_visitor_ip(self, nginx_factory):
        server = nginx_factory(trust_loopback_as_cloudflare=True)
        server.start()
        r = server.request(
            "GET",
            "/api/health",
            {"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "6.6.6.6"},
        )
        assert r.status_code == 200
        # The app sees only the visitor's address; the client-sent XFF is dropped.
        assert r.text == "203.0.113.7"

    def test_cloudflare_connection_forwards_ipv6_visitor(self, nginx_factory):
        server = nginx_factory(trust_loopback_as_cloudflare=True)
        server.start()
        r = server.request("GET", "/api/health", {"CF-Connecting-IP": "2001:db8::1"})
        assert r.status_code == 200
        assert r.text == "2001:db8::1"

    def test_direct_connection_ignores_forged_header(self, nginx_factory):
        server = nginx_factory(trust_loopback_as_cloudflare=False)
        server.start()
        r = server.request("GET", "/api/health", {"CF-Connecting-IP": "203.0.113.7"})
        assert r.status_code == 200
        assert r.text == "127.0.0.1"

    def test_login_limit_is_per_visitor(self, nginx_factory):
        server = nginx_factory(trust_loopback_as_cloudflare=True)
        server.start()
        visitor_a = {"CF-Connecting-IP": "203.0.113.7"}
        # rate=5r/m, burst=3, nodelay: 1 + 3 pass at once, the next is rejected.
        codes = [server.request("POST", "/api/auth/login", visitor_a).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 200, 429]
        # verify-2fa shares the login zone, so visitor A stays blocked there too.
        assert server.request("POST", "/api/auth/verify-2fa", visitor_a).status_code == 429
        # Another visitor behind the same Cloudflare edge is not affected.
        visitor_b = {"CF-Connecting-IP": "198.51.100.9"}
        assert server.request("POST", "/api/auth/login", visitor_b).status_code == 200

    def test_login_limit_applies_without_cf_header(self, nginx_factory):
        server = nginx_factory(trust_loopback_as_cloudflare=False)
        server.start()
        codes = [server.request("POST", "/api/auth/login").status_code for _ in range(5)]
        assert codes == [200, 200, 200, 200, 429]
