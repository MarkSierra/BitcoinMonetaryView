# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Optional binary REST access (bitcoind `rest=1`), used only if already enabled.

REST endpoints are GET-only and read-only by design in Core/Knots. Paths are
built here from validated hashes only; nothing user-controlled reaches them.
"""

import json
import re

from .transport import NodeError, Transport

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_HEADERS = 2000


def _check_hash(h):
    if not isinstance(h, str) or not _HEX64.match(h):
        raise ValueError("invalid block hash")
    return h


class RESTClient:
    def __init__(self, transport: Transport):
        self.transport = transport
        self.available = False
        self.new_headers_syntax = True

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

    def headers(self, start_hash, count=MAX_HEADERS):
        """Up to `count` consecutive 80-byte headers starting at start_hash (inclusive)."""
        count = max(1, min(int(count), MAX_HEADERS))
        h = _check_hash(start_hash)
        if self.new_headers_syntax:
            status, data = self.transport.request("GET", f"/rest/headers/{h}.bin?count={count}")
            if status == 200:
                return _split_headers(data)
            self.new_headers_syntax = False          # Core < 24
        status, data = self.transport.request("GET", f"/rest/headers/{count}/{h}.bin")
        if status != 200:
            raise NodeError(f"REST headers request failed (HTTP {status})")
        return _split_headers(data)


def _split_headers(data):
    if len(data) % 80:
        raise NodeError("REST returned a truncated header list")
    return [data[i:i + 80] for i in range(0, len(data), 80)]
