from __future__ import annotations

import argparse
import os
import socket
import urllib.request
from pathlib import Path

import yaml

from odoo_nginx_setup.detect import build_runtime, find_services
from odoo_nginx_setup.dns.cloudflare import CloudflareClient
from odoo_nginx_setup.dns.hetzner import HetznerDnsClient
from odoo_nginx_setup.firewall import configure_ufw
from odoo_nginx_setup.nginx import (
    certbot_issue_hetzner_dns,
    certbot_issue_http,
    enable_site,
    ensure_certbot_auto_renewal,
    install_nginx_and_certbot,
    render_acme_config,
    render_https_config,
    test_and_reload_nginx,
    write_site_config,
)
from odoo_nginx_setup.systemd import ensure_proxy_mode, restart_service


def _public_ip(ipv6: bool = False) -> str | None:
    try:
        url = "https://api64.ipify.org" if ipv6 else "https://api.ipify.org"
        with urllib.request.urlopen(url, timeout=10) as r:
            v = r.read().decode().strip()
        if not v:
            return None
        if ipv6 and ":" not in v:
            return None
        if not ipv6 and "." not in v:
            return None
        return v
    except Exception:
        return None


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    v = input(f"{prompt}{suffix}: ").strip()
    return v or (default or "")


def _pick_service(cli_service: str | None) -> str | None:
    if cli_service:
        return cli_service
    services = find_services()
    if not services:
        return None
    print("Detected services:")
    for idx, svc in enumerate(services, start=1):
        print(f"  {idx}. {svc}")
    choice = _ask("Choose service number", "1")
    try:
        i = int(choice)
        if 1 <= i <= len(services):
            return services[i - 1]
    except Exception:
        pass
    return services[0]


def _dns_setup(provider: str, domain: str, ip_mode: str) -> None:
    ipv4 = _public_ip(ipv6=False)
    ipv6 = _public_ip(ipv6=True)

    want_v4 = ip_mode in ("ipv4", "dual")
    want_v6 = ip_mode in ("ipv6", "dual")

    if provider == "none":
        print("Manual DNS mode selected.")
        if want_v4:
            print(f"Create A record: {domain} -> {ipv4 or '<server-ipv4>'}")
        if want_v6:
            print(f"Create AAAA record: {domain} -> {ipv6 or '<server-ipv6>'}")
        input("Press ENTER after DNS is configured...")
        return

    if provider == "cloudflare":
        token = os.getenv("CLOUDFLARE_API_TOKEN", "")
        if not token:
            raise RuntimeError("CLOUDFLARE_API_TOKEN is not set")
        cf = CloudflareClient(token)
        zone_id = cf.find_zone_id(domain)
        if want_v4 and ipv4:
            cf.upsert_record(zone_id, "A", domain, ipv4, proxied=False)
        if want_v6 and ipv6:
            cf.upsert_record(zone_id, "AAAA", domain, ipv6, proxied=False)
        print("Cloudflare DNS records updated.")
        return

    if provider == "hetzner":
        token = os.getenv("HETZNER_DNS_API_TOKEN", "")
        if not token:
            raise RuntimeError("HETZNER_DNS_API_TOKEN is not set")
        hz = HetznerDnsClient(token)
        zone_id, zone_name = hz.find_zone(domain)
        if want_v4 and ipv4:
            hz.upsert_record(zone_id, zone_name, "A", domain, ipv4)
        if want_v6 and ipv6:
            hz.upsert_record(zone_id, zone_name, "AAAA", domain, ipv6)
        print("Hetzner DNS records updated.")
        return

    raise RuntimeError(f"Unknown DNS provider: {provider}")


