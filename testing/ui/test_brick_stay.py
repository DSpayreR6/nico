"""Browser-Test: Nix-Brick bleibt beim Aufklappen und Bearbeiten stehen,
Farben schon beim Schreiben (2026-11-25)."""
import os, pathlib, sys, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8798
app = create_app()
threading.Thread(target=lambda: app.run(port=PORT, threaded=True), daemon=True).start()
time.sleep(2)

opts = webdriver.ChromeOptions()
for f in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1400,700"):
    opts.add_argument(f)
opts.binary_location = os.environ["CHROME_BIN"]
d = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=opts)

fails = []
def check(label, cond, info=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  ({info})" if info else ""))
    if not cond:
        fails.append(label)

LONG = "\n".join(f"  # line {i}\n  environment.variables.X{i} = \"{i}\";" for i in range(20))

def js_async(body, *args):
    return d.execute_async_script(
        "const done = arguments[arguments.length-1];"
        f"(async () => {{ {body} }})().then(done, e => done('ERR ' + e));", *args)

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)

    # A long brick at the end of configuration.nix, so the view can scroll.
    r = js_async("""
      let res = await csrfFetch('/api/brick', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name:'lang-brick', section:'TAIL', file:'configuration.nix'})});
      let data = await res.json(); if (data.error) return 'ERR ' + JSON.stringify(data);
      res = await csrfFetch('/api/brick/lang-brick', {method:'PATCH', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({content: arguments[0], file:'configuration.nix'})});
      data = await res.json(); if (data.error) return 'ERR ' + JSON.stringify(data);
      await _refreshPreviewForFile('configuration.nix'); return 'ok';""", LONG)
    check("langer Brick angelegt", r == "ok", r)
    time.sleep(1)

    head = lambda n: d.execute_script(
        "return document.querySelector(`.code-brix[data-brick-name='${arguments[0]}'] .code-brix-head`)", n)
    top  = lambda el: d.execute_script("return arguments[0].getBoundingClientRect().top", el)
    scroller = lambda el: d.execute_script("return _scrollParent(arguments[0])", el)

    print("\n1. Alles einklappen, Brick unten anklicken: Kopf bleibt stehen")
    d.find_element(By.ID, "collapse-all-btn").click(); time.sleep(1)
    names = d.execute_script("return [...document.querySelectorAll('#preview-configuration .code-brix')].map(e=>e.dataset.brickName)")
    print("   Brix:", names)
    # Open everything once, scroll the long brick to mid-screen, collapse again
    target = "lang-brick"
    h = head(target)
    sc = scroller(h)
    # Room below the content: at the very end of the scroll range the browser
    # clamps the position, and nothing can stay in place there.
    d.execute_script("arguments[0].style.paddingBottom = '2000px'", sc)
    d.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight - arguments[0].clientHeight - 2000", sc)
    time.sleep(0.3)
    y0 = top(h)
    d.execute_script("arguments[0].click()", h)
    time.sleep(1)
    h = head(target)
    y1 = top(h)
    check("Sektion aufgeklappt",
          d.execute_script("return !arguments[0].closest('.code-section').classList.contains('collapsed')", h))
    check("Brick-Kopf an derselben Stelle", abs(y1 - y0) < 2, f"{y0:.1f} → {y1:.1f}")

    print("\n2. Brick-Kopf wieder zuklappen: steht weiter")
    d.execute_script("arguments[0].click()", h); time.sleep(0.6)
    check("Kopf steht nach Zuklappen", abs(top(head(target)) - y1) < 2)
    d.execute_script("arguments[0].click()", head(target)); time.sleep(0.6)

    print("\n3. In der Mitte des langen Bricks klicken: Zeile bleibt, Cursor dort")
    wrap = d.execute_script("return document.querySelector(`.code-brix[data-brick-name='lang-brick']`)")
    nums = wrap.find_element(By.CSS_SELECTOR, ".line-nums")
    lh = d.execute_script("return parseFloat(getComputedStyle(arguments[0]).lineHeight)", nums)
    # Head above the visible area, row 15 roughly in the middle
    row = 15
    d.execute_script("""const n = arguments[0], sc = _scrollParent(n);
      sc.scrollTop += n.getBoundingClientRect().top + arguments[1]*arguments[2] - sc.getBoundingClientRect().top - 200;""",
      nums, row, lh)
    time.sleep(0.3)
    check("Brick-Kopf liegt oberhalb des Sichtbereichs",
          top(head(target)) < d.execute_script("return _scrollParent(arguments[0]).getBoundingClientRect().top", nums))
    row_y = top(nums) + row * lh
    pre = wrap.find_element(By.CSS_SELECTOR, "pre.code-view")
    # A real click event with coordinates; ActionChains would first scroll the
    # element's centre into view and move everything.
    d.execute_script("""const p = arguments[0], r = p.getBoundingClientRect();
      p.dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: r.left + 40, clientY: arguments[1]}));""",
      pre, row_y + lh / 2)
    time.sleep(0.5)
    ta = wrap.find_element(By.CSS_SELECTOR, "textarea.brix-inline-textarea")
    info = d.execute_script("""const ta = arguments[0], cs = getComputedStyle(ta);
      const line = ta.value.slice(0, ta.selectionStart).split('\\n').length - 1;
      return { line, focus: document.activeElement === ta,
               y: ta.getBoundingClientRect().top + ta.clientTop + parseFloat(cs.paddingTop) + line*parseFloat(cs.lineHeight),
               sh: ta.scrollHeight, ch: ta.clientHeight };""", ta)
    check("Editor hat den Fokus", info["focus"])
    check("Cursor in der angeklickten Zeile", info["line"] == row - 1, f"Zeile {info['line']}, erwartet {row-1}")
    check("Zeile steht an derselben Stelle", abs(info["y"] - row_y) < 2, f"{row_y:.1f} → {info['y']:.1f}")
    check("Editor so hoch wie der Text (kein eigener Rollbalken)", info["sh"] <= info["ch"] + 1, f"{info['sh']}/{info['ch']}")

    print("\n4. Farben beim Schreiben")
    view = wrap.find_element(By.CSS_SELECTOR, ".text-editor-view code")
    check("Farbschicht trägt Prism-Tokens",
          d.execute_script("return arguments[0].querySelectorAll('.token').length > 0", view))
    ta.send_keys("services.foo.enable = true;\n")
    time.sleep(0.3)
    check("Getippter Text erscheint gefärbt",
          d.execute_script("""return [...arguments[0].querySelectorAll('.token.boolean')]
                              .some(t => t.textContent === 'true')""", view))
    same = d.execute_script("""const ta = arguments[0], v = ta.parentElement.querySelector('.text-editor-view');
      const a = getComputedStyle(ta), b = getComputedStyle(v);
      return [a.paddingTop===b.paddingTop, a.paddingLeft===b.paddingLeft, a.lineHeight===b.lineHeight,
              a.fontSize===b.fontSize, ta.getBoundingClientRect().top===v.getBoundingClientRect().top];""", ta)
    check("Schichten deckungsgleich (Abstand, Zeilenhöhe, Schrift, Lage)", all(same), str(same))
    info2 = d.execute_script("return {sh: arguments[0].scrollHeight, ch: arguments[0].clientHeight}", ta)
    check("Editor wächst mit", info2["sh"] <= info2["ch"] + 1)

    print("\n5. Abbrechen: Kopf bleibt, Ansicht wieder normal")
    hy = top(head(target))
    d.execute_script("arguments[0].click()", wrap.find_element(By.CSS_SELECTOR, ".brix-edit-actions .btn-secondary")); time.sleep(0.3)
    check("Editor zu", not wrap.find_elements(By.CSS_SELECTOR, ".brix-inline-editor"))
    check("Kopf steht", abs(top(head(target)) - hy) < 2)

    print("\n6. Bearbeiten und speichern: Kopf bleibt, Inhalt geschrieben")
    d.execute_script("arguments[0].click()", wrap.find_element(By.CSS_SELECTOR, "pre.code-view"))
    time.sleep(0.3)
    ta = wrap.find_element(By.CSS_SELECTOR, "textarea.brix-inline-textarea")
    ta.send_keys("# neu\n")
    hy = top(head(target))
    d.execute_script("arguments[0].click()", wrap.find_element(By.CSS_SELECTOR, ".brix-edit-actions .btn-primary")); time.sleep(1.5)
    check("Kopf steht nach Speichern", abs(top(head(target)) - hy) < 2, f"{hy:.1f} → {top(head(target)):.1f}")
    content = js_async("const r = await csrfFetch('/api/file?path=configuration.nix'); return (await r.json()).content;")
    check("gespeichert", "# neu" in content)

    print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
finally:
    d.quit()
sys.exit(1 if fails else 0)
