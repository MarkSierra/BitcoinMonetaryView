# Packaging notes: StartOS 0.4 (and similar platforms)

This repository is the platform-independent core. The StartOS package is a thin wrapper in a separate
repository, [BitcoinMonetaryView-startos](https://github.com/MarkSierra/BitcoinMonetaryView-startos): a
StartOS SDK project that includes this repo as a git submodule (`upstream/`) and builds its own image from that
source. The table below is what the wrapper relies on.
Based on the [StartOS 0.4.0.x packaging guide](https://docs.start9.com/packaging/0.4.0.x/).

| StartOS concept | How this app supports it |
|---|---|
| **Settings via Actions + File Model** | Actions write `/data/settings.json` (`merge()`). The app reads it with the schema in [settings.md](settings.md) (`--print-settings-schema` prints JSON Schema). Invalid or missing values fall back to defaults (like zod `.catch()`), unknown keys are ignored, and changes are applied live. |
| **Managed mode** | Set `BMV_MANAGED_BY=startos`. The web UI then shows settings read-only ("managed by StartOS") and disables settings changes and the rescan button. Pause/resume stays (disable with `allow_pause_when_managed=false`). |
| **Dependency on Bitcoin Core / Knots** | `setupDependencies`: `bitcoind`, `kind: 'running'`, health checks `bitcoind` + `sync-progress`. Resolve the node with `sdk.host.getBridgeAddress(...)` (StartOS 0.4 no longer uses `bitcoind.startos`): prefer `rpc-local` (port 58332, bitcoind's own RPC **and REST**), fall back to the `rpc` proxy (port 8332, JSON-RPC only — the app then uses RPC and single calls instead of batches automatically). |
| **Credentials** | Mount the node's `main` volume **read-only as a directory** (`mountDependency({ dependencyId: 'bitcoind', volumeId: 'main', subpath: null, mountpoint: '/mnt/bitcoind', readonly: true })`) and set `rpc_cookie_file=/mnt/bitcoind/.cookie`. Do not bind-mount the single file: bitcoind replaces `.cookie` on every restart, and a file bind mount would keep showing the old one. The app re-reads the cookie on HTTP 401. |
| **Web UI interface** | Bind the app's port (8338) as a `ui` interface. Authentication can be enforced by the StartOS reverse proxy (`addSsl.auth`); the app's own basic auth can stay off in managed mode. The Host-header check is delegated to the proxy in managed mode. |
| **Health checks** | Daemon readiness: `GET /api/health` (cheap). Standalone check "Scan progress": read `GET /api/status` → `phase` (`quick`/`sample`/`full` → `loading` with `progress` %, `live` → `success`, `error` → `failure`). Use a cooldown trigger (e.g. 30 s). |
| **Logs** | Plain lines on stdout. |
| **Volumes & backups** | One volume (`/data`). Back up `settings.json`; the per-network databases (`/data/<network>/bmv.sqlite*`) can be rebuilt by rescanning and may be excluded to keep backups small. |
| **Architectures** | Pure Python: `x86_64`, `aarch64` (and `riscv64` where the base image exists). |

Recommended action set for the wrapper: *Node connection* (Core or Knots dependency), *Scan speed*
(`speed_profile`), *Scan window* (`scan_window`, `timezone`), *Carrier policy* (`carrier_policy` — changing it
triggers a rescan; say so in the action description), *Rescan* (deletes `/data/<network>/bmv.sqlite*` while
the service is stopped).
