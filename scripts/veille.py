#!/usr/bin/env python3
"""Veille hebdomadaire des sources des candidats.

Ne modifie JAMAIS les données candidates (data/). Écrit uniquement :
  - veille/AAAA-MM-JJ.md   : le rapport
  - veille/etat.json        : mémoire de la veille (empreintes de texte, liens vus)
  - un corps d'issue (option --issue-body)

Usage local :  python3 scripts/veille.py [--data DIR] [--out DIR] [--issue-body FICHIER]
Aucune dépendance (bibliothèque standard).
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

UA = "veille-auxvotescitoyens/1.0 (contrôle hebdomadaire des sources publiques)"
TIMEOUT = 30
SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form",
        "svg", "template", "iframe", "button", "select"}
MOTS_CLES = re.compile(r"programme|proposition|projet|manifeste", re.I)
CHEMINS = ["/programme", "/projet", "/propositions", "/notre-programme", "/notre-projet"]


# ---------------------------------------------------------------- réseau
def fetch(url, tentatives=2):
    """Renvoie (statut, octets, content_type, erreur)."""
    err = None
    for i in range(tentatives):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, r.read(), r.headers.get("Content-Type", ""), None
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
            if e.code in (403, 404, 410):
                return e.code, b"", "", err
        except Exception as e:  # réseau, DNS, TLS, délai
            err = f"{type(e).__name__}: {e}"[:160]
        time.sleep(2)
    return None, b"", "", err


def decode(octets, content_type):
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    for enc in ([m.group(1)] if m else []) + ["utf-8"]:
        try:
            return octets.decode(enc)
        except Exception:
            pass
    return octets.decode("utf-8", "replace")


# ---------------------------------------------------------------- extraction
class Extracteur(HTMLParser):
    """Texte visible, hors menus/pieds de page ; privilégie <main> ou <article>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.main = 0
        self.tout, self.principal, self.liens = [], [], []
        self._href = None
        self._txt = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP:
            self.skip += 1
        if tag in ("main", "article"):
            self.main += 1
        if tag == "a" and a.get("href"):
            self._href, self._txt = a["href"], []
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section"):
            self._ajoute("\n")

    def handle_endtag(self, tag):
        if tag in SKIP and self.skip:
            self.skip -= 1
        if tag in ("main", "article") and self.main:
            self.main -= 1
        if tag == "a" and self._href is not None:
            self.liens.append((self._href, " ".join("".join(self._txt).split())))
            self._href = None
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "tr", "section"):
            self._ajoute("\n")

    def handle_data(self, data):
        if self._href is not None:
            self._txt.append(data)
        if not self.skip:
            self._ajoute(data)

    def _ajoute(self, s):
        self.tout.append(s)
        if self.main:
            self.principal.append(s)


def lignes(chaine):
    out = []
    for l in chaine.splitlines():
        l = " ".join(l.split())
        if l:
            out.append(l)
    return out


def extraire(html):
    p = Extracteur()
    p.feed(html)
    brut = "".join(p.principal) if "".join(p.principal).strip() else "".join(p.tout)
    return lignes(brut), p.liens


def norm(s):
    s = unicodedata.normalize("NFKD", s.replace("’", "'").replace("\xa0", " "))
    return "".join(c for c in s if not unicodedata.combining(c)).casefold().strip()


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------- programmes
def verifie_source(url, doc, texte_archive):
    """Contrôle une URL d'un document. Renvoie un dict de résultat."""
    res = {"url": url}
    est_pdf = url.lower().split("?")[0].endswith(".pdf")
    statut, octets, ctype, err = fetch(url)
    if err and not octets:
        res.update(etat="injoignable", detail=err)
        return res
    res["http"] = statut
    if est_pdf or "pdf" in ctype.lower():
        h = sha(octets)
        ref = doc.get("sha256_original")
        res["sha256"] = h
        res.update(etat="inchange" if ref and h == ref else "change",
                   detail="empreinte PDF identique" if ref and h == ref else
                   "empreinte PDF différente de sha256_original" if ref else "pas d'empreinte de référence")
        return res
    if "docs.google.com/document" in url:
        # export texte : l'empreinte d'origine porte sur ce fichier
        u = re.sub(r"/(edit|view|pub).*$", "", url.split("?")[0]) + "/export?format=txt"
        s2, o2, c2, e2 = fetch(u)
        if e2 and not o2:
            res.update(etat="injoignable", detail=e2)
            return res
        h = sha(o2)
        ref = doc.get("sha256_original")
        res["sha256"] = h
        res.update(etat="inchange" if ref and h == ref else "change",
                   detail="empreinte de l'export texte identique" if ref and h == ref
                   else "empreinte de l'export texte différente")
        return res
    html = decode(octets, ctype)
    res["html_sha256"] = sha(octets)
    ls, _ = extraire(html)
    res["lignes"] = ls
    return res


