"""Test: ein Begriff pro Sprache und Konzept.

Gemessen am 2026-10-24 hatte jede Sprache mehrere Wörter für denselben
Vorgang – deutsch drei, englisch vier, spanisch fünf (instantánea,
estado intermedio, punto de guardado, punto de control, punto de
restauración). Entstanden ist das, weil es keine Festlegung gab und jede
Sitzung neu entschieden hat.

Die Festlegung steht in `vorgaben.txt`, Abschnitt „Begriffe". Dieser Test hält
sie durch: ohne Prüfung verwässert ein Begriffsverzeichnis genauso, wie es den
`__TODO__`-Übersetzungen passiert ist.

Seit 2026-11-01 auch: eine Config, die jemand zum Ausprobieren anlegt, heisst
nach aussen `test config`. Intern darf sie weiter "Wegwerf-Config" heissen -
geprueft wird deshalb nur, was der Nutzer zu sehen bekommt.

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

print("\n10. Eine Config zum Ausprobieren heisst nach aussen 'test config'")
# Festlegung vom 2026-11-01 (Martin): "toy" und "throwaway" klingen
# abwertend - was ein Nutzer sich zum Ausprobieren anlegt, soll nicht als
# wertlos bezeichnet werden. Intern bleibt "Wegwerf-Config" erlaubt, deshalb
# prueft dieser Abschnitt nur, was nach aussen geht: die Sprachdateien, die
# README und die Hilfe.
AUSSEN_VERBOTEN = ["toy config", "throwaway config", "dummy config",
                   "Spielkonfiguration", "Wegwerf-Config"]

for p_lang in sorted(LANG.glob("*.json")):
    data = json.loads(p_lang.read_text(encoding="utf-8"))
    for bad in AUSSEN_VERBOTEN:
        hits = [k for k, v in data.items()
                if isinstance(v, str) and re.search(re.escape(bad), v, re.IGNORECASE)]
        check(f"{p_lang.stem}: kein '{bad}'", not hits, hits[:3])

for rel in ("README.md", "nico/templates/help.html"):
    f = ROOT / rel
    if not f.exists():
        continue
    txt = f.read_text(encoding="utf-8")
    for bad in AUSSEN_VERBOTEN:
        check(f"{rel}: kein '{bad}'", not re.search(re.escape(bad), txt, re.IGNORECASE))

# Und die Gegenprobe: in internen Dokumenten darf es stehen bleiben, der Test
# soll dort nichts erzwingen.
intern = (ROOT / "testing" / "ui" / "README.md")
if intern.exists():
    check("intern bleibt 'Wegwerf-Config' unangetastet erlaubt",
          "Wegwerf-Config" in intern.read_text(encoding="utf-8"))

print("\n11. Zeitmaschine ist das Werkzeug, Historie die Datenstruktur")
# Festlegung vom 2026-11-07 (vorgaben.txt, Begriffe). Bis dahin hieß der
# Bereich unter dem Tab "Zeitmaschine" in fünf Sprachen "Git-Historie &
# Rollback", und im deutschen Text stand dreimal das englische "History".
ZEITMASCHINE = {"de": "Zeitmaschine", "en": "Time machine", "es": "Máquina del tiempo",
                "fr": "Machine à remonter le temps", "ja": "タイムマシン",
                "ru": "Машина времени", "zh": "时间机器"}
# Since 2026-11-25 there is no area title "Git / ‹Zeitmaschine›" any more:
# the time machine is a tab of its own, the title only repeated it. The parts
# of the Config side carry lay term + technical term (vorgaben.txt).
for lang, term in ZEITMASCHINE.items():
    data = json.loads((LANG / f"{lang}.json").read_text(encoding="utf-8"))
    tab = data.get("admin.tab.zeitmaschine", "")
    check(f"{lang}: Tab heißt '{term}'", tab == term, tab)
    check(f"{lang}: kein doppelter Bereichstitel mehr", "admin.gitTitle" not in data)
    for key in ("admin.gitLogTitle", "diff.title", "admin.gitignore.title"):
        val = data.get(key, "")
        check(f"{lang}: {key} mit Fachbegriff in Klammern", "(" in val or "（" in val, val)
        check(f"{lang}: {key} ohne 'rollback'", "rollback" not in val.lower(), val)
hits = [k for k, v in de.items()
        if isinstance(v, str) and re.search(r"History|Git-Log", v)]
check("de: kein 'History'/'Git-Log' (→ Historie bzw. Sicherungspunkte)", not hits, hits[:4])
idx = (ROOT / "nico" / "templates" / "index.html").read_text(encoding="utf-8")
check("index.html: Ersatztext ohne 'Git-History'", "Git-History" not in idx)

print("\n12. Textmodus statt Raw-Modus (Entscheidung 2026-11-20)")
# Laienbegriff, übersetzt. Schlüsselnamen (sidebar.rawSave …) sind intern und
# bleiben; geprüft werden nur die Texte.
RAW = {
    "de": r"\bRaw\b|Rohmodus|Texteditor", "en": r"\braw\b|text editor",
    "es": r"\braw\b|editor de texto", "fr": r"\braw\b|\bbrut\b|éditeur de texte",
    "ja": r"[Rr]aw|テキストエディタ", "ru": r"\braw\b|текстов\w+ редактор",
    "zh": r"[Rr]aw|原始模式|原始编辑器|文本编辑器",
}
TEXT_MODE = {"de": "Textmodus", "en": "text mode", "es": "modo texto", "fr": "mode texte",
             "ja": "テキストモード", "ru": "текстов", "zh": "文本模式"}
for lang, pattern in RAW.items():
    data = json.loads((LANG / f"{lang}.json").read_text(encoding="utf-8"))
    hits = [k for k, v in data.items() if isinstance(v, str) and re.search(pattern, v, re.IGNORECASE)]
    check(f"{lang}: kein Raw-Begriff in der Oberfläche", not hits, hits[:4])
    check(f"{lang}: Einstellung nennt den Textmodus",
          TEXT_MODE[lang].lower() in data.get("admin.settings.panelDefault", "").lower(),
          data.get("admin.settings.panelDefault"))

print("\n13. Rollback nur auf der System-Seite (Entscheidung 2026-11-21)")
# Zeitmaschine = Oberbegriff mit zwei Seiten. Config: Sicherungspunkte
# (Commits), Aktion Wiederherstellen (Restore) - so heißt es in git
# (`git restore --source`). System: Systemstände (Generationen), Rollback -
# so heißt es in Nix (`nixos-rebuild --rollback`). Bis 2026-11-21 sagte die
# Config-Seite "Rollback erfolgreich" und "Zurücksetzen".
ROLLBACK = {"de": r"rollback", "en": r"rollback|roll back", "es": r"rollback",
            "fr": r"rollback", "ja": r"ロールバック", "ru": r"откат", "zh": r"回滚"}
RESTORE = {"de": "Wiederherstellen", "en": "Restore", "es": "Restaurar", "fr": "Restaurer",
           "ja": "復元", "ru": "Восстановить", "zh": "恢复"}


def system_side(key: str) -> bool:
    """Keys of the system generations (Zeitmaschine → System)."""
    return key.startswith(("generations.", "admin.tm.system"))


for lang, pattern in ROLLBACK.items():
    data = json.loads((LANG / f"{lang}.json").read_text(encoding="utf-8"))
    hits = [k for k, v in data.items() if isinstance(v, str) and not system_side(k)
            and re.search(pattern, v, re.IGNORECASE)]
    check(f"{lang}: 'Rollback' nur bei den Systemständen", not hits, hits[:4])
    check(f"{lang}: Knopf der Sicherungspunkte heißt '{RESTORE[lang]}'",
          data.get("admin.rollbackBtn") == RESTORE[lang], data.get("admin.rollbackBtn"))

print("\n14. Versionierung statt 'Git' (Entscheidung 2026-11-25)")
# Lay term first, technical term in brackets. "git" stays only where the
# program itself, a command or a file name is meant – whoever uses Codeberg,
# GitLab or an own server uses git as well, but need not know the tool.
GIT_OK = re.compile(r"\((?:git|Git)\)|（git）|\.gitignore|\bgit (?:pull|rebase|merge|push)\b")
GIT_KEYS_OK = {
    "pkg.manualPlaceholder",       # the package called git
    "errors.ERR_GIT_NOT_FOUND",    # the program is missing
    "git.notInstalled",            # the program is missing
    "errors.ERR_INSIDE_OTHER_REPO",  # an outer repository, named as such
}
VERSIONING = {"de": "Versionierung", "en": "version control", "es": "control de versiones",
              "fr": "gestion de versions", "ja": "バージョン管理", "ru": "контроль версий",
              "zh": "版本控制"}
for lang, term in VERSIONING.items():
    data = json.loads((LANG / f"{lang}.json").read_text(encoding="utf-8"))
    hits = [k for k, v in data.items() if isinstance(v, str) and k not in GIT_KEYS_OK
            and re.search(r"\bgit\b", GIT_OK.sub("", v), re.IGNORECASE)]
    check(f"{lang}: 'Git' nur in Klammern, Befehlen und Dateinamen", not hits, hits[:6])
    check(f"{lang}: Rubrik heißt '{term} (git)'",
          term.lower() in data.get("admin.settings.rubrikGit", "").lower(),
          data.get("admin.settings.rubrikGit"))

print("\n15. Dry-Run, Rebuild und Sicherungspunkt (Commit) (Entscheidung 2026-11-26)")
# Fachbegriffe von Nix (Kategorie 2): in allen Sprachen englisch. Der Knopf
# hieß "Dry-Run", sein Fenster "Syntax-Prüfung" – ein Rest aus der ersten
# Fassung, als nur die Syntax geprüft wurde (Martin, VM-Test). Erlaubt bleibt
# "Syntax" dort, wo wirklich nur die Syntax geprüft wurde oder ein
# Syntaxfehler gemeint ist. Der Menüeintrag, der einen Sicherungspunkt
# anlegt, hieß "Konfiguration schreiben" – geschrieben wird aber immer.
OLD_DRY = {"de": r"Syntax-Prüfung|Syntax vor dem", "en": r"[Ss]yntax check",
           "es": r"verificación de sintaxis", "fr": r"vérification syntax",
           "ja": r"構文チェック|ドライラン", "ru": r"проверк\w+ синтаксиса", "zh": r"语法检查"}
DRY_KEYS_OK = {"errors.ERR_DRY_OK_SYNTAX"}   # here only the syntax was checked
for lang, pattern in OLD_DRY.items():
    data = json.loads((LANG / f"{lang}.json").read_text(encoding="utf-8"))
    hits = [k for k, v in data.items() if isinstance(v, str) and k not in DRY_KEYS_OK
            and re.search(pattern, v)]
    check(f"{lang}: kein alter Begriff für den Dry-Run", not hits, hits[:6])
    for key in ("dryrun.title", "header.nixosDryRun"):
        check(f"{lang}: {key} heißt Dry run", "dry" in data.get(key, "").lower(), data.get(key))
    for key in ("header.nixosRebuild", "admin.rebuild"):
        check(f"{lang}: {key} heißt Rebuild", data.get(key) == "Rebuild", data.get(key))
    check(f"{lang}: Menü und Fenster heißen gleich (Sicherungspunkt)",
          data.get("header.nixosSave") == data.get("write.title")
          and "commit" in data.get("header.nixosSave", "").lower(), data.get("header.nixosSave"))

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
