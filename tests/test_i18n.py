# SPDX-License-Identifier: AGPL-3.0-or-later
"""The German translation covers every text of the dashboard and every message the server sends."""

import ast
import html
import json
import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "bitcoinmonetaryview", "static")
LIT = r'"(?:[^"\\]|\\.)*"'
PLACEHOLDER = re.compile(r"\{\w+\}")


def ui_keys():
    """Every English text app.js passes to _t() and every data-i18n text of index.html."""
    js = open(os.path.join(STATIC, "app.js"), encoding="utf-8").read()
    keys = []
    for m in re.finditer(r"_t\(\s*(" + LIT + r"(?:\s*\+\s*" + LIT + r")*)", js):
        keys.append("".join(json.loads(p) for p in re.findall(LIT, m.group(1))))
    # _t() with a variable: the values it can take
    keys += ["quick", "sample"]                       # _t(st.phase) in the status bar
    keys += ["default", "file", "env", "cli"]         # _t(x.source) on the Connection page
    page = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
    for m in re.finditer(r"<[^>]*\bdata-i18n\b(?!-)[^>]*>([^<]*)<", page):
        keys.append(html.unescape(m.group(1).strip()))
    for m in re.finditer(r'<[^>]*data-i18n-attr="([^"]*)"[^>]*>', page):
        for a in m.group(1).split():
            v = re.search(r"\s" + re.escape(a) + r'="([^"]*)"', m.group(0))
            if v:
                keys.append(html.unescape(v.group(1)))
    return keys


def de_strings():
    src = open(os.path.join(STATIC, "i18n.js"), encoding="utf-8").read()
    block = src[src.index("const strings = {"):src.index("  };", src.index("const strings = {"))]
    out = {}
    for line in block.splitlines():
        m = re.match(r"\s*(" + LIT + r"): (" + LIT + r"),?$", line)
        if m:
            out[json.loads(m.group(1))] = json.loads(m.group(2))
    return out


class Coverage(unittest.TestCase):
    def test_every_ui_text_is_translated(self):
        de = de_strings()
        keys = ui_keys()
        self.assertGreater(len(keys), 300)
        self.assertEqual(sorted({k for k in keys if k not in de}), [], "texts without a German translation")
        self.assertEqual(sorted(set(de) - set(keys)), [], "translations nothing uses any more")

    def test_placeholders_match(self):
        for en, de in de_strings().items():
            self.assertEqual(set(PLACEHOLDER.findall(en)), set(PLACEHOLDER.findall(de)), en)

    def test_every_server_message_has_a_translation(self):
        """The fixed start of every message the server sends must appear in i18n.js, so a new
        message cannot be added without a German version."""
        src = open(os.path.join(STATIC, "i18n.js"), encoding="utf-8").read()
        plain = src.replace("\\", "")                   # regex escapes don't matter for this check
        calls = {"event": 1, "set": None, "add": 2, "append": 0, "Restart": 0, "reset_results": 0}
        missing = []
        for name in ("scanner.py", "diagnostics.py", "server.py", "config.py", "analytics.py"):
            tree = ast.parse(open(os.path.join(ROOT, "bitcoinmonetaryview", name), encoding="utf-8").read())
            texts = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                    if fn in calls:
                        args = list(node.args)
                        if fn == "set":
                            args = [k.value for k in node.keywords if k.arg == "detail"]
                        elif calls[fn] is not None:
                            args = args[calls[fn]:]
                        texts += args
                if isinstance(node, ast.Dict):            # {"error": "..."} answers of the server
                    for k, v in zip(node.keys, node.values):
                        if isinstance(k, ast.Constant) and k.value == "error":
                            texts.append(v)
                if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in ("PHASE_LABELS", "AGE_BANDS"):
                    texts += [c for c in ast.walk(node.value) if isinstance(c, ast.Constant) and isinstance(c.value, str)
                              and " " in c.value or (isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value[:1].isupper())]
            for t in texts:
                if isinstance(t, ast.Constant) and isinstance(t.value, str):
                    start = t.value
                elif isinstance(t, ast.JoinedStr):
                    start = ""
                    for part in t.values:
                        if not isinstance(part, ast.Constant):
                            break
                        start += part.value
                elif isinstance(t, ast.BinOp):              # "..." + "..." (implicit concatenation is a Constant)
                    continue
                else:
                    continue
                start = start.strip()
                if len(start) < 6 or not re.search(r"[a-z]{3}", start):
                    continue                                # e.g. str(e), a bare value
                probe = start[:40]
                if probe not in plain:
                    missing.append(f"{name}: {start[:80]}")
        self.assertEqual(missing, [])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ServerPatterns(unittest.TestCase):
    """Real messages through i18n.js's server() in Node.js."""

    SAMPLES = {
        "Loading spent-output filter: 48,000,000 entries": "Lade Filter für ausgegebene Outputs: 48.000.000 Einträge",
        "Spent-output filter loaded from its saved copy (47,998,812 entries)":
            "Filter für ausgegebene Outputs aus der gespeicherten Kopie geladen (47.998.812 Einträge)",
        "Analysing block 971,313 (2026-10-27) — 1.59 MB, 3,412 txs, 12.4 % spam":
            "Analysiere Block 971.313 (2026-10-27) – 1,59 MB, 3.412 Txs, 12,4 % Spam",
        "New block 971,474: Analysing block 971,474 (2026-10-27) — 1.71 MB, 2,000 txs, 13.0 % spam":
            "Neuer Block 971.474: Analysiere Block 971.474 (2026-10-27) – 1,71 MB, 2.000 Txs, 13,0 % Spam",
        "Restarting scanner: rescan requested": "Scanner startet neu: neuer Scan angefordert",
        "Chain reorganisation: blocks 102–103 were replaced; results rolled back and will be re-analysed":
            "Reorganisation der Chain: Blöcke 102–103 wurden ersetzt; die Ergebnisse wurden zurückgerollt und werden neu analysiert",
        "Sample pass: analysing every 100th block of blocks 0–970,313 (970,314 blocks) for a whole-chain estimate":
            "Stichprobe: analysiere jeden 100. Block der Blöcke 0–970.313 (970.314 Blöcke) für eine Schätzung der gesamten Chain",
        "Your node is still syncing (87.3 %). Scanning starts when it is done.":
            "Dein Node synchronisiert noch (87,3 %). Das Scannen startet, sobald er fertig ist.",
        "Connected to mainnet node /Satoshi:31.1.0/ (REST on)": "Verbunden mit mainnet-Node /Satoshi:31.1.0/ (REST an)",
        "Fetched the latest block (1.59 MB) in 0.42 s ≈ 3.8 MB/s": "Neuesten Block (1,59 MB) in 0,42 s abgerufen ≈ 3,8 MB/s",
        "Please wait 3 s before the next lookup.": "Bitte warte 3 s bis zur nächsten Abfrage.",
        "under 6 months": "unter 6 Monaten",
        "Up to date. Waiting for the next block.": "Auf dem neuesten Stand. Warte auf den nächsten Block.",
        "Some message nobody translated": "Some message nobody translated",
    }

    def test_samples(self):
        script = ("global.window = {}; require(process.argv[1]); const s = window.BMV_I18N.de.server;"
                  "process.stdout.write(JSON.stringify(JSON.parse(process.argv[2]).map(s)));")
        out = subprocess.run(["node", "-e", script, os.path.join(STATIC, "i18n.js"), json.dumps(list(self.SAMPLES))],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(dict(zip(self.SAMPLES, json.loads(out))), self.SAMPLES)


if __name__ == "__main__":
    unittest.main()
