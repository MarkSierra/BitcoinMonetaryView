# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every translation covers every text of the dashboard and every message the server sends."""

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


LANGS = ("de", "es", "fr", "pl")
PLURAL_FORMS = {"de": {"one", "other"}, "es": {"one", "other"}, "fr": {"one", "other"},
                "pl": {"one", "few", "many", "other"}}


def lang_file(lang):
    return os.path.join(STATIC, "i18n", f"{lang}.js")


def lang_strings(lang):
    """The translation table of a language file, read by Node.js exactly as the browser reads it."""
    script = ("global.window = {}; require(process.argv[1]);"
              "process.stdout.write(JSON.stringify(window.BMV_I18N[process.argv[2]].strings));")
    out = subprocess.run(["node", "-e", script, lang_file(lang), lang], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def forms(value):
    """A translation is a text, or {one, few, many, other} for texts that depend on a number."""
    return list(value.values()) if isinstance(value, dict) else [value]


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Coverage(unittest.TestCase):
    def test_language_files_are_complete(self):
        """Every language file exists and the app offers exactly these languages."""
        self.assertEqual(sorted(f[:-3] for f in os.listdir(os.path.join(STATIC, "i18n"))), sorted(LANGS))
        js = open(os.path.join(STATIC, "app.js"), encoding="utf-8").read()
        offered = re.findall(r"^    (\w\w): \{ name:", js, re.M)
        self.assertEqual(sorted(offered), sorted(("en",) + LANGS))

    def test_every_ui_text_is_translated(self):
        keys = ui_keys()
        self.assertGreater(len(keys), 300)
        for lang in LANGS:
            with self.subTest(lang):
                tr = lang_strings(lang)
                self.assertEqual(sorted({k for k in keys if k not in tr}), [], "texts without a translation")
                self.assertEqual(sorted(set(tr) - set(keys)), [], "translations nothing uses any more")

    def test_placeholders_match(self):
        for lang in LANGS:
            for en, tr in lang_strings(lang).items():
                for form in forms(tr):
                    self.assertEqual(set(PLACEHOLDER.findall(en)), set(PLACEHOLDER.findall(form)), f"{lang}: {en}")

    def test_plural_forms(self):
        """Number-dependent texts have exactly the forms the language uses, and use {n} for the number."""
        for lang in LANGS:
            for en, tr in lang_strings(lang).items():
                if isinstance(tr, dict):
                    self.assertEqual(set(tr), PLURAL_FORMS[lang], f"{lang}: {en}")
                    self.assertIn("{n}", en, f"{lang}: {en}")

    def test_every_server_message_has_a_translation(self):
        """The fixed start of every message the server sends must appear in each language file,
        so a new message cannot be added without its translations."""
        calls = {"event": 1, "set": None, "add": 2, "append": 0, "Restart": 0, "reset_results": 0}
        starts = []
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
                else:
                    continue                                # e.g. "..." + "...", str(e)
                start = start.strip()
                if len(start) < 6 or not re.search(r"[a-z]{3}", start):
                    continue                                # e.g. a bare value
                starts.append((name, start))
        self.assertGreater(len(starts), 40)
        for lang in LANGS:
            plain = open(lang_file(lang), encoding="utf-8").read().replace("\\", "")   # regex escapes don't matter
            missing = [f"{name}: {start[:80]}" for name, start in starts if start[:40] not in plain]
            self.assertEqual(missing, [], lang)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ServerPatterns(unittest.TestCase):
    """Real messages through i18n.js's server() in Node.js."""

    SAMPLES_DE = {
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

    SAMPLES_ES = {
        "Loading spent-output filter: 48,000,000 entries":
            "Cargando el filtro de outputs gastados: 48.000.000 entradas",
        "Spent-output filter loaded from its saved copy (47,998,812 entries)":
            "Filtro de outputs gastados cargado desde su copia guardada (47.998.812 entradas)",
        "Analysing block 971,313 (2026-10-27) — 1.59 MB, 3,412 txs, 12.4 % spam":
            "Analizando el bloque 971.313 (2026-10-27): 1,59 MB, 3412 txs, 12,4 % de spam",
        "New block 971,474: Analysing block 971,474 (2026-10-27) — 1.71 MB, 2,000 txs, 13.0 % spam":
            "Bloque nuevo 971.474: Analizando el bloque 971.474 (2026-10-27): 1,71 MB, 2000 txs, 13,0 % de spam",
        "Restarting scanner: rescan requested":
            "Reiniciando el escáner: se pidió un nuevo escaneo",
        "Chain reorganisation: blocks 102–103 were replaced; results rolled back and will be re-analysed":
            "Reorganización de la cadena: los bloques 102–103 fueron sustituidos; los resultados se revirtieron y se volverán a analizar",
        "Sample pass: analysing every 100th block of blocks 0–970,313 (970,314 blocks) for a whole-chain estimate":
            "Muestreo: analizando uno de cada 100 bloques de los bloques 0–970.313 (970.314 bloques) para estimar toda la cadena",
        "Your node is still syncing (87.3 %). Scanning starts when it is done.":
            "Tu nodo aún se está sincronizando (87,3 %). El escaneo empieza cuando termine.",
        "Connected to mainnet node /Satoshi:31.1.0/ (REST on)":
            "Conectado al nodo /Satoshi:31.1.0/ de mainnet (REST activado)",
        "Fetched the latest block (1.59 MB) in 0.42 s ≈ 3.8 MB/s":
            "Último bloque (1,59 MB) obtenido en 0,42 s ≈ 3,8 MB/s",
        "Please wait 3 s before the next lookup.":
            "Espera 3 s antes de la siguiente consulta.",
        "under 6 months":
            "menos de 6 meses",
        "Up to date. Waiting for the next block.":
            "Al día. Esperando el siguiente bloque.",
        "Some message nobody translated":
            "Some message nobody translated",
    }

    SAMPLES_FR = {
        "Loading spent-output filter: 48,000,000 entries":
            "Chargement du filtre des outputs dépensés\u00a0: 48\u202f000\u202f000 entrées",
        "Spent-output filter loaded from its saved copy (47,998,812 entries)":
            "Filtre des outputs dépensés chargé depuis sa copie enregistrée (47\u202f998\u202f812 entrées)",
        "Analysing block 971,313 (2026-10-27) — 1.59 MB, 3,412 txs, 12.4 % spam":
            "Analyse du bloc 971\u202f313 (2026-10-27) — 1,59 MB, 3\u202f412 tx, 12,4 % de spam",
        "New block 971,474: Analysing block 971,474 (2026-10-27) — 1.71 MB, 2,000 txs, 13.0 % spam":
            "Nouveau bloc 971\u202f474\u00a0: Analyse du bloc 971\u202f474 (2026-10-27) — 1,71 MB, 2\u202f000 tx, 13,0 % de spam",
        "Restarting scanner: rescan requested":
            "Redémarrage de l’analyseur\u00a0: nouvelle analyse demandée",
        "Chain reorganisation: blocks 102–103 were replaced; results rolled back and will be re-analysed":
            "Réorganisation de la chaîne\u00a0: les blocs 102–103 ont été remplacés\u00a0; les résultats ont été annulés et seront réanalysés",
        "Sample pass: analysing every 100th block of blocks 0–970,313 (970,314 blocks) for a whole-chain estimate":
            "Échantillonnage\u00a0: analyse d’un bloc sur 100 parmi les blocs 0–970\u202f313 (970\u202f314 blocs) pour estimer toute la chaîne",
        "Your node is still syncing (87.3 %). Scanning starts when it is done.":
            "Votre nœud est encore en cours de synchronisation (87,3 %). L’analyse démarrera une fois terminé.",
        "Connected to mainnet node /Satoshi:31.1.0/ (REST on)":
            "Connecté au nœud mainnet /Satoshi:31.1.0/ (REST activé)",
        "Fetched the latest block (1.59 MB) in 0.42 s ≈ 3.8 MB/s":
            "Dernier bloc (1,59 MB) récupéré en 0,42 s ≈ 3,8 MB/s",
        "Please wait 3 s before the next lookup.":
            "Veuillez patienter 3 s avant la prochaine recherche.",
        "under 6 months":
            "moins de 6 mois",
        "Up to date. Waiting for the next block.":
            "À jour. En attente du prochain bloc.",
        "Some message nobody translated":
            "Some message nobody translated",
    }

    SAMPLES_PL = {
        "Loading spent-output filter: 48,000,000 entries":
            "Wczytuję filtr wydanych wyjść: 48\u00a0000\u00a0000 wpisów",
        "Spent-output filter loaded from its saved copy (47,998,812 entries)":
            "Filtr wydanych wyjść wczytany z zapisanej kopii (47\u00a0998\u00a0812 wpisów)",
        "Analysing block 971,313 (2026-10-27) — 1.59 MB, 3,412 txs, 12.4 % spam":
            "Analizuję blok 971\u00a0313 (2026-10-27) — 1,59 MB, 3412 tx, 12,4 % spamu",
        "New block 971,474: Analysing block 971,474 (2026-10-27) — 1.71 MB, 2,000 txs, 13.0 % spam":
            "Nowy blok 971\u00a0474: Analizuję blok 971\u00a0474 (2026-10-27) — 1,71 MB, 2000 tx, 13,0 % spamu",
        "Restarting scanner: rescan requested":
            "Ponowne uruchamianie skanera: zażądano ponownego skanowania",
        "Chain reorganisation: blocks 102–103 were replaced; results rolled back and will be re-analysed":
            "Reorganizacja łańcucha: bloki 102–103 zostały zastąpione; wyniki wycofano i zostaną przeanalizowane ponownie",
        "Sample pass: analysing every 100th block of blocks 0–970,313 (970,314 blocks) for a whole-chain estimate":
            "Próbkowanie: analizuję co 100. blok z bloków 0–970\u00a0313 (970\u00a0314 bloków), aby oszacować cały łańcuch",
        "Your node is still syncing (87.3 %). Scanning starts when it is done.":
            "Twój węzeł wciąż się synchronizuje (87,3 %). Skanowanie zacznie się, gdy skończy.",
        "Connected to mainnet node /Satoshi:31.1.0/ (REST on)":
            "Połączono z węzłem mainnet /Satoshi:31.1.0/ (REST włączony)",
        "Fetched the latest block (1.59 MB) in 0.42 s ≈ 3.8 MB/s":
            "Pobrano najnowszy blok (1,59 MB) w 0,42 s ≈ 3,8 MB/s",
        "Please wait 3 s before the next lookup.":
            "Odczekaj 3 s przed następnym wyszukiwaniem.",
        "under 6 months":
            "poniżej 6 miesięcy",
        "Up to date. Waiting for the next block.":
            "Aktualne. Czekam na następny blok.",
        "Some message nobody translated":
            "Some message nobody translated",
        "UTXO set: 1 entries":
            "Zbiór UTXO: 1 wpis",
        "UTXO set: 3 entries":
            "Zbiór UTXO: 3 wpisy",
        "Quick pass: analysing the latest 2 blocks (1–2)":
            "Szybki przebieg: analizuję ostatnie 2 bloki (1–2)",
    }

    def check(self, lang, samples):
        script = ("global.window = {}; require(process.argv[1]); const s = window.BMV_I18N[process.argv[2]].server;"
                  "process.stdout.write(JSON.stringify(JSON.parse(process.argv[3]).map(s)));")
        out = subprocess.run(["node", "-e", script, lang_file(lang), lang, json.dumps(list(samples))],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(dict(zip(samples, json.loads(out))), samples)

    def test_german(self):
        self.check("de", self.SAMPLES_DE)

    def test_spanish(self):
        self.check("es", self.SAMPLES_ES)

    def test_french(self):
        self.check("fr", self.SAMPLES_FR)

    def test_polish(self):
        self.check("pl", self.SAMPLES_PL)


if __name__ == "__main__":
    unittest.main()
