# odoo-nginx-setup

Interactive CLI to configure Nginx + Let's Encrypt for an Odoo instance, with optional DNS record creation in Cloudflare or Hetzner DNS.

## Features
- Detect Odoo systemd service and Odoo config path
- Supports Docker/non-systemd mode via direct `--config`
- Supports odoo-deploy profile config (`--odoo-deploy-config`) to auto-resolve `docker/etc/odoo.conf`
- Detect Odoo `http_port` and `longpolling_port` / `gevent_port`
- Supports Docker single-port mode (`--single-upstream`) for setups where only one Odoo port is exposed
- Supports Let's Encrypt HTTP-01 and Hetzner DNS-01 challenge modes
- Supports wildcard certificates (`*.example.com`) with Hetzner DNS-01
- Interactive wizard for domain/email/provider
- Optional DNS record creation:
  - Cloudflare (`CLOUDFLARE_API_TOKEN`)
  - Hetzner DNS (`HETZNER_DNS_API_TOKEN`)
- Two-phase Nginx setup:
  - HTTP ACME config
  - HTTPS reverse proxy config
- Certbot certificate issuance
- Enables certbot auto-renewal (`certbot.timer`) with nginx reload deploy hook
- Ensures `proxy_mode = True` in Odoo config
- Optional UFW hardening

## Install
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage
```bash
odoo-nginx-setup init
```

Systemd service example:
```bash
odoo-nginx-setup init \
  --service odoo19 \
  --domain erp.example.com \
  --email admin@example.com \
  --provider cloudflare \
  --ip-mode dual \
  --restart-service \
  --ufw
```

Docker/non-systemd example:
```bash
odoo-nginx-setup init \
  --config /etc/odoo/odoo.conf \
  --domain erp.example.com \
  --email admin@example.com \
  --provider none \
  --ip-mode ipv4 \
  --no-restart-service
```

odoo-deploy profile example:
```bash
odoo-nginx-setup init \
  --odoo-deploy-config /home/calic/profiles/mm19-dev/config.yaml \
  --domain erp.example.com \
  --email admin@example.com \
  --provider none \
  --ip-mode ipv4 \
  --single-upstream \
  --no-restart-service
```

Wildcard certificate example (Hetzner DNS):
```bash
export HETZNER_DNS_API_TOKEN=...
odoo-nginx-setup init \
  --config /home/calic/odoo_deploy_data/mm19-dev/docker/etc/odoo.conf \
  --domain example.com \
  --email admin@example.com \
  --provider hetzner \
  --ip-mode ipv4 \
  --tls-challenge dns \
  --wildcard \
  --no-restart-service
```

This configures persistent Hetzner DNS auth/cleanup hooks under `/etc/letsencrypt/odoo-nginx-setup/`
so `certbot renew` can run unattended.

## DNS Tokens
Cloudflare:
```bash
export CLOUDFLARE_API_TOKEN=...
```

Hetzner DNS:
```bash
export HETZNER_DNS_API_TOKEN=...
```

## Notes
- Run on Ubuntu/Debian server.
- Requires `nginx` and `certbot` packages (the tool can install them).
- API token must have rights for DNS zone edits.
- If no systemd Odoo service exists, provide `--config`.

## Quickstart (Recommended)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip setuptools wheel
pip install -e .
odoo-nginx-setup --help
```
