"""Application Flask pour Scalingo : sert dist/ en statique et expose trois
compteurs anonymes (/api/compteurs, /api/visite, /api/soutien).

Aucun cookie, aucune session, aucune adresse IP enregistrée. Pour limiter les
clics répétés sur « Je soutiens », une empreinte SHA-256 salée de l'adresse
IP et du jour est conservée en mémoire 24 h ; le sel est tiré au hasard au
démarrage (ou lu dans SEL_SOUTIEN) et n'est jamais écrit.

Stockage des compteurs : Postgres si DATABASE_URL est défini (psycopg),
sinon mémoire (développement).
"""
from __future__ import annotations

import hashlib
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

RACINE = Path(__file__).resolve().parent.parent
DIST = Path(os.environ.get("DIST", RACINE / "dist")).resolve()

# Construit dist/ au démarrage s'il est absent (Scalingo : le conteneur web
# part du dépôt, dist/ n'étant pas versionné).
if not (DIST / "index.html").is_file():
    subprocess.run([sys.executable, str(RACINE / "site" / "build.py"), "--dist", str(DIST)], check=True)

app = Flask(__name__, static_folder=None)

CSP = ("default-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'self'; "
       "style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'self'")


# ----------------------------------------------------------------------------
# Compteurs
# ----------------------------------------------------------------------------

class Memoire:
    """Repli en mémoire (développement, ou DATABASE_URL absent)."""

    def __init__(self):
        self.v = {"visites": 0, "soutiens": 0}
        self.depuis = date.today().isoformat()
        self.lock = threading.Lock()

    def lire(self):
        with self.lock:
            return {"visites": self.v["visites"], "soutiens": self.v["soutiens"], "depuis": self.depuis}

    def incrementer(self, cle):
        with self.lock:
            self.v[cle] += 1
        return self.lire()


class Postgres:
    """Table compteurs(cle text primary key, valeur bigint, depuis date)."""

    def __init__(self, url):
        import psycopg
        self.psycopg = psycopg
        self.url = url
        with self.connexion() as cx:
            cx.execute("CREATE TABLE IF NOT EXISTS compteurs (cle text PRIMARY KEY, valeur bigint NOT NULL DEFAULT 0, depuis date NOT NULL DEFAULT CURRENT_DATE)")
            for cle in ("visites", "soutiens"):
                cx.execute("INSERT INTO compteurs (cle, valeur) VALUES (%s, 0) ON CONFLICT (cle) DO NOTHING", (cle,))
            cx.commit()

    def connexion(self):
        return self.psycopg.connect(self.url, connect_timeout=5)

    def lire(self):
        with self.connexion() as cx:
            lignes = cx.execute("SELECT cle, valeur, depuis FROM compteurs").fetchall()
        d = {"visites": 0, "soutiens": 0, "depuis": None}
        for cle, valeur, depuis in lignes:
            d[cle] = int(valeur)
            if cle == "visites":
                d["depuis"] = depuis.isoformat()
        return d

    def incrementer(self, cle):
        with self.connexion() as cx:
            cx.execute("UPDATE compteurs SET valeur = valeur + 1 WHERE cle = %s", (cle,))
            cx.commit()
        return self.lire()


def ouvrir_stockage():
    url = os.environ.get("DATABASE_URL") or os.environ.get("SCALINGO_POSTGRESQL_URL")
    if url:
        try:
            return Postgres(url)
        except Exception as exc:  # noqa: BLE001
            print(f"Postgres indisponible ({exc}), repli en mémoire", file=sys.stderr)
    return Memoire()


stockage = ouvrir_stockage()

# Anti-rafale pour /api/soutien : empreinte salée (IP + jour), en mémoire, 24 h.
SEL = os.environ.get("SEL_SOUTIEN") or secrets.token_hex(16)
_recents: dict[str, float] = {}
_recents_lock = threading.Lock()
DUREE = 24 * 3600


def adresse_client() -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() if xff else request.remote_addr) or "?"


def empreinte_jour() -> str:
    return hashlib.sha256(f"{SEL}|{adresse_client()}|{date.today().isoformat()}".encode()).hexdigest()


def deja_soutenu(h: str) -> bool:
    maintenant = time.time()
    with _recents_lock:
        for k in [k for k, t in _recents.items() if maintenant - t > DUREE]:
            del _recents[k]
        if h in _recents:
            return True
        _recents[h] = maintenant
        return False


# ----------------------------------------------------------------------------
# API
# ----------------------------------------------------------------------------

@app.get("/api/compteurs")
def api_compteurs():
    return jsonify(stockage.lire())


@app.post("/api/visite")
def api_visite():
    return jsonify(stockage.incrementer("visites"))


@app.post("/api/soutien")
def api_soutien():
    if deja_soutenu(empreinte_jour()):
        d = stockage.lire()
        d["deja"] = True
        return jsonify(d)
    d = stockage.incrementer("soutiens")
    d["deja"] = False
    return jsonify(d)


# ----------------------------------------------------------------------------
# Fichiers statiques
# ----------------------------------------------------------------------------

@app.get("/")
@app.get("/<path:chemin>")
def statique(chemin: str = ""):
    if chemin.startswith("api/"):
        abort(404)
    cible = (DIST / chemin).resolve()
    if DIST not in cible.parents and cible != DIST:
        abort(404)
    if cible.is_dir():
        if chemin and not chemin.endswith("/"):
            # /sujets -> /sujets/
            from flask import redirect
            return redirect("/" + chemin + "/", code=301)
        cible = cible / "index.html"
    if not cible.is_file():
        rep = send_from_directory(DIST, "404.html")
        rep.status_code = 404
        return rep
    rel = cible.relative_to(DIST).as_posix()
    rep = send_from_directory(DIST, rel)
    if rel.startswith("candidats/") and ("/original/" in rel or "/texte/" in rel):
        rep.headers["Cache-Control"] = "public, max-age=604800"
    elif rel.startswith("static/") or rel.startswith("data/"):
        rep.headers["Cache-Control"] = "public, max-age=3600"
    else:
        rep.headers["Cache-Control"] = "public, max-age=300"
    return rep


@app.after_request
def entetes(rep):
    rep.headers["Content-Security-Policy"] = CSP
    rep.headers["X-Frame-Options"] = "SAMEORIGIN"
    rep.headers["X-Content-Type-Options"] = "nosniff"
    rep.headers["Referrer-Policy"] = "no-referrer"
    rep.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
    if request.path.startswith("/api/"):
        rep.headers["Cache-Control"] = "no-store"
    rep.headers.pop("Set-Cookie", None)
    return rep


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8000)), debug=False)
