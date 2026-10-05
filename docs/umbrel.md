# Umbrel package

BitcoinMonetaryView is packaged for umbrelOS in the community app store
[MarkSierra/umbrel-community-app-store](https://github.com/MarkSierra/umbrel-community-app-store)
(app ID `marksierra-bitcoinmonetaryview`). The package is two files, `umbrel-app.yml` and
`docker-compose.yml`, and uses the multi-arch image `ghcr.io/marksierra/bitcoinmonetaryview`, pinned by
version tag and digest.

| Topic | How it is done |
|---|---|
| **Bitcoin node** | `dependencies: [bitcoin]`. Umbrel's Bitcoin Node app and Bitcoin Knots (which implements `bitcoin`) both export `APP_BITCOIN_NODE_IP`, `APP_BITCOIN_RPC_PORT`, `APP_BITCOIN_RPC_USER` and `APP_BITCOIN_RPC_PASS`; the compose file passes them as `BMV_RPC_URL`, `BMV_RPC_USER`, `BMV_RPC_PASSWORD`. Only the whitelisted read-only RPC methods are used; nothing on the node is changed. |
| **Platform mode** | `BMV_MANAGED_BY=umbrel`: Umbrel's `app_proxy` fronts the web UI and provides the login, the Host check is left to it, and plain HTTP on Umbrel's internal Docker network is not flagged. Settings stay editable in the dashboard (Umbrel has no settings screen for apps); the connection settings come from Umbrel and are shown as "set by Umbrel". |
| **Data** | `${APP_DATA_DIR}/data/app` is mounted at `/data`. `storage.dataRoot: data` lets umbrelOS 2.0 move it to another drive. |
| **Backups** | `backupIgnore` excludes the per-network scan databases (`data/app/mainnet`, …); only `settings.json` is backed up. The scan can always be rebuilt from the node. |
| **User** | The container runs as `1000:1000` (Umbrel's app user), with a read-only root filesystem, `/tmp` as tmpfs, all capabilities dropped and `no-new-privileges`. |
| **Port** | `8338` (the app's default; not used by any app in the official Umbrel store). |

## Updating the package

After a release of this repository (image `X.Y.Z` on GHCR):

1. Get the digest: `docker buildx imagetools inspect ghcr.io/marksierra/bitcoinmonetaryview:X.Y.Z`
   (check that `linux/amd64` and `linux/arm64` are listed).
2. In the store repo, set `image: ghcr.io/marksierra/bitcoinmonetaryview:X.Y.Z@sha256:<digest>` in
   `marksierra-bitcoinmonetaryview/docker-compose.yml`, and `version` / `releaseNotes` in its `umbrel-app.yml`.
3. Commit to `main`. Umbrel devices that added the store offer the update automatically.
