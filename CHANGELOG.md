# Changelog

All notable changes are documented here. This project uses [semantic versioning](https://semver.org/).

## [Unreleased]

- **A damaged `settings.json` no longer switches the web login off or deletes the scan results.** Until now a
  settings file that could not be read (cut short, a typo), or a single invalid value, made the affected
  settings fall back to their defaults while the app was running. For the web login the default is "none",
  and for the spam rules a changed value means "delete the results and rescan". Now the app keeps what the
  file said before and shows a warning. If the file is already unreadable when the app starts, the web
  interface stays locked (503) until it is repaired or deleted (not on Umbrel, whose own login protects the
  dashboard), and a scan made with non-default spam rules is put on hold with its results kept. The settings page refuses to save into a damaged file instead of
  overwriting it. Settings from the command line or the environment are unaffected.
- **Quick pass after an interruption:** if the app was stopped during the quick pass and started again after
  the chain had grown by more blocks than the pass had already done, the old piece stayed behind, with
  unscanned blocks between it and the latest 1,000. The dashboard then reported that stretch as scanned and
  left it out of the whole-chain estimate until the full scan reached it. A new quick pass now drops such
  leftovers, so the latest blocks are always one unbroken range.
- **RPC URL with a password in it:** an `rpc_url` written as `http://user:password@host:8332` is now refused
  as a value, with a warning to use `rpc_user`/`rpc_password` or the cookie file. Before, the app refused to
  connect with it but still showed the whole URL, password included, on the Connection page and in the
  settings API.
- **Web login with a user name but no password** is now a configuration error: the web interface stays
  locked (503) with an explanation until both are set. Before, it accepted the user name with an empty
  password.
- **Unknown network names from the node are refused.** The app names a directory after the network the node
  reports; a name other than main, test, testnet4, signet or regtest now stops the scan with an error instead
  of creating a directory for it (which could lie outside the data directory and outside the backup
  exclusions).
- **Web server hardening:** a wrong web password is answered after half a second and logged, so guessing is
  slow and visible; at most 64 connections are served at once; custom ranges and the per-block chart are
  cached for 3 seconds like the overview. A request body the server does not expect (a GET with a body, a
  chunked POST) can no longer be read as a second request on the same connection.
- **Clear answers to bad input:** an unknown time zone on the Connection page is now reported as unknown
  instead of "internal error"; out-of-range numbers in API requests and unusual characters in
  request headers get a plain error instead of a logged traceback.
- **Block check:** a block whose last transaction was repeated passes the merkle root check (a known
  property of Bitcoin's merkle tree); a dishonest node could have served such a block and inflated the
  spam figures. Such blocks are now refused, as Bitcoin Core does.
- **Downgrades:** a scan database written by a newer version of the app is now refused with a clear message
  and left as it is, instead of being used by an older version that does not understand it.
- **Status texts:** while paused or outside the scan window, the status line no longer says "New blocks are
  still shown" when the quick pass is off, because then they are not; the progress and ETA figures are
  recalculated after an interrupted scan step.
- **UTXO set size once a week instead of daily:** to show what share of the UTXO set is spam, the app asks the
  node for the size of its UTXO set. To answer, Bitcoin Core first writes the part of the UTXO set it holds
  in memory to disk and then reads the whole set, which takes a few minutes of the node's disk and CPU. The
  app now asks once a week (after a failure again a day later, not an hour later), and the README and the
  About page say plainly what the request does.
- **Lighter dashboard on large scans:** the trends no longer repeat the monthly history query (on a fully
  scanned chain 0.1 s instead of about 1 s on a desktop, several times that on small hardware), and once
  more than 100,000 blocks are scanned the overview is refreshed every minute and its figures are cached
  longer.
- **Source code link:** "Free software (AGPL-3.0)" in the footer now links to the source code of the running
  version.
- **Custom range:** dates entered in reverse order (end before start) now include both edge days.
- **Docker health check** no longer goes through proxy settings in the container's environment.
- **Build:** every GitHub Action in the build and release workflows is pinned to an exact commit, so a moved
  or compromised release tag of a third-party action cannot change the published image.

## [0.3.12] — 2026-11-09

- **Quieter log:** when a browser leaves before an answer arrives (the page is reloaded or closed while it
  loads), the app no longer logs this as an "API error" with two long tracebacks; it simply drops the
  connection. Real errors are still logged as before.

## [0.3.11] — 2026-11-09

- **Gentler on the disk, especially on btrfs (StartOS):** the scan databases are created without
  copy-on-write on btrfs (`chattr +C` on each network's data directory; other file systems are unaffected).
  A database rewritten in small pieces fragments badly on copy-on-write file systems, and on one StartOS
  server this drove btrfs into a fault that made the data drive read-only. The databases lose btrfs
  checksums as a result; they can always be rebuilt from the node and are not in backups. Existing database
  files keep their attribute (the StartOS package rewrites them once in its 0.3.10:1 update).
- SQLite now flushes the database to disk every 64 MB of changes instead of every 4 MB, so a fast scan forces
  far fewer disk flushes (about 16 times fewer while the history scan runs). An app crash loses nothing; a
  power cut can lose the scan progress since the last flush, which the scan then redoes (the database stays
  consistent either way).

## [0.3.10] — 2026-11-07

- **Trends and records:** a tile still waiting for its blocks now says "Available once the full scan is done"
  (and the year-earlier comparison likewise). The percentage shown in 0.3.9 stood still for hours: the full
  scan runs from the first block upwards and reaches these recent windows only at its end. Where the blocks
  cannot come any more (e.g. a pruned node after the full scan), the tile says it is not available on this
  node.

## [0.3.9] — 2026-11-07

- **Trends and records:** the card always shows its four tiles. A tile whose blocks are not scanned far
  enough yet shows "–" and how far they are ("Available at 95 % scanned (now 12 %)"), instead of leaving a
  gap; the year-earlier comparison under "Spam share of recent blocks" says the same. The tiles follow the
  same 4 / 2 / 1 columns as the other rows (they could end up three and one).

## [0.3.8] — 2026-11-07

- **Bitcoin Roots:** Monetary View works with [Bitcoin Roots](https://plan-b.foundation/bitcoin-roots/), the
  Bitcoin Core-based node of the Plan ₿ Foundation, as with Core and Knots. The automated tests now run the
  app against real Roots 29.4 and 31.1 nodes, and against Bitcoin Core 32.1, next to Core 29.1/31.1 and Knots.
  Texts that named "Core/Knots" now name Roots too.
- **Latest blocks:** the line below the share ("spam: 92.1 kB of 1.41 MB") was cut off on most screen widths;
  it now uses two lines ("spam: 92.1 kB", "of 1.41 MB") that fit in every language at every width. Eight
  tiles per row only from about 1,180 px wide; narrower screens show four, and up to 700 px (phones, small
  tablets) the latest four in two columns, all with a fixed height so the spam fill stays clearly visible.
- **Database connections:** the dashboard's read-only database connections are now pooled and returned after
  each request, and closed when the database is replaced (rescan, other network) or the app stops. Before,
  every request thread opened its own connection, which was only closed by Python's garbage collector.

## [0.3.7] — 2026-11-05

- **Realistic remaining time at the start of the full scan:** the first years of the chain are nearly empty
  blocks, so the data rate there is tiny and the estimate said "~400 days". Until the blocks reach half the
  chain's average size, the estimate now uses at least the data rate the sample pass measured across the
  whole chain.
- **Latest blocks:** each card shows the spam share with one decimal below 10 % ("0.3 %" instead of "0 %")
  and spells out what the small line means: "spam: 4.1 kB of 1.26 MB" (it showed only the block size).
- **Overview:** "Transactions with spam" now comes before "Spam entries in the UTXO set", which stays empty
  until the full scan is done; the share card uses the same order.
- **Status bar:** the line describing the current block may use two lines, so it is no longer cut off in
  German, Spanish, French or Polish. Numbers and their units ("5.6 %", "23.1 MB") no longer break across
  lines. During the full scan the progress reads "12.34 % of data": it is measured by data volume, which is
  why the nearly empty early years barely move it. On phones the phase sits on top with the current block
  and the progress bar below it, and the figures use two columns, so they fit in every language.
- **UTXO set:** "Chainstate they occupy" is rounded like the per-type figures (it showed 465 B next to
  466 B for the same outputs).
- **All settings on StartOS and Umbrel:** the description no longer suggests the command line, environment
  variables or `settings.json`; it says the platform sets the connection and where your own choices are
  made (StartOS: the service's Actions; Umbrel: the scan settings above).
  The settings list on the Connection page is folded away by default (it is a read-only reference); on StartOS
  and Umbrel it is called "Technical details (for support)", since hardly any of it can be changed there.
- **Connection test on StartOS and Umbrel:** no advice to edit `bitcoin.conf` for REST; the platform manages
  the node's configuration.

## [0.3.6] — 2026-11-05

- **One name:** the app is called **Monetary View** everywhere you see it — header, footer, About page, share
  text, messages, logs, the browser's login prompt and the docs, in all five languages. `BitcoinMonetaryView`
  stays only as the technical name (GitHub repository, Docker image, Python package, StartOS package id); the
  About page and README mention it once ("on GitHub: BitcoinMonetaryView"). No change to the figures.

## [0.3.5] — 2026-11-05

- **Python 3.15:** the image now runs on the current `python:3.15-slim` base (Python 3.15.2, Debian 13),
  pinned by digest, for amd64, arm64 and riscv64. Before the switch the whole test suite, including the browser
  tests in all five languages, passed on Python 3.15 and inside the new image itself. CI now tests every
  Python from 3.10 (the oldest supported) to 3.15, and also runs the full suite inside the built image. No
  change to the app's behaviour or figures.

## [0.3.4] — 2026-11-05

- **Independence stated clearly:** the About page (in all five languages), the README and NOTICE now say that
  BitcoinMonetaryView is an independent project, not affiliated with or endorsed by the Monetary Node project,
  and that it uses that project's published spam rules unchanged. No change to the figures.

## [0.3.3] — 2026-10-27

- **Open pages update themselves:** a dashboard tab or installed app window that stays open used to keep
  running the code it was loaded with after the app was updated, until it was reloaded by hand. It now notices
  the new version in its regular status update and reloads once, on the same page. (The files themselves were
  never cached: the server already makes the browser fetch them fresh on every page load.)

## [0.3.2] — 2026-10-27

- **Install as an app:** the dashboard has a web app manifest and icons, so Chrome, Edge and Brave offer
  "Install" and phones "Add to Home Screen". It opens in its own window with its own icon; the window's title
  bar follows the light or dark theme. Nothing is cached offline (no service worker): it always shows your
  node's live figures. The manifest is also loaded behind the app's own login.
- **Biggest spam month opens its range:** in Trends and records, the "Biggest spam month" tile now links to the
  History page with that month loaded as a custom range (the dates filled in) and scrolls the range card into
  view. Like the biggest spam block, it is marked with an arrow.

## [0.3.1] — 2026-10-27

- **Clearer label:** "Transactions touched" is now "Transactions with spam" (on the overview, in a custom
  range, on the share card and as the Blocks table column) in all five languages. It is the share of
  transactions a Monetary Node changes: it removes their spam part or keeps only their txid. The figure itself
  is unchanged; upstream calls it "transactions modified or stripped".
- **About page brought up to date:** it now explains "Transactions with spam" and where Trends and records come
  from (and when a comparison appears), lists the five languages with how to switch and how to report a
  mistake, and names both inscription envelope forms the rules detect (`OP_FALSE OP_IF` and `OP_TRUE
  OP_NOTIF`). The README's spam table names both forms too.
- **Tidier tiles:** the figure tiles no longer carry a coloured square. Only some had one, and the one on
  "Spam in your blocks" used the inscriptions colour for all carriers; colour keys now appear only in chart
  legends, where they match a colour in the chart. In Trends and records, the biggest spam block, the one
  tile you can click, is now last and has an arrow.
- README: how to compare with upstream's published figures, which are in binary units ("GB" = GiB), with
  the converted values from a full mainnet node and why the storage saving is slightly higher here.

## [0.3.0] — 2026-10-27

- **Five languages:** the dashboard is now also available in Spanish, French and Polish, besides English and
  German — the languages of StartOS. It follows the browser's language (English otherwise); the new language
  menu in the header switches it. Each translation is its own file and only the chosen one is loaded; Polish
  number-dependent texts use the right plural forms (1 blok, 2 bloki, 5 bloków). The translations are new: the
  footer links to a form for reporting mistakes. Exports keep English column names.
- **Trends and records** on the overview: spam in the last 30 days and its yearly pace, the spam share of the
  last 1,000 blocks compared with a year earlier, and the biggest spam block and month. Computed from the
  blocks already scanned (no extra requests to the node); a window counts only when 95 % of it is scanned.
- **Layout on tablets and phones:** on screens up to 1,200 px the tabs get their own row you can swipe, with the
  current tab in view (on a tablet, "Connection" used to be cut off with no way to reach it). On phones the
  status figures sit in a tidy grid, and the overview shows the latest four blocks in compact tiles.
- **Block tiles:** the height, date and spam share no longer overlap the fill, so they stay readable at any share
  and in the light theme.
- Placeholders in the shape of the page instead of a bare "Loading…"; the UTXO tile shows "–" instead of "0"
  before the full scan has counted anything; tidier settings form (range buttons no longer break into two
  lines, even input widths, "—" for empty values).
- **Correction:** oversized scriptSigs are counted as spam but, as upstream's coverage audit
  (`docs/CARRIERS.md`) now states, a Monetary Node keeps them in block storage. The About page and README no
  longer say they are removed. The figures were already right, since "storage saved" uses upstream's own
  calculation. Upstream's rule files are unchanged since the pinned commit, so no rescan is needed.
- Fix: switching between pages no longer shifts the header and page a few pixels sideways. A short page
  without a scrollbar (e.g. UTXO set) was wider than a long one; the scrollbar's space is now always kept.
- The About page uses the full width for its text, like the other pages.

## [0.2.5] — 2026-10-27

- German dashboard: every page, chart, message and the share card are available in German, with German number
  formats (40,4 GB). The dashboard follows the browser's language; the new EN/DE button in the header switches it
  and is remembered in that browser. Messages from the scanner and the connection test are translated too.
- Fix: the scanner's parallel block fetching kept up to four connections to the node open after a scan session
  ended (settings change, reconnect, stop); they are now all closed.
- Supply chain: the base image is pinned by digest (Dependabot proposes updates weekly), and release images
  carry a signed build-provenance attestation; SECURITY.md shows how to verify an image or a StartOS package.
- Block details: long words are no longer split mid-word (only hashes break anywhere). On the Connection page a
  "set via …" note sits on its own line under its label.

## [0.2.4] — 2026-10-27

- RISC-V: the Docker image is now also published for `linux/riscv64`, next to x86_64 and ARM64. The StartOS
  package builds for riscv64 as well (since package version 0.2.3:1).
- Faster filter rebuild: when no saved spent-output filter can be used (first start after an update, after a crash),
  rebuilding it takes about half as long as before (about 2 instead of 4 minutes for ~48 million spam outputs).
  The filter now keeps all bits of an entry in one 64-byte block, which also makes the spend check during the
  scan faster. The first start after this update rebuilds the filter once, because its saved format changed.
- Tested against more nodes: every change is now checked against a real regtest node running Bitcoin Core 29.1,
  Bitcoin Core 31.1 and Bitcoin Knots 29.4.1.
- Dashboard on phones: the Connection page no longer scrolls sideways (the settings table widened the whole
  page); wide content now scrolls inside its card, also on tablets.
- Tested in a real browser: every change now opens every dashboard page in Chromium, at desktop, tablet and
  phone width, and fails on JavaScript errors, failed requests, requests leaving the app, or sideways scrolling.
- GitHub: a bug report form that asks for platform, version, node and logs.

## [0.2.3] — 2026-10-27

- Faster restarts: the spent-output filter is saved when the app stops and loaded back in seconds on the next
  start, instead of being rebuilt from the database (about 4 minutes on a fully scanned mainnet node). The saved
  copy is only used while the database is unchanged since it was written and is checked for damage; otherwise
  the filter is rebuilt as before. Like the database, it is excluded from StartOS and Umbrel backups.
- The image is rebuilt on the current `python:3.12-slim` base, with the latest Python and Debian security updates.

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