def _resolve_odoo_deploy_config(profile_config_path: str) -> str:
    config_path = Path(profile_config_path).expanduser()
    if not config_path.is_file():
        raise FileNotFoundError(f"Odoo-deploy profile config not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        conf = yaml.safe_load(f) or {}

    profile_name = conf.get("profile_name")
    if not profile_name:
        raise RuntimeError(f"Missing profile_name in {config_path}")

    build_dir = conf.get("build_dir", f"~/odoo_deploy_data/{profile_name}")
    odoo_conf = Path(build_dir).expanduser() / "docker" / "etc" / "odoo.conf"
    if not odoo_conf.is_file():
        raise FileNotFoundError(
            f"Resolved odoo.conf not found: {odoo_conf}. "
            "Generate it first with `odoodeploy odoo-build`."
        )
    return str(odoo_conf)


def cmd_init(args: argparse.Namespace) -> None:
    if args.wildcard and args.tls_challenge != "dns":
        raise RuntimeError("--wildcard requires --tls-challenge dns")

    service = _pick_service(args.service)
    config_path = args.config
    if not config_path and args.odoo_deploy_config:
        config_path = _resolve_odoo_deploy_config(args.odoo_deploy_config)

    if not service and not config_path:
        config_path = _ask("No Odoo services auto-detected. Enter path to Odoo config file")

    runtime = build_runtime(service, config_path)

    domain = args.domain or _ask("Domain for this Odoo instance")
    if not domain:
        raise RuntimeError("Domain is required")

    default_email = f"admin@{domain}"
    email = args.email or _ask("Email for Let's Encrypt", default_email)

    provider = args.provider
    if not provider:
        provider = _ask("DNS provider (none/cloudflare/hetzner)", "none").lower()
    ip_mode = args.ip_mode or _ask("IP mode (ipv4/ipv6/dual)", "ipv4").lower()

    print("Installing nginx/certbot...")
    install_nginx_and_certbot()

    print("Configuring DNS...")
    _dns_setup(provider, domain, ip_mode)

    webroot = f"/var/www/{domain}"
    os.makedirs(webroot, exist_ok=True)

    site_path, _ = enable_site(domain)

    if args.tls_challenge == "http":
        print("Writing temporary ACME nginx config...")
        write_site_config(site_path, render_acme_config(domain, webroot))
        test_and_reload_nginx()

        print("Issuing certificate with HTTP challenge...")
        certbot_issue_http(domain, email)
    else:
        if provider != "hetzner":
            raise RuntimeError("DNS challenge automation currently requires --provider hetzner")
        token = os.getenv("HETZNER_DNS_API_TOKEN", "")
        if not token:
            raise RuntimeError("HETZNER_DNS_API_TOKEN is not set")
        print("Issuing certificate with Hetzner DNS challenge...")
        certbot_issue_hetzner_dns(domain, email, token, wildcard=args.wildcard)

    print("Writing final HTTPS nginx config...")
    write_site_config(
        site_path,
        render_https_config(
            domain,
            runtime.http_port,
            runtime.longpolling_port,
            single_upstream=args.single_upstream,
            backend_host=args.backend_host,
        ),
    )
    test_and_reload_nginx()
    ensure_certbot_auto_renewal()

    changed = ensure_proxy_mode(runtime.config_file)
    if changed:
        print("Enabled proxy_mode=True in Odoo config.")

    restart = args.restart_service
    if restart is None and runtime.service_name:
        restart = _ask(f"Restart service {runtime.service_name}? (y/N)", "N").lower() == "y"
    if restart and runtime.service_name:
        restart_service(runtime.service_name)
    elif restart and not runtime.service_name:
        print("Skipping service restart: no systemd service configured.")

    if args.ufw:
        allow_odoo = _ask(f"Allow direct Odoo port {runtime.http_port}? (y/N)", "N").lower() == "y"
        allow_lp = _ask(f"Allow direct longpolling/gevent port {runtime.longpolling_port}? (y/N)", "N").lower() == "y"
        configure_ufw(allow_odoo, runtime.http_port, allow_lp, runtime.longpolling_port)

    print("Done.")
    print(f"Domain: https://{domain}")
    print(f"Service: {runtime.service_name or '<none>'}")
    print(f"Config: {runtime.config_file}")
    print(f"Odoo port: {runtime.http_port}")
    print(f"Longpolling/gevent port: {runtime.longpolling_port}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="odoo-nginx-setup")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Interactive setup for one Odoo instance")
    init.add_argument("--service", help="Systemd service name (e.g. odoo19)")
    init.add_argument("--config", help="Path to Odoo config file (required if no service exists)")
    init.add_argument(
        "--odoo-deploy-config",
        help="Path to odoo-deploy profile config.yaml. Auto-resolves docker/etc/odoo.conf",
    )
    init.add_argument("--domain", help="Public domain for the instance")
    init.add_argument("--email", help="Email for Let's Encrypt")
    init.add_argument("--provider", choices=["none", "cloudflare", "hetzner"], help="DNS provider")
    init.add_argument("--ip-mode", choices=["ipv4", "ipv6", "dual"], help="DNS IP mode")
    init.add_argument(
        "--backend-host",
        default="127.0.0.1",
        help="Backend host for Odoo upstreams (default: 127.0.0.1)",
    )
    init.add_argument(
        "--single-upstream",
        action="store_true",
        help="Use same backend upstream for / and /longpolling (useful when Docker exposes one port)",
    )
    init.add_argument(
        "--tls-challenge",
        choices=["http", "dns"],
        default="http",
        help="Let's Encrypt challenge type (dns supports wildcard with Hetzner)",
    )
    init.add_argument("--wildcard", action="store_true", help="Request wildcard certificate (*.domain), requires --tls-challenge dns")
    init.add_argument(
        "--restart-service",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Restart Odoo service at the end (only when a systemd service is used)",
    )
    init.add_argument("--ufw", action="store_true", help="Configure UFW rules")
    init.set_defaults(func=cmd_init)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
