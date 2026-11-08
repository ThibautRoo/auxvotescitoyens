# Site : génération, exécution, déploiement

Le site est entièrement statique (HTML, un fichier CSS, un script JS), généré par `site/build.py` depuis `data/`, `sujets/` et `CHARTE.md`. Une petite application Flask (`app/app.py`) le sert et tient deux compteurs anonymes (visites, soutiens). Aucune ressource n'est chargée depuis un autre domaine, aucun cookie n'est déposé.

## Construire

```
python3 site/build.py            # écrit dist/
python3 site/build.py --dist /tmp/x
```

Python 3 standard uniquement, sans dépendance. Le générateur :

- lit `data/presidentielle-2027/election.json`, `candidats-sans-programme.json` et chaque `candidats/<slug>/meta.json` avec son `texte/*.txt` ;
- copie dans `dist/candidats/<slug>/` le texte et le fichier original, recalcule leurs empreintes SHA-256 et avertit sur la sortie d'erreur si elles diffèrent de `meta.json` ;
- précalcule la recherche par sujet (`dist/data/sujets.json`) et exporte les textes pour la recherche libre (`dist/data/textes.json`, découpé par candidat au-delà de 3 Mo) ;
- rend `CHARTE.md` en HTML (convertisseur Markdown minimal, intégré).

Règle de recherche (identique dans `build.py` et `static/app.js`) : mot entier ou suivi d'une marque de pluriel (`s`, `x`, `es`), sans tenir compte de la casse ni des accents ; un passage est la phrase complète contenant le mot, la découpe se faisant sur `.`, `!`, `?` et les sauts de ligne. Les lignes séparateur `=== Titre === URL` des captures web deviennent des titres avec lien vers la page source.

## Lancer en local

```
pip install -r requirements.txt
python3 site/build.py
PORT=8000 python3 app/app.py        # ou : gunicorn app.app:app
```

Sans `DATABASE_URL`, les compteurs sont tenus en mémoire (remis à zéro à chaque redémarrage).

API :

- `GET /api/compteurs` → `{"visites": N, "soutiens": N, "depuis": "AAAA-MM-JJ"}`
- `POST /api/visite` : incrémente les visites (appelé une fois par chargement de page)
- `POST /api/soutien` : incrémente les soutiens ; un même visiteur n'est compté qu'une fois par 24 h, via une empreinte SHA-256 salée (IP + jour) conservée en mémoire seulement ; renvoie `deja: true` sinon.

## Déployer sur Scalingo

```
scalingo create auxvotescitoyens
scalingo --app auxvotescitoyens addons-add postgresql postgresql-starter-512   # fournit SCALINGO_POSTGRESQL_URL et DATABASE_URL
scalingo --app auxvotescitoyens env-set SEL_SOUTIEN=$(openssl rand -hex 16)   # facultatif : sel stable entre redémarrages
git remote add scalingo git@ssh.osc-fr1.scalingo.com:auxvotescitoyens.git
git push scalingo main
scalingo --app auxvotescitoyens domains-add auxvotescitoyens.fr
```

Fichiers lus par Scalingo : `requirements.txt`, `.python-version` (3.12), `Procfile` (`web: gunicorn app.app:app ...`). `dist/` n'est pas versionné : l'application le génère au démarrage s'il est absent (quelques secondes). Un seul worker gunicorn (plusieurs threads) pour que la limite anti-rafale des soutiens, tenue en mémoire, soit cohérente.

Variables d'environnement :

| Variable | Rôle |
|---|---|
| `DATABASE_URL` ou `SCALINGO_POSTGRESQL_URL` | Postgres pour les compteurs (table `compteurs(cle, valeur, depuis)`, créée automatiquement). Absente : mémoire. |
| `SEL_SOUTIEN` | Sel de l'empreinte anti-rafale. Absent : tiré au hasard au démarrage. |
| `DIST` | Emplacement de `dist/` (défaut : `dist/` à la racine). |
| `PORT` | Fourni par Scalingo. |

## En-têtes et vie privée

`Content-Security-Policy: default-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'self'; style-src 'self'; script-src 'self'; ...`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`. Aucun `Set-Cookie`. Les styles sont tous dans `static/style.css` (aucun attribut `style`, interdit par la CSP).

## Arborescence de `dist/`

```
index.html                       accueil
candidats/<slug>/index.html      programme intégral, ou page « pas de programme publié »
candidats/<slug>/texte/…         texte brut publié (téléchargeable, empreinte vérifiable)
candidats/<slug>/original/…      PDF ou fichier original tel que capturé
sujets/index.html, sujets/<id>/  recherche par sujet (sans JavaScript)
comparer/                        comparaison 2 à 4 candidats (JavaScript, données locales)
recherche/?q=mot                 recherche libre (JavaScript, données locales)
charte/  mentions-legales/  verifier/  404.html  robots.txt
data/sujets.json  data/textes.json  data/candidats.json
static/style.css  static/app.js
```
