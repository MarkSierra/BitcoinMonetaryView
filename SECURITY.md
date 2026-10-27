# Security policy

BitcoinMonetaryView is designed to be strictly read-only towards your Bitcoin node. Anything that could make it
change node state, read or write the node's files, leak credentials, or let a website or another user on your
network control it is a security issue.

## Reporting a vulnerability

Please report vulnerabilities privately via GitHub's **"Report a vulnerability"** (Security → Advisories) on this
repository instead of opening a public issue. Include steps to reproduce and the affected version.

You can expect an acknowledgement within a few days. Fixes are released as a new version and noted in
[CHANGELOG.md](CHANGELOG.md).

## Design guarantees (what we test for)

- Only whitelisted read-only RPC methods/parameters can be sent (`bitcoinmonetaryview/node/rpc.py`), including
  inside batch requests.
- No writes outside the app's own data directory; the RPC cookie file is opened read-only.
- Every block is verified (hash, merkle root, witness commitment) before it is counted.
- Web interface: localhost by default, optional basic auth and TLS, CSRF tokens on state-changing requests,
  Host allowlist against DNS rebinding, strict Content-Security-Policy, no secrets in API responses or logs.
- "Analyse this block now" reads exactly one block through the same whitelisted client, verifies it, keeps the
  result in memory only (never in the scan database), and is CSRF-protected, one request at a time with a
  cooldown.
- No external network requests besides your node.

## Verifying a download

Every release from 0.2.5 on carries a signed build-provenance attestation: a statement, signed with GitHub's
keyless Sigstore signing, that the file was built by this repository's release workflow from a specific commit.
With the [GitHub CLI](https://cli.github.com/):

```sh
# Docker image (covers all architectures)
gh attestation verify oci://ghcr.io/marksierra/bitcoinmonetaryview:0.3.2 --repo MarkSierra/BitcoinMonetaryView

# StartOS package, after downloading it from the package's GitHub release
gh attestation verify bitcoinmonetaryview_x86_64.s9pk --repo MarkSierra/BitcoinMonetaryView-startos
```

A successful check prints the workflow and commit the file was built from. If it fails, do not install the file.
The Docker images also carry build provenance and an SBOM (list of contained software) from the build itself.
