from __future__ import annotations

import os
import subprocess


def ensure_proxy_mode(config_path: str) -> bool:
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()
    if "proxy_mode = True" in content:
        return False
    if not content.endswith("\n"):
        content += "\n"
    content += "proxy_mode = True\n"
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def restart_service(service_name: str) -> None:
    cmd = ["systemctl", "restart", service_name]
    if os.geteuid() != 0:
        cmd = ["sudo"] + cmd
    subprocess.run(cmd, check=True)
