from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def _run(cmd: list[str], sudo: bool = False) -> None:
    if sudo and os.geteuid() != 0:
        cmd = ["sudo"] + cmd
    subprocess.run(cmd, check=True)


def _write(path: str, content: str, sudo: bool = False) -> None:
    if sudo and os.geteuid() != 0:
        p = subprocess.Popen(["sudo", "tee", path], stdin=subprocess.PIPE, text=True)
        p.communicate(content)
        if p.returncode != 0:
            raise RuntimeError(f"Failed writing {path}")
        return
    Path(path).write_text(content, encoding="utf-8")


def _slug(domain: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", domain)


def install_nginx_and_certbot() -> None:
    _run(["apt", "update"], sudo=True)
    _run(["apt", "install", "-y", "nginx", "certbot", "python3-certbot-nginx", "curl", "jq"], sudo=True)
    _run(["systemctl", "enable", "--now", "nginx"], sudo=True)


def render_acme_config(domain: str, webroot: str) -> str:
    return f"""server {{
    listen 80;
    listen [::]:80;
    server_name {domain};

    location /.well-known/acme-challenge/ {{
        root {webroot};
    }}

    location / {{
        return 301 http://$host$request_uri;
    }}
}}
"""


def render_https_config(domain: str, odoo_port: int, longpolling_port: int) -> str:
    up = _slug(domain)
    return f"""upstream {up}_backend {{
    server 127.0.0.1:{odoo_port};
}}

upstream {up}_longpolling {{
    server 127.0.0.1:{longpolling_port};
}}

server {{
    listen 80;
    listen [::]:80;
    server_name {domain};
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name {domain};

    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;

    client_max_body_size 500M;
    proxy_read_timeout 720s;
    proxy_connect_timeout 720s;
    proxy_send_timeout 720s;

    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;

    location / {{
        proxy_pass http://{up}_backend;
        proxy_redirect off;
    }}

    location /longpolling {{
        proxy_pass http://{up}_longpolling;
    }}

    location /websocket {{
        proxy_pass http://{up}_longpolling;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }}

    location ~* /web/static/ {{
        proxy_cache_valid 200 90m;
        expires 864000;
        proxy_pass http://{up}_backend;
    }}

    gzip on;
    gzip_types text/css text/less text/plain text/xml application/xml application/json application/javascript;
}}
"""


def enable_site(domain: str) -> tuple[str, str]:
    available = f"/etc/nginx/sites-available/{domain}"
    enabled = f"/etc/nginx/sites-enabled/{domain}"
    _run(["mkdir", "-p", "/etc/nginx/sites-available", "/etc/nginx/sites-enabled"], sudo=True)
    _run(["ln", "-sf", available, enabled], sudo=True)
    _run(["rm", "-f", "/etc/nginx/sites-enabled/default"], sudo=True)
    return available, enabled


def write_site_config(path: str, content: str) -> None:
    _write(path, content, sudo=True)


def test_and_reload_nginx() -> None:
    _run(["nginx", "-t"], sudo=True)
    _run(["systemctl", "reload", "nginx"], sudo=True)


def certbot_issue_http(domain: str, email: str) -> None:
    _run(
        [
            "certbot",
            "certonly",
            "--nginx",
            "-d",
            domain,
            "--non-interactive",
            "--agree-tos",
            "--keep-until-expiring",
            "-m",
            email,
        ],
        sudo=True,
    )


def certbot_issue_hetzner_dns(domain: str, email: str, token: str, wildcard: bool = False) -> None:
    if not token:
        raise RuntimeError("HETZNER_DNS_API_TOKEN is required for Hetzner DNS challenge")

    safe = _slug(domain)
    token_path = f"/tmp/odoo_nginx_setup_hetzner_token_{safe}"
    auth_path = f"/tmp/odoo_nginx_setup_hetzner_auth_{safe}.sh"
    cleanup_path = f"/tmp/odoo_nginx_setup_hetzner_cleanup_{safe}.sh"

    _write(token_path, token + "\n")
    os.chmod(token_path, 0o600)

    auth_script = f"""#!/bin/bash
set -euo pipefail

token="$(cat {token_path})"
api="https://dns.hetzner.com/api/v1"
fqdn="$CERTBOT_DOMAIN"

find_zone() {{
  local candidate="$fqdn"
  while true; do
    local resp
    resp="$(curl -sS -H "Auth-API-Token: $token" "$api/zones?name=$candidate")"
    local zid
    zid="$(echo "$resp" | jq -r '.zones[0].id // empty')"
    if [ -n "$zid" ]; then
      echo "$zid|$candidate"
      return
    fi
    if [[ "$candidate" != *.* ]]; then
      echo "Could not determine zone for $fqdn" >&2
      exit 1
    fi
    candidate="${{candidate#*.}}"
  done
}}

zone_info="$(find_zone)"
zone_id="${{zone_info%%|*}}"
zone_name="${{zone_info##*|}}"

record_name="_acme-challenge"
if [ "$fqdn" != "$zone_name" ]; then
  host_part="${{fqdn%.$zone_name}}"
  record_name="_acme-challenge.$host_part"
fi

payload="$(jq -cn --arg zid "$zone_id" --arg name "$record_name" --arg val "$CERTBOT_VALIDATION" '{{zone_id:$zid, type:"TXT", name:$name, value:$val, ttl:120}}')"
curl -sS -X POST "$api/records" -H "Auth-API-Token: $token" -H "Content-Type: application/json" -d "$payload" >/dev/null
sleep 20
"""

    cleanup_script = f"""#!/bin/bash
set -euo pipefail

token="$(cat {token_path})"
api="https://dns.hetzner.com/api/v1"
fqdn="$CERTBOT_DOMAIN"

find_zone() {{
  local candidate="$fqdn"
  while true; do
    local resp
    resp="$(curl -sS -H "Auth-API-Token: $token" "$api/zones?name=$candidate")"
    local zid
    zid="$(echo "$resp" | jq -r '.zones[0].id // empty')"
    if [ -n "$zid" ]; then
      echo "$zid|$candidate"
      return
    fi
    if [[ "$candidate" != *.* ]]; then
      exit 0
    fi
    candidate="${{candidate#*.}}"
  done
}}

zone_info="$(find_zone)"
zone_id="${{zone_info%%|*}}"
zone_name="${{zone_info##*|}}"

record_name="_acme-challenge"
if [ "$fqdn" != "$zone_name" ]; then
  host_part="${{fqdn%.$zone_name}}"
  record_name="_acme-challenge.$host_part"
fi

records="$(curl -sS -H "Auth-API-Token: $token" "$api/records?zone_id=$zone_id")"
echo "$records" | jq -r --arg n "$record_name" --arg v "$CERTBOT_VALIDATION" '.records[] | select(.type=="TXT" and .name==$n and .value==$v) | .id' | while read -r rid; do
  [ -n "$rid" ] || continue
  curl -sS -X DELETE "$api/records/$rid" -H "Auth-API-Token: $token" >/dev/null
done
"""

    _write(auth_path, auth_script)
    _write(cleanup_path, cleanup_script)
    os.chmod(auth_path, 0o700)
    os.chmod(cleanup_path, 0o700)

    cmd = [
        "certbot",
        "certonly",
        "--manual",
        "--preferred-challenges",
        "dns",
        "--manual-auth-hook",
        auth_path,
        "--manual-cleanup-hook",
        cleanup_path,
        "--non-interactive",
        "--agree-tos",
        "--keep-until-expiring",
        "-m",
        email,
        "-d",
        domain,
    ]
    if wildcard:
        cmd.extend(["-d", f"*.{domain}"])
    _run(cmd, sudo=True)