def traite_web(res, ref_html, etat, texte_archive):
    """Décide de l'état d'une page web ; met à jour l'état (empreinte du texte)."""
    if "lignes" not in res:
        return
    texte = "\n".join(res.pop("lignes"))
    h_txt = sha(texte.encode())
    res["sha256_texte"] = h_txt
    memoire = etat["pages"].get(res["url"])
    etat["pages"][res["url"]] = {"sha256_texte": h_txt, "vu": dt.date.today().isoformat()}
    if ref_html and res.get("html_sha256") == ref_html:
        res.update(etat="inchange", detail="HTML identique à l'archive")
    elif memoire:
        if memoire["sha256_texte"] == h_txt:
            res.update(etat="inchange", detail="texte extrait identique à la veille précédente "
                       "(HTML différent : changement cosmétique)")
        else:
            res.update(etat="change", detail="texte extrait différent de la veille précédente")
    else:
        # première veille de cette page : on compare au texte archivé
        ls = [norm(l) for l in texte.splitlines() if len(l) > 20]
        if not ls:
            res.update(etat="a_verifier", detail="aucun texte exploitable (page rendue par JavaScript ?)")
        else:
            arch = norm(texte_archive)
            part = sum(1 for l in ls if l in arch) / len(ls)
            res["couverture"] = round(part, 2)
            if part >= 0.5:
                res.update(etat="inchange", detail=f"{part:.0%} des lignes retrouvées dans le texte archivé "
                           "(référence initiale enregistrée)")
            else:
                res.update(etat="a_verifier", detail=f"seulement {part:.0%} des lignes retrouvées dans le "
                           "texte archivé (référence initiale enregistrée) : contenu modifié ou page "
                           "rendue autrement")


def veille_programmes(cands_dir, etat, workers):
    taches = []
    for meta in sorted(cands_dir.glob("*/meta.json")):
        m = json.loads(meta.read_text(encoding="utf-8"))
        for doc in m.get("documents", []):
            ft = meta.parent / doc["fichier_texte"] if doc.get("fichier_texte") else None
            archive = ft.read_text(encoding="utf-8") if ft and ft.exists() else ""
            ps = doc.get("pages_sources") or []
            sources = [(p["url"], p.get("html_sha256")) for p in ps] or [(doc["source_url"], None)]
            for url, h in sources:
                taches.append((m, doc, url, h, archive))

    def run(t):
        m, doc, url, h, archive = t
        return t, verifie_source(url, doc, archive)

    out = {}
    with cf.ThreadPoolExecutor(workers) as ex:
        for (m, doc, url, h, archive), res in ex.map(run, taches):
            traite_web(res, h, etat, archive)
            out.setdefault((m["slug"], m["nom"], doc["id"], doc["titre"]), []).append(res)
    return out


# ---------------------------------------------------------------- sans programme
def veille_sans_programme(fichier, etat, workers):
    cands = json.loads(fichier.read_text(encoding="utf-8"))["candidats"]

    def run(c):
        site = c.get("site_officiel")
        r = {"nom": c["nom"], "site": site, "trouves": [], "etat": "ok"}
        if not site:
            r["etat"] = "sans_site"
            return r
        pages = [site] + [urllib.parse.urljoin(site, p) for p in CHEMINS]
        vus = {}
        accueil_ok = False
        for i, u in enumerate(pages):
            st, o, ct, err = fetch(u, tentatives=2 if i == 0 else 1)
            if i == 0:
                if err and not o:
                    r.update(etat="injoignable", detail=err)
                    return r
                accueil_ok = True
                r["http"] = st
            if err or not o:
                continue
            if i > 0:  # chemin courant qui répond : c'est en soi un signal
                if not urllib.parse.urlparse(u).path.rstrip("/") == urllib.parse.urlparse(site).path.rstrip("/"):
                    vus[u] = "page « %s » répond" % urllib.parse.urlparse(u).path
            if "html" not in ct.lower() and i > 0:
                continue
            _, liens = extraire(decode(o, ct))
            for href, txt in liens:
                full = urllib.parse.urljoin(u, href).split("#")[0]
                if full.lower().split("?")[0].endswith(".pdf"):
                    vus[full] = f"PDF : {txt or full}"[:120]
                elif MOTS_CLES.search(txt) and len(txt) < 80:
                    vus[full] = f"lien « {txt} »"
        r["trouves"] = [{"url": k, "desc": v} for k, v in sorted(vus.items())]
        return r

    with cf.ThreadPoolExecutor(workers) as ex:
        resultats = list(ex.map(run, cands))
    for r in resultats:
        ancien = etat["sans_programme"].get(r["nom"])
        urls = {t["url"] for t in r["trouves"]}
        r["premiere_veille"] = ancien is None
        r["nouveaux"] = [t for t in r["trouves"] if ancien is not None and t["url"] not in set(ancien)]
        if r["etat"] != "injoignable":
            etat["sans_programme"][r["nom"]] = sorted(urls | set(ancien or []))
    return resultats


