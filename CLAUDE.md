# Working notes for Claude

Monetary View (repo name BitcoinMonetaryView): a read-only dashboard that analyses a Bitcoin Core/Knots
node with the Monetary Node spam rules. AGPL-3.0-or-later. Independent of the Monetary Node project.

## Ground rules from the maintainer

- Strictly read-only towards bitcoind: RPC/REST whitelist, never change the node's configuration, never
  modify block files (not even when reading them directly). Bitcoind's volume is mounted read-only.
- The scan databases are never included in backups.
- Never ask for tokens or passwords in chat.
- Security and standard procedures come first. Test everything before it ships.

## Repositories

| Repo | Role |
|---|---|
| `MarkSierra/BitcoinMonetaryView` (this one) | the app; releases are tagged here (`vX.Y.Z`), image on ghcr.io |
| `Start9-Community/bitcoinmonetaryview-startos` | the StartOS package. **Start9 builds and publishes only from here.** A fork of the repo below |
| `MarkSierra/bitcoinmonetaryview-startos` | our workspace for the package: sync `main` from the Start9-Community fork, branch, test, then open PRs to the fork. Never release from here |
| `MarkSierra/umbrel-community-app-store` (`marksierra-bitcoinmonetaryview/`) | Umbrel community listing; image pinned by digest. No CI; merge directly |
| `MarkSierra/umbrel-apps` (`bitcoinmonetaryview/`, branch `bitcoinmonetaryview`) | fork of `getumbrel/umbrel-apps` for the official Umbrel App Store; PRs to getumbrel are opened by the maintainer |

## StartOS release flow (through Start9-Community)

