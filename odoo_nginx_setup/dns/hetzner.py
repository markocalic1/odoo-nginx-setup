from __future__ import annotations

import requests

API = "https://dns.hetzner.com/api/v1"


class HetznerDnsClient:
    def __init__(self, token: str):
        self.headers = {
            "Auth-API-Token": token,
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
                return zones[0]["id"], zone_name
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
        r = requests.get(f"{API}/records", headers=self.headers, params={"zone_id": zone_id}, timeout=20)
        r.raise_for_status()
        records = r.json().get("records", [])

        matches = [rec for rec in records if rec.get("type") == rtype and rec.get("name") == name]

        payload = {"value": content, "ttl": 120, "type": rtype, "name": name, "zone_id": zone_id}
        if matches:
            rec_id = matches[0]["id"]
            rr = requests.put(f"{API}/records/{rec_id}", headers=self.headers, json=payload, timeout=20)
            rr.raise_for_status()
        else:
            rr = requests.post(f"{API}/records", headers=self.headers, json=payload, timeout=20)
            rr.raise_for_status()
