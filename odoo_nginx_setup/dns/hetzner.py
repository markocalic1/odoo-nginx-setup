from __future__ import annotations

import requests

API = "https://api.hetzner.cloud/v1"


class HetznerDnsClient:
    def __init__(self, token: str):
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _zone_candidates(self, fqdn: str) -> list[str]:
        parts = fqdn.split(".")
        return [".".join(parts[i:]) for i in range(len(parts) - 1)]

    def find_zone(self, fqdn: str) -> tuple[str, str]:
        for zone_name in self._zone_candidates(fqdn):
            r = requests.get(f"{API}/zones", headers=self.headers, params={"name": zone_name}, timeout=20)
            r.raise_for_status()
            zones = r.json().get("zones", [])
            if zones:
                return zone_name, zone_name
        raise RuntimeError(f"Could not find Hetzner DNS zone for {fqdn}")

    def _relative_name(self, fqdn: str, zone_name: str) -> str:
        if fqdn == zone_name:
            return "@"
        suffix = "." + zone_name
        if fqdn.endswith(suffix):
            return fqdn[: -len(suffix)]
        return fqdn

    def upsert_record(self, zone_id: str, zone_name: str, rtype: str, fqdn: str, content: str) -> None:
        name = self._relative_name(fqdn, zone_name)
        # Ensure idempotency by deleting existing RRset before creating a new one.
        requests.delete(f"{API}/zones/{zone_name}/rrsets/{name}/{rtype}", headers=self.headers, timeout=20)
        payload = {"name": name, "type": rtype, "ttl": 120, "records": [{"value": content}]}
        rr = requests.post(f"{API}/zones/{zone_name}/rrsets", headers=self.headers, json=payload, timeout=20)
        rr.raise_for_status()
