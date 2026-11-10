# Packaging notes: StartOS 0.4 (and similar platforms)

This repository is the platform-independent core. The StartOS package is a thin wrapper: a StartOS SDK project
that includes this repo as a git submodule (`upstream/`) and builds its own image from that source. Start9 builds
and publishes it from [Start9-Community/bitcoinmonetaryview-startos](https://github.com/Start9-Community/bitcoinmonetaryview-startos);
[MarkSierra/BitcoinMonetaryView-startos](https://github.com/MarkSierra/BitcoinMonetaryView-startos) is the
workspace changes are prepared in. The table below is what the wrapper relies on.
Based on the [StartOS 0.4.0.x packaging guide](https://docs.start9.com/packaging/0.4.0.x/).

| StartOS concept | How this app supports it |
|---|---|
| **Settings via Actions + File Model** | Actions write `/data/settings.json` (`merge()`). The app reads it with the schema in [settings.md](settings.md). Missing values are defaults, unknown keys are ignored, and changes are applied live. An unreadable file or an invalid value keeps the previous setting; the web login and the spam rules are never taken from defaults by accident (see [settings.md](settings.md)). |
| **Managed mode** | Set `BMV_MANAGED_BY=startos`. The web UI then shows settings read-only ("managed by StartOS") and disables settings changes and the rescan button. Pause/resume stays available. |
| **Dependency on Bitcoin Core / Knots / Roots** | `setupDependencies`: `bitcoind`, `kind: 'running'`, health checks `bitcoind` + `sync-progress`. Resolve the node with `sdk.host.getBridgeAddress(...)` (StartOS 0.4 no longer uses `bitcoind.startos`): prefer `rpc-local` (port 58332, bitcoind's own RPC **and REST**), fall back to the `rpc` proxy (port 8332, JSON-RPC only — the app then uses RPC and single calls instead of batches automatically). |
| **Credentials** | Mount the node's `main` volume **read-only as a directory** (`mountDependency({ dependencyId: 'bitcoind', volumeId: 'main', subpath: null, mountpoint: '/mnt/bitcoind', readonly: true })`) and set `rpc_cookie_file=/mnt/bitcoind/.cookie`. Do not bind-mount the single file: bitcoind replaces `.cookie` on every restart, and a file bind mount would keep showing the old one. The app re-reads the cookie on HTTP 401. |
| **Web UI interface** | Bind the app's port (8338) as a `ui` interface. The package protects it with the app's own basic auth (`auth_user` / `auth_password` in `settings.json`), set by a *Set Web Password* action that a critical task asks for; the action runs in any state and restarts a running service, so only the new password works. The Host-header check is delegated to the proxy in managed mode. |
| **Health checks** | Daemon readiness: `GET /api/health` (cheap). Standalone check "Scan progress": read `GET /api/status` → `phase` (`quick`/`sample`/`full` → `loading` with `progress` %, `live` → `success`, `error` → `failure`). Use a cooldown trigger (e.g. 30 s). |
| **Logs** | Plain lines on stdout. |
| **Volumes & backups** | One volume (`/data`). Back up `settings.json`; the per-network directories (`/data/<network>/`: the database `bmv.sqlite*` and the saved spent-output filter `spent-filter.bin`) can be rebuilt by rescanning and may be excluded to keep backups small. The app creates each per-network directory nodatacow on btrfs (`chattr +C`), so its files skip copy-on-write; files created before that keep their attribute and need a one-time copy (the package's `nocow.py`). |
| **Stopping** | On SIGTERM the app finishes its current batch and saves the spent-output filter for a fast next start; give it up to 60 s (the StartOS default). If it is killed sooner, the next start rebuilds the filter (a few minutes on mainnet). |
| **Languages** | The package texts (description, actions, settings, health check, release notes) come in English, German, Spanish, French and Polish; StartOS shows them in its system language, English otherwise. The dashboard has the same five languages and follows the browser's language (the header's language menu switches it). |
| **Architectures** | Pure Python: `x86_64`, `aarch64` and `riscv64` (all three built by the StartOS package). |

Recommended action set for the wrapper: *Node connection* (Core, Knots or Roots dependency), *Scan speed*
(`speed_profile`), *Scan window* (`scan_window`, `timezone`), *Carrier policy* (`carrier_policy` — changing it
triggers a rescan; say so in the action description), *Rescan* (deletes the `/data/<network>/` directories
while the service is stopped).
