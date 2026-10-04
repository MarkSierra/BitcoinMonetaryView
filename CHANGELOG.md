# Changelog

All notable changes are documented here. This project uses [semantic versioning](https://semver.org/).

## [0.1.0] — 2026-10-04

First version.

- Full-history spam analysis of a Bitcoin Core/Knots node using the Monetary Node rules (upstream commit
  `492539256d43`), byte-identical to upstream's `strip_block`.
- Exact current spam UTXO set, tracked block by block; reorg-safe; resumable.
- Read-only node access: RPC method/parameter whitelist, optional binary REST, cookie or user/password,
  HTTPS with custom CA, Tor via SOCKS5.
- Block verification (hash, merkle root, witness commitment).
- Dashboard is light on the viewing device: no endless animations, compositor-only progress bar, no polling
  while the tab is hidden.
- Web dashboard: overview, blocks, history, UTXO set, about, connection; status bar with progress and ETA;
  dark/light themes; CSV/JSON export; shareable summary image.
- Speed profiles, adaptive backoff, scan time window, pause/resume.
- Settings via CLI, environment or self-healing `settings.json`; managed mode for platform packages.
- Docker image and compose example.
