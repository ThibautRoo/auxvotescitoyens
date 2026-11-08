#!/usr/bin/env python3
"""Produit dist-apercu/ : copie de dist/ où tous les liens absolus (« /x ») deviennent
relatifs, pour prévisualiser le site sur un hébergement qui ne le sert pas à la racine
(aperçu Claude, par exemple). Le site réel (Scalingo) utilise dist/ tel quel."""
import re, shutil, sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
SRC, DST = RACINE / "dist", RACINE / "dist-apercu"
ABS = re.compile(r'(href|src|action)="/(?!/)')

if DST.exists():
    shutil.rmtree(DST)
shutil.copytree(SRC, DST)

for f in DST.rglob("*.html"):
    prof = len(f.relative_to(DST).parts) - 1
    pref = "../" * prof if prof else "./"
    html = f.read_text(encoding="utf-8")
    html = ABS.sub(lambda m: f'{m.group(1)}="{pref}', html)
    html = html.replace(f'{pref}"', f'{pref}index.html"')  # href="/" -> ./index.html
    # les liens de dossier « x/ » doivent pointer vers x/index.html
    html = re.sub(r'(href="(?:\.\./|\./)[^"#?]*?)/"', r'\1/index.html"', html)
    html = html.replace("<head>", f"<head><script>window.AVC_BASE={pref!r};</script>", 1)
    f.write_text(html, encoding="utf-8")

js = DST / "static" / "app.js"
code = js.read_text(encoding="utf-8")
code = code.replace('"/api/', '(window.AVC_BASE||"")+"/api/'.replace('+"/api/', '+"api/'))
code = code.replace('"/data/', '(window.AVC_BASE||"")+"data/')
js.write_text(code, encoding="utf-8")
print("dist-apercu/ généré")
