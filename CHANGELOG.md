# Changelog

All notable changes are documented here. This project uses [semantic versioning](https://semver.org/).

## [0.2.2] — 2026-10-05

- Umbrel: new platform mode (`BMV_MANAGED_BY=umbrel`) for the Umbrel package in the
  [community app store](https://github.com/MarkSierra/umbrel-community-app-store). Umbrel's proxy fronts the
  dashboard and provides the login, the connection to Umbrel's Bitcoin Node / Knots app is not flagged as
  plain HTTP, and the settings stay editable in the dashboard. The Connection page says the app runs on Umbrel.

## [0.2.1] — 2026-10-05

Bug fixes from a code and security review of 0.2.0 (the security review found no vulnerabilities).

- Pruned nodes: the sample pass no longer gets stuck retrying when the node has pruned the blocks it wants to
  sample; it moves past them like the full scan does.
- After the full scan is complete, each new block is downloaded and analysed once again, not twice.
- No more false "Chain reorganisation" warnings when a new block arrives during the history scan.
- After the app was off for a while during the scan, every block mined meanwhile is picked up, not only the
  last 10; previously the rest counted as scanned and was missing from the whole-chain estimate.
- Changing `sample_every` while the sample pass runs no longer mixes two sampling grids (the documented
  behaviour: it applies from the next rescan).
- Custom range: a range reaching back before the scanned blocks no longer shows as 100 % scanned; the
  shortcuts (24 hours, 7 / 30 days, this year) are recalculated each time instead of keeping the time of the
  first click; a slow earlier answer no longer replaces a later choice; "This year" clears the year list.
- Block dialog: a slow "Analyse this block now" result no longer appears in a dialog opened meanwhile; the
  "scan gets there" time uses the byte-based scan estimate (block counts were far too optimistic).
- Overview: new data that arrived while a dialog was open is shown when it closes.
- Block search: non-ASCII digits (e.g. "²") are rejected with a clear message instead of an internal error;
  after switching to another node or rules, on-demand results from the old one are dropped; finding a block
  by hash uses a database index (built once on the first start after the update).

## [0.2.0] — 2026-10-05

- Sample pass: right after the quick pass, every 100th block of the remaining history is analysed (about 1 %
  of the data), and the dashboard shows an estimate for the whole chain with its margin of error from the start.
  It is marked with ≈ and converges to the exact figures as the full scan proceeds. Setting `sample_every`.
- Overview: while the full scan runs, only the whole-chain estimate is shown (≈), with a line giving the exact
  figure so far — no more switching between estimate and exact figures. Fixed a race that made the estimate
  briefly disappear (summary queries now read one consistent database snapshot).
- Overview: corrected the storage note — a Monetary Node usually saves more than the spam itself, because it
  also drops the witness data of spam-carrying transactions.
- History: months the scan has not reached are greyed out ("not scanned yet") instead of looking spam-free.
  New **Custom range** card: spam statistics for any period, with shortcuts (24 hours, 7 / 30 days, this
  year, any year, since Ordinals, all time) or free dates / block heights, and the share of the range scanned.
- Blocks: **Find a block** by height or hash, a line showing which blocks are scanned, and **Analyse this
  block now** for blocks the scan has not reached (read-only, verified, one at a time with a cooldown).
- Newly mined blocks are picked up every 2 minutes during the history scan too, so "Latest blocks" and the
  totals stay current (previously they froze until the scan reached the tip).
- All notes in page content use one style (the bordered banner of the UTXO page); the "managed by StartOS"
  note appears only on the Connection page.
- Phones: all six tabs are visible (wrapped under the brand) instead of three hidden in a scroll row.
- Share dialog: explains that the card uses exact figures only and, during the scan, covers only the part
  scanned so far; the share text links this app too.
- About: new sections "Spam vs. storage saved" and "While the scan is running".
- History: milestone labels no longer overlap on narrow screens (a label that does not fit keeps its line and
  a tooltip). UTXO: the share of all UTXOs is hidden if the node's figure is older and smaller than the live
  count.
- Docs: README screenshot, Start9 download from Releases, SECURITY (on-demand lookup), release steps.
- Dashboard: the progress bar no longer animates (the animation kept the GPU drawing ~19 frames per second
  during a scan), and background data refreshes swap the content without the fade-in and only when the data
  actually changed, so the page no longer appears to reload every few seconds.

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