# ---------------------------------------------------------------- rapport
def rapport(date, prog, sans):
    chg, inj, aver = [], [], []
    n_src = n_ok = 0
    for (slug, nom, did, titre), rs in prog.items():
        for r in rs:
            n_src += 1
            if r["etat"] == "inchange":
                n_ok += 1
            elif r["etat"] == "injoignable":
                inj.append((nom, did, r))
            elif r["etat"] == "change":
                chg.append((nom, did, r))
            else:
                aver.append((nom, did, r))
    sp_inj = [r for r in sans if r["etat"] == "injoignable"]
    sp_new = [r for r in sans if r["nouveaux"]]
    sp_ref = [r for r in sans if r["premiere_veille"] and r["trouves"]]

    def ligne(nom, did, r):
        return f"- **{nom}** (`{did}`) — {r['url']}\n  {r['detail']}"

    L = [f"# Veille du {date.strftime('%d/%m/%Y')}", "",
         "Rapport automatique. **Aucune donnée candidate n'a été modifiée** : toute mise à jour "
         "reste une action manuelle après lecture de ce rapport.", "",
         "## Résumé", "",
         f"- Sources de programmes contrôlées : {n_src} (inchangées : {n_ok})",
         f"- Changements probables : {len(chg)}",
         f"- À vérifier à la main : {len(aver)}",
         f"- Sources injoignables : {len(inj) + len(sp_inj)}",
         f"- Candidats sans programme : {len(sans)} contrôlés, {len(sp_new)} avec un lien nouveau", ""]
    L += ["## Programmes modifiés", ""] + ([ligne(*x) for x in chg] or ["Aucun."]) + [""]
    L += ["## À vérifier", ""] + ([ligne(*x) for x in aver] or ["Aucune."]) + [""]
    L += ["## Sources injoignables", "",
          "Une erreur 403 ou une coupure de connexion peut venir d'un blocage anti-robot : "
          "vérifier dans un navigateur avant de conclure.", ""]
    L += [ligne(*x) for x in inj] + [f"- **{r['nom']}** (site officiel) — {r['site']}\n  {r['detail']}" for r in sp_inj]
    if not inj and not sp_inj:
        L.append("Aucune.")
    L += ["", "## Programmes nouveaux (candidats sans programme)", ""]
    if sp_new:
        for r in sp_new:
            L.append(f"- **{r['nom']}** — {r['site']}")
            L += [f"  - {t['desc']} → {t['url']}" for t in r["nouveaux"]]
    else:
        L.append("Aucun lien nouveau détecté.")
    if sp_ref:
        L += ["", "### Référence initiale (liens déjà présents à la première veille)", "",
              "À examiner une fois ; ils ne seront plus signalés comme nouveaux.", ""]
        for r in sp_ref:
            L.append(f"- **{r['nom']}** — {r['site']}")
            L += [f"  - {t['desc']} → {t['url']}" for t in r["trouves"][:8]]
    L += ["", "## Détail des sources inchangées", ""]
    for (slug, nom, did, titre), rs in prog.items():
        ok = sum(1 for r in rs if r["etat"] == "inchange")
        L.append(f"- {nom} — `{did}` : {ok}/{len(rs)} inchangée(s)")
    L.append("")
    resume = {"changes": len(chg), "a_verifier": len(aver), "injoignables": len(inj) + len(sp_inj),
              "nouveaux": len(sp_new)}
    return "\n".join(L), resume


def main():
    racine = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(racine / "data/presidentielle-2027"))
    ap.add_argument("--out", default=str(racine / "veille"))
    ap.add_argument("--issue-body")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    data, out = Path(a.data), Path(a.out)
    out.mkdir(exist_ok=True)
    f_etat = out / "etat.json"
    etat = json.loads(f_etat.read_text()) if f_etat.exists() else {}
    etat.setdefault("pages", {})
    etat.setdefault("sans_programme", {})

    date = dt.date.today()
    prog = veille_programmes(data / "candidats", etat, a.workers)
    sans = veille_sans_programme(data / "candidats-sans-programme.json", etat, a.workers)
    texte, resume = rapport(date, prog, sans)

    (out / f"{date.isoformat()}.md").write_text(texte, encoding="utf-8")
    f_etat.write_text(json.dumps(etat, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if a.issue_body:
        Path(a.issue_body).write_text(texte[:60000], encoding="utf-8")
    print(json.dumps(resume), file=sys.stderr)
    print(out / f"{date.isoformat()}.md")


if __name__ == "__main__":
    main()
