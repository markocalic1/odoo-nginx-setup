from __future__ import annotations

import requests

API = "https://api.cloudflare.com/client/v4"


class CloudflareClient:
    def __init__(self, token: str):
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _zone_candidates(self, fqdn: str) -> list[str]:
        parts = fqdn.split(".")
        return [".".join(parts[i:]) for i in range(len(parts) - 1)]

    def find_zone_id(self, fqdn: str) -> str:
        for zone_name in self._zone_candidates(fqdn):
            r = requests.get(f"{API}/zones", headers=self.headers, params={"name": zone_name}, timeout=20)
            r.raise_for_status()
            data = r.json()
            if data.get("success") and data.get("result"):
                return data["result"][0]["id"]
        raise RuntimeError(f"Could not find Cloudflare zone for {fqdn}")

    def upsert_record(self, zone_id: str, rtype: str, name: str, content: str, proxied: bool = False) -> None:
        r = requests.get(
            f"{API}/zones/{zone_id}/dns_records",
            headers=self.headers,
            params={"type": rtype, "name": name},
            timeout=20,
        )
        r.raise_for_status()
        existing = r.json().get("result", [])
        payload = {"type": rtype, "name": name, "content": content, "ttl": 120, "proxied": proxied}
        if existing:
            rec_id = existing[0]["id"]
            rr = requests.put(f"{API}/zones/{zone_id}/dns_records/{rec_id}", headers=self.headers, json=payload, timeout=20)
            rr.raise_for_status()
        else:
            rr = requests.post(f"{API}/zones/{zone_id}/dns_records", headers=self.headers, json=payload, timeout=20)
            rr.raise_for_status()
