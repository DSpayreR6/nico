"""Browser-Test: Sprachwechsel – keine __TODO__ sichtbar, CSS-Hinweis übersetzt.

Dazu seit 2026-11-01: Ein Fehlercode mit Daten muss am Ende ein lesbarer Satz
sein. Das Backend packt Code und Daten mit dem Unit Separator in einen String
(`git_manager._err`), `tErr()` teilt ihn wieder und füllt die Platzhalter der
Übersetzung. Geprüft wird das Ergebnis, nicht die Mechanik – ein Nutzer soll
keinen Code sehen und keine leeren Klammern.
"""
import os, pathlib, sys, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from nico import git_manager as _gm

PORT = 8798
app = create_app()
threading.Thread(target=lambda: app.run(port=PORT, threaded=True), daemon=True).start()
time.sleep(2)
opts = webdriver.ChromeOptions()
for f in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1600,1200"):
    opts.add_argument(f)
opts.binary_location = os.environ["CHROME_BIN"]
d = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=opts)

fails = []
def check(label, cond):
    print(("  OK   " if cond else "  FAIL ") + label)
    if not cond: fails.append(label)

EXPECT = {"de": "eingeklappt", "en": "collapsed", "es": "plegado",
          "fr": "replié", "ja": "折りたたみ中", "ru": "свёрнуто", "zh": "已折叠"}
try:
    d.get(f"http://127.0.0.1:{PORT}/"); time.sleep(4)
    for lang, word in EXPECT.items():
        d.execute_script("localStorage.setItem('nico_lang', arguments[0]);", lang)
        d.refresh(); time.sleep(3)
        var = d.execute_script(
            "return getComputedStyle(document.documentElement).getPropertyValue('--ni-collapsed-label');")
        check(f"{lang}: CSS-Hinweis = {var.strip()}", word in var)
        body = d.find_element(By.TAG_NAME, "body").text
        check(f"{lang}: kein __TODO__ in der Oberfläche", "__TODO__" not in body)
    print()
    # Ein Code mit Daten muss in jeder Sprache ein Satz werden.
    FAELLE = [
        (_gm._err(_gm.ERR_GIT_FAILED, "git push"), ["git push"]),
        (_gm._err(_gm.ERR_GIT_FAILED_OUT, "git commit", "error: pathspec did not match"),
         ["git commit", "error: pathspec did not match"]),
        (_gm._err(_gm.ERR_TOO_BIG_FOR_GIT, 50, "log.txt, dump.bin"),
         ["50", "log.txt, dump.bin"]),
        (_gm._err(_gm.ERR_PARTIALLY_RESTORED, "a.nix, b.nix"), ["a.nix, b.nix"]),
        (_gm.ERR_NO_UPSTREAM, []),
    ]
    for lang in EXPECT:
        d.execute_script("localStorage.setItem('nico_lang', arguments[0]);", lang)
        d.refresh(); time.sleep(2.5)
        for roh, muss in FAELLE:
            txt = d.execute_script("return tErr(arguments[0])", roh) or ""
            kennung = roh.split("\x1f")[0]
            check(f"{lang}: {kennung} wird ein Satz",
                  bool(txt) and kennung not in txt and "__TODO__" not in txt)
            check(f"{lang}: {kennung} ohne offene Platzhalter",
                  "{0}" not in txt and "{1}" not in txt)
            check(f"{lang}: {kennung} behält seine Daten",
                  all(str(m) in txt for m in muss))
            check(f"{lang}: {kennung} ohne Trennzeichen im Text",
                  "\x1f" not in txt)

    print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
finally:
    d.quit()
sys.exit(1 if fails else 0)