1. App change → PR in this repo → CI green → merge → run `release.yml` → note the image digest.
2. Package: sync `MarkSierra/bitcoinmonetaryview-startos` `main` from the Start9-Community fork (a branch
   cut from an unsynced copy reverts Start9's review changes). Branch with the **final** version
   (`X.Y.Z:0`, or `:N+1` for a package-only change), move the `upstream` submodule, copy the `FROM` line of
   `upstream/Dockerfile`, write release notes in all five languages.
3. **Test build:** a separate branch (`test/X.Y.Z-beta.N`) with one extra commit that only sets the version to
   `X.Y.Z:0-beta.N`; run the **Build** workflow on it (workflow_dispatch) and give the maintainer the
   `.s9pk` artifact link. `X.Y.Z:0-beta.N` < `X.Y.Z:0`, so the published build arrives as a normal update.
   The maintainer sideloads and tests: update over the installed version, a clean install, tasks/actions,
   sign-in, health checks. Fixes go on the final-version branch; the next test build is `-beta.N+1`.
4. PR from the final-version branch (never a test branch) to `Start9-Community/bitcoinmonetaryview-startos`
   `main`. This session cannot post or open PRs there (Claude's GitHub App is not installed on that org):
   give the maintainer a prefilled compare link plus the description in a plain code block. Leave
   "Allow edits and access to secrets by maintainers" unchecked.
5. Each merge there publishes to **Community Beta** (`https://community-beta-registry.start9.com`). Promotion
   to the **Community Registry** is the maintainer's call: an issue on the fork (copies the same build).
   Ask there for the Bitcoin category too (categories are set by the registry, not the manifest).
6. After each merge: sync our `main` from the fork; delete merged branches.
7. Umbrel: bump image tag and digest, version and releaseNotes in `umbrel-app.yml` (community store), and
   the same in the official-store package on the fork (PR to getumbrel). Run the *umbrelOS* workflow first.

Reading the fork: `git` works directly; for PR comments/reviews/CI use a helper session that has the fork
attached (read-only). Nothing on github.com/Start9-Community is posted without the maintainer's own action.

Practical notes from the first releases through the fork:
- Claude's GitHub App is not installed on Start9-Community: this session and the helper can read the fork but
  cannot comment, review, open PRs or watch PR activity there. The maintainer posts (give texts in a plain
  code block; they copy them on a phone) and opens PRs via a prefilled compare link
  (`.../compare/main...MarkSierra:BitcoinMonetaryView-startos:<branch>?expand=1&title=...`). A PR from our
  branch updates itself on every push; Start9's build checks run on their own. Keep the PR title and
  description in step with the version (the maintainer edits them).
- A helper session for reading the fork: `session_014Xu9HKAQ8jL94y4Y7ZwcfN` (send_message, read-only). It
  may no longer exist in a later session; create a new one with the fork as source if needed.
- Watching a PR or issue there: scheduled tasks (minimum interval one hour; two hourly tasks at different
  minutes give ~30 min) that run `git ls-remote` on the fork and ask the helper for comments; delete them
  after the merge. Matt Hill (Start9) usually answers in the US afternoon/evening; helix-a is Start9's AI
  reviewer.
- Deleting branches with `git push --delete` is refused by this environment's proxy: ask the maintainer to
  delete merged branches on GitHub.
- Downloading and running third-party node binaries here (e.g. a Roots release) is blocked; test such nodes
  in CI only (the regtest matrix).
- Promotion: a blank issue on the fork (none of the fork's issue templates fits), stating what was tested on
  which build. Categories (Bitcoin) are set by Start9 on request.
- Version numbers may skip: a package version that never reached a registry can be replaced by a newer one
  in the same PR (0.3.8:0 became 0.3.10:0); the release notes then cover everything since the last
  published version.
- Bitcoin Roots: the dependency range accepts `>=#roots:29.4:0`, but Roots is not on the Community Registry
  yet, so package descriptions and release notes do not mention it (Matt's request on PR #4). Add the wording
  back once Roots is on community-prod and tested against the Roots package on StartOS. The app itself and
  its README may name Roots.
- Release notes follow the guide's format: "Updated Monetary View to X.Y.Z." then short bullets
  (New/Fix/Internal), then a link to the GitHub release, in all five languages (FR: no-break space before
  ":" and ";").

## Umbrel official App Store

- Umbrel requires a runtime test through umbrelOS before a PR. `.github/workflows/umbrel.yml` does it in CI
  (umbrelOS container on amd64/arm64, Bitcoin Node switched to regtest via its `settings.json`
  `{"chain": "regtest"}`, `umbreld client` for install/restart/logs, Playwright through Umbrel's port), plus
  Umbrel's `lint:apps --check-images`. Running Umbrel's lint or Docker here is blocked; use CI.
- Official app IDs have no store prefix; `bitcoinmonetaryview` matches StartOS. No `icon`, `gallery: []`,
  `releaseNotes: ""` for a new app, `submission:` is the PR link. Lint warns about `security_opt`
  (`no-new-privileges`); justify it in the PR as hardening.
- This session cannot fork, comment or open PRs on getumbrel: the maintainer opens the PR from a prefilled
  compare link (leave "Allow edits by maintainers" on there: Umbrel adds the gallery and icon itself).
- umbrelOS 2.0.1 CLI notes: the store is added with `appStore.addRepository`; `docker cp` into the umbrelOS
  container fails for `/home/umbrel/umbrel/...`, pipe a tar through `docker exec` instead.

## State (2026-11-09)

App v0.3.12. StartOS: 0.3.10:0 in the Community Registry; Matt's 0.3.10:1 (nocow migration, Start9 PR #6)
on Community Beta; 0.3.12:0 submitted as Start9-Community PR #7 (0.3.10:1 kept as a historical version in
`startos/versions/v0.3.10_1.ts`; 0.3.11:0 was never submitted). Umbrel community store 0.3.12; official
Umbrel App Store getumbrel/umbrel-apps#6328 at 0.3.12, waiting for review. Open: Start9 PR #7, then
promotion and the Bitcoin category; the Umbrel review; Roots wording once Roots is on community-prod.

Lessons from 0.3.10:1-0.3.12: a StartOS user's btrfs data drive went read-only during a first full scan
(fsync of a fragmented copy-on-write SQLite file). The app now creates its per-network directory
nodatacow and checkpoints the WAL every 64 MB (`store.py`, CI job `btrfs`). A StartOS migration that must
still run for users skipping a version stays in a historical version file, not in `current.ts`.

## Start9's packaging rules (they hold every registry package to these)

Guide: https://docs.start9.com/packaging (see `versions.md` "Release Notes", `project-structure.md`,
`writing-readmes.md`, `publishing.md`). Read the relevant pages before each PR.

- **Code comments carry one fact, in one line.** History and reasons go in the PR description.
- **Release notes are for users deciding whether to update.** No entries for defects that never reached a
  published community version; no old-behaviour narration. All five languages (en, de, es, fr, pl).
- **`UPDATING.md` is the upstream bump recipe and nothing else.** Our own workflow (the `-beta.N` testing)
  does not go into the package repo, nor into its `AGENTS.md`.
- README follows the guide's section set; no version numbers in it.
- Pre-publish checklist: `tsc --noEmit`, prettier (`--config node_modules/@start9labs/start-sdk/prettier.config.json`),
  pack/build green, tested end-to-end on StartOS (install, start, UI, health checks, uninstall/reinstall).
- Dashboard checks before a UI release: measure texts in all five languages at 360–1920 px with Playwright
  (`PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`, Python 3.15 venv with playwright 1.63.0) and send screenshots;
  the demo server (`python3 -m tests.demo`) has a 50-block quick pass, so tiles that wait for it are not
  representative of mainnet.
- The web UI uses the app's own basic auth (`auth_user`/`auth_password` in settings.json); **Set Web
  Password** runs in any state and restarts a running service (an only-stopped action behind a critical
  task deadlocks after an update).
