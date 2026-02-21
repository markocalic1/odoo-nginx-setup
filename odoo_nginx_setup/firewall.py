from __future__ import annotations

import os
import subprocess


def _run(cmd: list[str]) -> None:
    if os.geteuid() != 0:
        cmd = ["sudo"] + cmd
    subprocess.run(cmd, check=True)


def configure_ufw(allow_odoo: bool, odoo_port: int, allow_longpolling: bool, longpolling_port: int) -> None:
    _run(["apt", "install", "-y", "ufw"])
    _run(["ufw", "allow", "22/tcp"])
    _run(["ufw", "allow", "Nginx Full"])

    if allow_odoo:
        _run(["ufw", "allow", f"{odoo_port}/tcp"])
    if allow_longpolling:
        _run(["ufw", "allow", f"{longpolling_port}/tcp"])

    _run(["ufw", "--force", "enable"])
