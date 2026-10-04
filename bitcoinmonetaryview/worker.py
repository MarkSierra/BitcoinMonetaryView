# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Block analysis in worker processes (Balanced/Full speed profiles).

Pure computation: no network, no files, no database. Each worker gets the raw
block, analyses and verifies it, and returns only what the scanner needs.
"""

from .rules import monetary_rules as mr
from .rules.upstream import carrier_policy as cp

_CFG = {"dust_start": mr.MAINNET_DUST_START, "policy": None}

# Keys the scanner uses; large intermediate lists (txids, wtxids, …) stay in the worker.
KEEP = ("original", "stored", "weight", "tx_count", "header", "spends", "utxo_spam", "filter_entries",
        "whole", "modified", "stripped", "dust_outputs", "retained_protocol", *mr.CARRIERS)


def init(dust_start, policy_on):
    _CFG["dust_start"] = dust_start
    _CFG["policy"] = cp.Policy() if policy_on else None


def analyze_verified(raw, height, hexhash, dust_start, policy):
    """Analyse and verify one block (hash, merkle root, witness commitment). Raises BlockInvalid."""
    res = mr.analyze_block(raw, height, dust_start_height=dust_start, carrier_policy=policy)
    mr.verify_block(res, expected_hash=mr.hex_to_hash(hexhash))
    return {k: res[k] for k in KEEP}


def analyze_in_worker(raw, height, hexhash):
    return analyze_verified(raw, height, hexhash, _CFG["dust_start"], _CFG["policy"])
