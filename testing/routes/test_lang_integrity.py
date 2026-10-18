"""Test: Sprachdateien und die Platzhalter im Code passen zusammen.

Fängt die Fehler ab, die sonst erst in der Oberfläche auffallen – und das meist
erst, wenn ein fremdsprachiger Nutzer sie sieht:

  * ein Schlüssel fehlt in einer Sprache (t() zeigt dann den Schlüsselnamen)
  * ein `__TODO__` ist stehengeblieben (t() zeigt es wörtlich an)
  * ein Schlüssel steht doppelt in derselben Datei (der letzte gewinnt still)
  * ein Text erwartet {3}, der Code liefert aber nur zwei Werte – dann steht
    "{3}" wörtlich in der Meldung

Läuft ohne Server und ohne Browser.
"""
import ast, collections, json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
LANG = ROOT / "nico" / "static" / "lang"

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  ({got})"))
    if not cond:
        fails.append(label)

def placeholders(text: str) -> set:
    return set(re.findall(r"\{\d+\}", text or ""))

print("1. Sprachdateien")
data, raw = {}, {}
for p in sorted(LANG.glob("*.json")):
    raw[p.stem] = p.read_text(encoding="utf-8")
    try:
        data[p.stem] = json.loads(raw[p.stem])
    except json.JSONDecodeError as exc:
        check(f"{p.stem}: gültiges JSON", False, exc)
if "de" not in data:
    print("ABBRUCH: de.json fehlt oder ist kaputt")
    sys.exit(2)

de = data["de"]
for code, d in sorted(data.items()):
    keys = re.findall(r'^\s*"([^"]+)"\s*:', raw[code], re.M)
    dupes = [k for k, n in collections.Counter(keys).items() if n > 1]
    missing = sorted(set(de) - set(d))
    extra = sorted(set(d) - set(de))
    todo = sorted(k for k, v in d.items() if v == "__TODO__")
    ph = sorted(k for k in de if placeholders(de[k]) != placeholders(d.get(k, "")))
    check(f"{code}: {len(d)} Schlüssel, keine Dubletten", not dupes, dupes[:3])
    check(f"{code}: keine fehlenden Schlüssel", not missing, missing[:3])
    check(f"{code}: keine überzähligen Schlüssel", not extra, extra[:3])
    check(f"{code}: keine __TODO__-Reste", not todo, todo[:3])
    check(f"{code}: Platzhalter wie in de", not ph, ph[:3])

print("\n2. Platzhalter gegen die Parameter im Validator")
tree = ast.parse((ROOT / "nico" / "validator.py").read_text(encoding="utf-8"))
sites = 0
for node in ast.walk(tree):
    if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Finding"):
        continue
    kw = {k.arg: k.value for k in node.keywords}
    for key_arg, params_arg in (("message_key", "params"), ("detail_key", "detail_params")):
        key_node = kw.get(key_arg)
        if not isinstance(key_node, ast.Constant) or not key_node.value:
            continue
        sites += 1
        key = key_node.value
        params = kw.get(params_arg)
        have = len(params.elts) if isinstance(params, ast.List) else 0
        want = len(placeholders(de.get(key, "")))
        if key not in de:
            check(f"Zeile {node.lineno}: {key} existiert", False, "fehlt in de.json")
        elif want != have:
            check(f"Zeile {node.lineno}: {key} – Text {want}, Code {have}", False)
print(f"  {sites} Schlüssel-Verwendungen geprüft")

print("\n3. Jeder im Validator genannte Schlüssel existiert")
# Fängt auch zusammengesetzte Schlüssel (f"...{case}"), die Abschnitt 2 nicht
# als Konstante sieht – dort fiele ein Tippfehler erst in der Oberfläche auf.
src = (ROOT / "nico" / "validator.py").read_text(encoding="utf-8")
prefixes = set(re.findall(r'f"(validator\.[fdr]\.[A-Za-z0-9_.]+)\{', src))
plain = set(re.findall(r'"(validator\.[fdr]\.[A-Za-z0-9_.]+)"', src)) - prefixes
for key in sorted(plain):
    check(f"{key} existiert", key in de, "fehlt in de.json")
for prefix in sorted(prefixes):
    matches = [k for k in de if k.startswith(prefix)]
    check(f"{prefix}* hat Varianten in de.json", bool(matches), "keine gefunden")

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}"))
sys.exit(1 if fails else 0)
