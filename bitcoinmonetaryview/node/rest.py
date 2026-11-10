# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Optional binary REST access (bitcoind `rest=1`), used only if already enabled.

REST endpoints are GET-only and read-only by design in Core/Knots/Roots. Paths are
built here from validated hashes only; nothing user-controlled reaches them.
"""

import json
import re

from .transport import NodeError, Transport

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _check_hash(h):
    if not isinstance(h, str) or not _HEX64.fullmatch(h):
        raise ValueError("invalid block hash")
    return h


class RESTClient:
    def __init__(self, transport: Transport):
        self.transport = transport
        self.available = False

    def probe(self):
        """Detect whether REST is enabled. Never fails loudly: REST is optional."""
        try:
            status, data = self.transport.request("GET", "/rest/chaininfo.json")
            self.available = status == 200 and isinstance(json.loads(data), dict)
        except Exception:
            self.available = False
        return self.available

    def block(self, block_hash):
        status, data = self.transport.request("GET", f"/rest/block/{_check_hash(block_hash)}.bin")
        if status != 200:
            raise NodeError(f"REST block request failed (HTTP {status})")
        return data
