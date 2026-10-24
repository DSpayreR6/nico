"""Test: ein Begriff pro Sprache und Konzept.

Gemessen am 2026-10-24 hatte jede Sprache mehrere Wörter für denselben
Vorgang – deutsch drei, englisch vier, spanisch fünf (instantánea,
estado intermedio, punto de guardado, punto de control, punto de
restauración). Entstanden ist das, weil es keine Festlegung gab und jede
Sitzung neu entschieden hat.

Die Festlegung steht in `vorgaben.txt`, Abschnitt „Begriffe". Dieser Test hält
sie durch: ohne Prüfung verwässert ein Begriffsverzeichnis genauso, wie es den
`__TODO__`-Übersetzungen passiert ist.

`snapshot` ist dabei der wichtigste Fall: Das Wort ist für Snapper- und
Btrfs-Snapshots reserviert. Im Git-Zusammenhang benennt es sonst zwei
verschiedene Dinge gleich.

Läuft ohne Server und ohne Browser.
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
LANG = ROOT / "nico" / "static" / "lang"

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


# Schlüssel, die den Git-Vorgang meinen. Alles mit snapper/swap_in_snapshot im
# Namen gehört zum Dateisystem und darf "snapshot" verwenden.
def git_context(key: str) -> bool:
    if "snapper" in key.lower() or "swap_in_snapshot" in key:
        return False
    return (key.startswith(("git.", "rebuild.", "write.", "format."))
            or key in ("errors.ERR_NO_GIT_REPO", "errors.ERR_NO_REMOTE",
                       "admin.checkpoint", "admin.checkpointTitle",
                       "header.nixosSave", "header.nixosGitPush", "header.nixosGitPull"))


# Begriffe, die im Git-Zusammenhang nicht mehr vorkommen dürfen (pro Sprache).
FORBIDDEN = {
    # "Zwischenstand" steht absichtlich nicht hier: im Git-Start-Guard meint es
    # den unbeabsichtigten Zustand des Arbeitsverzeichnisses, nicht einen Commit.
    "de": [("Schnappschuss", "Sicherungspunkt"),
           ("Zwischenstand speichern", "Sicherungspunkt anlegen"),
           ("Zwischenstand konservieren", "Sicherungspunkt anlegen"),
           ("Wiederherstellungspunkt", "Sicherungspunkt")],
    "en": [("snapshot", "checkpoint"),
           ("restore point", "checkpoint"),
           ("safety commit", "checkpoint")],
    "es": [("punto de guardado", "punto de control"),
           ("instantánea", "punto de control"),
           ("punto de seguridad", "punto de control"),
           ("punto de restauración", "punto de control")],
    "fr": [("point de sauvegarde", "point de contrôle"),
           ("instantané", "point de contrôle"),
           ("point de restauration", "point de contrôle")],
    "ja": [("スナップショット", "チェックポイント"),
           ("保存ポイント", "チェックポイント"),
           ("セーブポイント", "チェックポイント"),
           ("セーフティコミット", "チェックポイント"),
           ("復元ポイント", "チェックポイント")],
    "ru": [("снимок", "контрольная точка"),
           ("точка сохранения", "контрольная точка"),
           ("точки сохранения", "контрольная точка"),
           ("точку сохранения", "контрольную точку"),
           ("точкой сохранения", "контрольной точкой")],
    "zh": [("保存点", "检查点"), ("快照", "检查点"), ("还原点", "检查点"),
           ("安全提交", "检查点")],
}

# Ausreißer, die überall falsch sind – auch außerhalb des Git-Zusammenhangs.
GLOBAL_FORBIDDEN = {
    "de": [("Ordner", "Verzeichnis")],
    "en": [("folder", "directory")],
    "es": [("carpeta", "directorio")],
    "fr": [("dossier", "répertoire")],
    "ja": [("フォルダ", "ディレクトリ")],
    "ru": [("папк", "каталог")],
    "zh": [("文件夹", "目录")],
}

print("1. Kein zweiter Begriff für den Commit-Vorgang")
for lang, rules in FORBIDDEN.items():
    data = json.loads((LANG / f"{lang}.json").read_text(encoding="utf-8"))
    for bad, good in rules:
        hits = [k for k, v in data.items()
                if isinstance(v, str) and git_context(k)
                and re.search(re.escape(bad), v, re.IGNORECASE)]
        check(f"{lang}: kein '{bad}' (→ {good})", not hits, hits[:4])

print("\n2. Verzeichnis heißt überall Verzeichnis")
for lang, rules in GLOBAL_FORBIDDEN.items():
    data = json.loads((LANG / f"{lang}.json").read_text(encoding="utf-8"))
    for bad, good in rules:
        hits = [k for k, v in data.items()
                if isinstance(v, str) and re.search(re.escape(bad), v, re.IGNORECASE)]
        check(f"{lang}: kein '{bad}' (→ {good})", not hits, hits[:4])

print("\n3. snapshot bleibt für Snapper und Btrfs reserviert")
en = json.loads((LANG / "en.json").read_text(encoding="utf-8"))
snap = [k for k, v in en.items() if isinstance(v, str) and "snapshot" in v.lower()]
check("alle snapshot-Stellen gehören zum Dateisystem",
      all(("snapper" in k.lower() or "swap_in_snapshot" in k) for k in snap), snap)
check("es gibt sie noch (sonst prüft der Test nichts)", len(snap) > 0, len(snap))

print("\n4. Fachbegriff steht an den Knöpfen in Klammern")
for lang in sorted(p.stem for p in LANG.glob("*.json")):
    data = json.loads((LANG / f"{lang}.json").read_text(encoding="utf-8"))
    only = data.get("rebuild.commitOnly", "")
    push = data.get("rebuild.commitPush", "")
    check(f"{lang}: commitOnly nennt (commit)", "commit" in only.lower(), only)
    check(f"{lang}: commitPush nennt (commit & push)",
          "commit" in push.lower() and "push" in push.lower(), push)

print("\n5. Der Eigenname wird nirgends übersetzt")
# Nur in Schlüsseln, die über Nix-Brix sprechen: "Block"/"块"/"блок" meint
# woanders gewöhnliche Code-Blöcke und darf dort stehen bleiben.
def brick_key(key: str) -> bool:
    k = key.lower()
    return "brix" in k or "brick" in k or key in ("import.brixInfo", "admin.importHint")

TRANSLATED = {
    "de": r"(?<!Nix-)\bBri[cx]k?s?\b",
    "en": r"(?<!Nix-)\bbri[cx]ks?\b|Nix bri[cx]k",
    "es": r"\bbloques?\b|(?<!Nix-)\bbricks?\b",
    "fr": r"\bblocs?\b|\bbriques?\b|(?<!Nix-)\bbricks?\b",
    "ja": r"ブリック|ブロック",
    "ru": r"\bблок\w*\b|кирпич\w*",
    "zh": r"砖块|积木",
}
for lang, pattern in TRANSLATED.items():
    data = json.loads((LANG / f"{lang}.json").read_text(encoding="utf-8"))
    hits = [k for k, v in data.items()
            if isinstance(v, str) and brick_key(k) and re.search(pattern, v, re.IGNORECASE)]
    check(f"{lang}: Nix-Brick/Nix-Brix unübersetzt", not hits, hits[:4])

print("\n6. Keine Doppelungen aus dem Umbau")
for lang in sorted(p.stem for p in LANG.glob("*.json")):
    data = json.loads((LANG / f"{lang}.json").read_text(encoding="utf-8"))
    hits = [k for k, v in data.items()
            if isinstance(v, str) and re.search(r"Nix-Bri[cx]k?\s*(?:Nix\b|Bri[cx])", v)]
    check(f"{lang}: kein 'Nix-Brick Nix' o. ä.", not hits, hits[:3])

print("\n7. 'Validator' ist ein Eigenname und bleibt unübersetzt")
# Jede Sprache hätte eine eigene Form (validación, validateur, валидатор,
# 検証, 校验). Nicht gemeint ist die semantische Prüfung durch Nix selbst
# (ERR_DRY_TIMEOUT_SEM) – die heißt zu Recht weiter so.
VALIDATOR_KEYS = ("header.nixosValidate", "admin.validation.title",
                  "admin.validation.runBtn", "admin.validation.settingsBtn",
                  "admin.validation.settingsTitle", "admin.validation.resultsTitle",
                  "admin.validation.saved", "rebuild.validateBtn", "rebuild.validateHint")
TRANSLATED_FUNC = {
    "de": r"Validierung", "en": r"validation", "es": r"validaci[oó]n|validador",
    "fr": r"validation|validateur", "ja": r"検証", "ru": r"валидац|проверк",
    "zh": r"校验",
}
for lang, pattern in TRANSLATED_FUNC.items():
    data = json.loads((LANG / f"{lang}.json").read_text(encoding="utf-8"))
    hits = [k for k in VALIDATOR_KEYS
            if isinstance(data.get(k), str) and re.search(pattern, data[k], re.IGNORECASE)]
    check(f"{lang}: Validator unübersetzt", not hits, hits)
    check(f"{lang}: Menüeintrag heißt Validator",
          data.get("header.nixosValidate") == "Validator", data.get("header.nixosValidate"))

print("\n8. Startknöpfe tragen den Namen ihrer Funktion")
# Der Knopf unter einem fehlgeschlagenen Rebuild hieß "Run check", die Funktion
# im Menü "Validation" – so findet niemand das eine über das andere.
for lang in sorted(p.stem for p in LANG.glob("*.json")):
    data = json.loads((LANG / f"{lang}.json").read_text(encoding="utf-8"))
    func = data.get("header.nixosValidate", "").strip()
    # Auf den Wortstamm prüfen, nicht auf das ganze Wort: flektierende Sprachen
    # beugen den Funktionsnamen im Satz ("Проверка" → "Запустить проверку"),
    # und das ist richtig so.
    stem = func[:max(4, len(func) - 2)].lower()
    for key in ("rebuild.validateBtn", "admin.validation.runBtn"):
        label = data.get(key, "")
        check(f"{lang}: {key} nennt '{func}'", stem and stem in label.lower(),
              f"{label!r} vs Stamm {stem!r}")

print("\n9. Nix-Brix ist der Plural, Nix-Brick der Singular")
de = json.loads((LANG / "de.json").read_text(encoding="utf-8"))
# "Nix-Bricks" ist als Genitiv Singular korrekt ("die Marker eines Nix-Bricks"),
# falsch ist es nur als Plural – deshalb wird nach Pluralwörtern davor gesucht.
plural_misuse = re.compile(r"(?:mehrere|alle|beide|die|zwei|drei|viele)\s+Nix-Bricks")
hits = [k for k, v in de.items() if isinstance(v, str) and plural_misuse.search(v)]
check("'Nix-Bricks' nicht als Plural verwendet", not hits, hits[:4])
check("kein 'Nix-Brixe' (doppelter Plural)",
      not any("Nix-Brixe" in str(v) for v in de.values()))

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
