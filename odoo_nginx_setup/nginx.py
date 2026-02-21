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


def render_https_config(
    domain: str,
    odoo_port: int,
    longpolling_port: int,
    single_upstream: bool = False,
    backend_host: str = "127.0.0.1",
) -> str:
    up = _slug(domain)
    lp_upstream = f"{up}_backend" if single_upstream else f"{up}_longpolling"
    longpolling_block = ""
    if not single_upstream:
        longpolling_block = f"""
upstream {up}_longpolling {{
    server {backend_host}:{longpolling_port};
}}
"""
    return f"""upstream {up}_backend {{
    server {backend_host}:{odoo_port};
}}
{longpolling_block}

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
        proxy_pass http://{lp_upstream};
    }}

    location /websocket {{
        proxy_pass http://{lp_upstream};
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
    base_dir = "/etc/letsencrypt/odoo-nginx-setup"
    token_path = f"{base_dir}/hetzner-token-{safe}"
    auth_path = f"{base_dir}/hetzner-auth-{safe}.sh"
    cleanup_path = f"{base_dir}/hetzner-cleanup-{safe}.sh"
    _run(["mkdir", "-p", base_dir], sudo=True)

    _write(token_path, token + "\n", sudo=True)
    _run(["chmod", "600", token_path], sudo=True)

    auth_script = f"""#!/bin/bash
set -euo pipefail

token="$(cat {token_path})"
api="https://api.hetzner.cloud/v1"
fqdn="$CERTBOT_DOMAIN"

find_zone() {{
  local candidate="$fqdn"
  while true; do
    local resp
    resp="$(curl -sS -H "Authorization: Bearer $token" "$api/zones?name=$candidate")"
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
zone_name="${{zone_info##*|}}"

record_name="_acme-challenge"
if [ "$fqdn" != "$zone_name" ]; then
  host_part="${{fqdn%.$zone_name}}"
  record_name="_acme-challenge.$host_part"
fi

payload="$(jq -cn --arg name "$record_name" --arg val "$CERTBOT_VALIDATION" '{{name:$name, type:"TXT", ttl:300, records:[{{value:("\\\"" + $val + "\\\"")}}]}}')"
curl -sS -X POST "$api/zones/$zone_name/rrsets" -H "Authorization: Bearer $token" -H "Content-Type: application/json" -d "$payload" >/dev/null
sleep 20
"""

    cleanup_script = f"""#!/bin/bash
set -euo pipefail

token="$(cat {token_path})"
api="https://api.hetzner.cloud/v1"
fqdn="$CERTBOT_DOMAIN"

find_zone() {{
  local candidate="$fqdn"
  while true; do
    local resp
    resp="$(curl -sS -H "Authorization: Bearer $token" "$api/zones?name=$candidate")"
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
zone_name="${{zone_info##*|}}"

record_name="_acme-challenge"
if [ "$fqdn" != "$zone_name" ]; then
  host_part="${{fqdn%.$zone_name}}"
  record_name="_acme-challenge.$host_part"
fi

curl -sS -X DELETE "$api/zones/$zone_name/rrsets/$record_name/TXT" -H "Authorization: Bearer $token" >/dev/null
"""

    _write(auth_path, auth_script, sudo=True)
    _write(cleanup_path, cleanup_script, sudo=True)
    _run(["chmod", "700", auth_path, cleanup_path], sudo=True)

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


def ensure_certbot_auto_renewal() -> None:
    deploy_hook_dir = "/etc/letsencrypt/renewal-hooks/deploy"
    deploy_hook_path = f"{deploy_hook_dir}/odoo-nginx-setup-reload-nginx.sh"
    hook = """#!/bin/bash
set -euo pipefail
systemctl reload nginx
"""
    _run(["mkdir", "-p", deploy_hook_dir], sudo=True)
    _write(deploy_hook_path, hook, sudo=True)
    _run(["chmod", "755", deploy_hook_path], sudo=True)
    _run(["systemctl", "enable", "--now", "certbot.timer"], sudo=True)
