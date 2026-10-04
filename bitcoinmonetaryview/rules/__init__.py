"""
Spam classification rules.

`upstream/` holds byte-identical copies of the Monetary Node tools
(BSD-2-Clause, see upstream/LICENSE and NOTICE). `monetary_rules` applies them
to a block and collects the extra facts this app needs (UTXO effects, txids,
witness commitment) in the same single parse.
"""

UPSTREAM_REPO = "https://github.com/sambitcoin/BitcoinMonetaryNode"
UPSTREAM_COMMIT = "492539256d43151973cb4cdf73c91d67c4be203c"
UPSTREAM_DATE = "2026-09-24"
