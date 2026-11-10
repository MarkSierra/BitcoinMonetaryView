# Contributing

Thanks for helping! A few ground rules keep this project trustworthy:

1. **Read-only stays read-only.** Never add an RPC method to the whitelist that can change node state, and
   never add code that writes outside the app's data directory. `tests/test_server.py::StaticSafety` enforces
   part of this; reviewers check the rest.
2. **Standard library only.** No third-party Python packages, no CDNs or external requests in the web UI.
3. **Upstream rules are not edited.** Files in `bitcoinmonetaryview/rules/upstream/` are byte-identical copies of
   the Monetary Node project. To update them, copy the new upstream files, update the commit in `NOTICE` and
   `bitcoinmonetaryview/rules/__init__.py`, run the tests (parity and upstream suites) and note it in the
   changelog. Changed rules trigger an automatic rescan for users.
4. **Tests**: `python3 -m unittest discover -s tests -t .` must pass. Add tests for new behaviour.
5. **English** for code, UI, docs and commit messages.

## Translations

The dashboard speaks English, German, Spanish, French and Polish (the languages of StartOS). Every text is
English in `bitcoinmonetaryview/static/app.js` (wrapped in `_t("…")`) and in `index.html` (`data-i18n`); each
translation is one file, `bitcoinmonetaryview/static/i18n/<code>.js`, and the browser loads only the one it
needs. Messages the server sends (status line, activity, connection test) are translated there too, by exact
text or pattern; numbers in them are reformatted for the language.

- **Correcting a translation:** edit the text on the right-hand side in that language's file. Keep the
  `{placeholders}` exactly as they are. Native speakers' corrections are very welcome — the translations are new.
- **Texts that depend on a number** use `{n}` for it and can have one form per plural category instead of a
  single text, e.g. Polish `{"one": "{n} blok", "few": "{n} bloki", "many": "{n} bloków", "other": "{n} bloki"}`.
  The browser picks the form (`Intl.PluralRules`).
- **Adding a language:** copy a file to `i18n/<code>.js` and translate it, add the language to `LANGS` in
  `app.js` and the file to `STATIC_FILES` in `server.py`, and add it to `LANGS`/`PLURAL_FORMS` in
  `tests/test_i18n.py` and a `Dashboard…` class in `tests/test_browser.py`.

`tests/test_i18n.py` fails if a text or a server message has no translation in some language, a translation
has other `{placeholders}`, or a plural text lacks a form; `tests/test_browser.py` opens every page in every
language at desktop, tablet and phone width.

## Releases

1. Update the version in `bitcoinmonetaryview/__init__.py` and `pyproject.toml`, and the date in `CHANGELOG.md`.
2. Merge that change into `main`, then run the *Release image* workflow on `main` (Actions → *Release image* →
   *Run workflow*). It runs the tests, creates the tag `vX.Y.Z` and the GitHub release (that version's
   `CHANGELOG.md` section as "Highlights", then GitHub's list of merged pull requests), and publishes
   `ghcr.io/marksierra/bitcoinmonetaryview` for x86_64, ARM64 and RISC-V (tags `X.Y.Z`, `X.Y`, `latest`). The
   package is public. To refresh the notes of a published release only, run it with *notes only*. Pushing a tag
   `vX.Y.Z` by hand publishes the image too.
3. StartOS: Start9 builds and publishes the package only from
   [Start9-Community/bitcoinmonetaryview-startos](https://github.com/Start9-Community/bitcoinmonetaryview-startos).
   1. Sync `main` of [MarkSierra/BitcoinMonetaryView-startos](https://github.com/MarkSierra/BitcoinMonetaryView-startos)
      from that fork, then branch. Move the `upstream` submodule to the release, copy the `FROM` line of
      `upstream/Dockerfile`, and set `startos/versions/current.ts` to the final version (`X.Y.Z:0`) with release
      notes in all five languages (see the fork's `UPDATING.md` and Start9's packaging guide).
   2. Test build: on a second branch, one extra commit sets the version to `X.Y.Z:0-beta.N`; run the *Build*
      workflow on it and sideload the `.s9pk` (an update over the installed version, a clean install, tasks and
      actions, sign-in, health checks). `-beta.N` sorts before `X.Y.Z:0`, so the published build arrives as a
      normal update. Fixes go on the final-version branch; the first test build is `-beta.0`, the next
      `-beta.N+1`.
   3. Open the pull request to the fork from the final-version branch, never from a test branch. Each merge
      publishes to Community Beta; promotion to the Community Registry is requested with an issue on the fork.
4. Umbrel: in [umbrel-community-app-store](https://github.com/MarkSierra/umbrel-community-app-store), pin the
   new image by version and digest and bump `version` / `releaseNotes`; then the same in the official-store
   package via a pull request to `getumbrel/umbrel-apps`, after the *umbrelOS* workflow passed (see
   [docs/umbrel.md](docs/umbrel.md)).

By contributing you agree that your contribution is licensed under the AGPL-3.0-or-later.
