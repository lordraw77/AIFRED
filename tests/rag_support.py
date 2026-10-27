"""Shared material for the knowledge-base tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aifred.app import Services
from tests.fake_llm import FakeLLM

PROXMOX = """# Proxmox upgrade

## Backup first

Before an upgrade take a snapshot of every VM and dump the config. Keep the backup on a separate disk.

## Upgrade steps

Run apt update and apt full-upgrade on each node, one node at a time. Reboot when the kernel changed.

## Troubleshooting

If the cluster loses quorum after the update check corosync with pvecm status. Error code PVE-ERR-4471 means the ring is down.
"""

NETWORK = """# Network map

## VLANs

VLAN 10 carries the servers, VLAN 20 the cameras and VLAN 30 the guests.

## DNS

The resolver runs on 10.70.0.2 and forwards to the provider.
"""


@dataclass
class KB:
    services: Services
    fake: FakeLLM

    @property
    def rag(self):  # type: ignore[no-untyped-def]
        return self.services.rag

    def add(self, name: str, text: str, collection: str = "homelab") -> dict[str, Any]:
        if not self.services.store.rag_collection_get(collection):
            self.rag.create_collection(collection, f"{collection} notes")
        return self.rag.add_document(collection, name, text.encode())

    def index(self) -> int:
        return self.rag.drain()

    def embedded_texts(self) -> list[str]:
        return [t for req in self.fake.embed_requests for t in req.body["input"]]

    def doc(self, source: str) -> dict[str, Any]:
        [d] = [d for d in self.rag.documents() if d["source"] == source]
        return d
