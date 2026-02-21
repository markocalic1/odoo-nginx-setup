from __future__ import annotations

import configparser
import glob
import os
import re
from dataclasses import dataclass


@dataclass
class OdooRuntime:
    service_name: str | None
    service_file: str | None
    config_file: str
    http_port: int
    longpolling_port: int


def find_services() -> list[str]:
    services = []
    for root in ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system"):
        if not os.path.isdir(root):
            continue
        for path in glob.glob(os.path.join(root, "odoo*.service")):
            services.append(os.path.basename(path).replace(".service", ""))
    return sorted(set(services))


def find_service_file(service_name: str) -> str:
    for root in ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system"):
        candidate = os.path.join(root, f"{service_name}.service")
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(f"Service file not found for {service_name}")


def detect_config_from_service(service_file: str) -> str | None:
    with open(service_file, "r", encoding="utf-8") as f:
        content = f.read()
    m = re.search(r"-c\s+([^\s]+)", content)
    if not m:
        return None
    cfg = m.group(1).strip().strip('"').strip("'")
    return cfg if os.path.isfile(cfg) else None


def parse_odoo_ports(config_file: str) -> tuple[int, int]:
    parser = configparser.ConfigParser()
    parser.read(config_file)
    section = parser["options"] if "options" in parser else {}

    def _int(name: str, default: int) -> int:
        try:
            return int(str(section.get(name, default)).strip())
        except Exception:
            return default

    http_port = _int("http_port", 8069)
    # Odoo >= 18 commonly uses gevent_port; older uses longpolling_port.
    longpolling = _int("longpolling_port", _int("gevent_port", http_port + 1))
    return http_port, longpolling


def build_runtime(service_name: str | None = None, config_override: str | None = None) -> OdooRuntime:
    service_file: str | None = None
    config_file: str | None = None

    if service_name:
        service_file = find_service_file(service_name)
        config_file = config_override or detect_config_from_service(service_file)
        if not config_file:
            raise FileNotFoundError("Could not detect Odoo config file from service")
    else:
        if not config_override:
            raise FileNotFoundError("Odoo config path is required when no service is provided")
        if not os.path.isfile(config_override):
            raise FileNotFoundError(f"Config file not found: {config_override}")
        config_file = config_override

    http_port, longpolling_port = parse_odoo_ports(config_file)
    return OdooRuntime(
        service_name=service_name,
        service_file=service_file,
        config_file=config_file,
        http_port=http_port,
        longpolling_port=longpolling_port,
    )
