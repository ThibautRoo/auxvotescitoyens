#!/usr/bin/env python3
"""Générateur du site statique auxvotescitoyens.fr.

Lit data/, sujets/ et CHARTE.md, écrit dist/. Python 3 standard uniquement.
Aucun contenu n'est rédigé ici sur les candidats : tout vient des fichiers
de données, reproduits tels quels.

Usage : python3 site/build.py [--dist CHEMIN]
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DATA = RACINE / "data" / "presidentielle-2027"
SUJETS = RACINE / "sujets" / "sujets.json"
CHARTE = RACINE / "CHARTE.md"
STATIC = RACINE / "site" / "static"

DEPOT = "https://github.com/ThibautRoo/auxvotescitoyens"
ISSUES = DEPOT + "/issues"
CONTACT = "contact@auxvotescitoyens.fr"
SITE_NOM = "auxvotescitoyens.fr"
PASSAGES_VISIBLES = 5          # passages affichés avant « Afficher les autres »
LIMITE_TEXTES = 3 * 1024 * 1024  # au-delà, textes.json est découpé par candidat

NBSP = " "  # espace fine insécable pour les nombres

# ----------------------------------------------------------------------------
# Utilitaires
# ----------------------------------------------------------------------------

def e(s) -> str:
    """Échappe pour HTML."""
    return html.escape("" if s is None else str(s), quote=True)


def nombre(n: int) -> str:
    return f"{n:,}".replace(",", NBSP)


def date_fr(iso: str | None, heure: bool = False) -> str:
    if not iso:
        return "non indiquée"
    try:
        if len(iso) == 7:  # AAAA-MM
            d = datetime.strptime(iso, "%Y-%m")
            mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
                    "août", "septembre", "octobre", "novembre", "décembre"]
            return f"{mois[d.month - 1]} {d.year}"
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if heure and len(iso) > 10:
        d = d.astimezone(timezone.utc)
        return d.strftime("%d/%m/%Y à %H:%M UTC")
    return d.strftime("%d/%m/%Y")


def slugify(nom: str) -> str:
    s = unicodedata.normalize("NFD", nom)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def sha256_fichier(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def hote(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1) if m else (url or "")


# ----------------------------------------------------------------------------
# Recherche mécanique (identique côté client dans app.js)
# ----------------------------------------------------------------------------

def normaliser(texte: str) -> str:
    """Normalisation caractère par caractère (même longueur que l'original) :
    minuscules, sans accents. Permet de retrouver les positions d'origine."""
    sortie = []
    for c in texte:
        d = unicodedata.normalize("NFD", c)
        base = d[0] if d else c
        sortie.append(base.lower())
    return "".join(sortie)


def motif_mot(mot: str) -> re.Pattern:
    """Mot entier, ou mot suivi d'une marque de pluriel (s, x, es)."""
    n = normaliser(mot).strip()
    parties = [re.escape(p) for p in n.split()]
    corps = r"\s+".join(parties)
    return re.compile(r"(?<!\w)" + corps + r"(?:s|x|es)?(?!\w)")


SEPARATEUR = re.compile(r"^=== (.*?) === (\S+)\s*$")
FIN_PHRASE = re.compile(r"(?<=[.!?])\s+")


def phrases(texte: str, par_bloc: bool = False):
    """Rend des (debut, fin) de phrases : découpe sur les sauts de ligne, puis
    sur . ! ? suivis d'un blanc. Les lignes séparateur « === » sont ignorées.
    par_bloc=True (textes extraits de PDF) : l'unité est le paragraphe, c'est-à-dire
    un bloc de lignes consécutives non vides, pour qu'une phrase coupée par la mise
    en page du PDF reste entière. Les positions renvoyées restent celles du texte
    d'origine, rien n'est réécrit."""
    pos = 0
    bloc_debut = None
    bloc_fin = None

    def _decouper(d0, f0):
        debut = d0
        for morceau in FIN_PHRASE.split(texte[d0:f0]):
            d = debut
            f = d + len(morceau)
            while d < f and texte[d].isspace():
                d += 1
            while f > d and texte[f - 1].isspace():
                f -= 1
            if f > d:
                yield d, f
            debut += len(morceau)
            while debut < f0 and texte[debut].isspace():
                debut += 1

    for ligne in texte.split("\n"):
        fin_ligne = pos + len(ligne)
        utile = bool(ligne.strip()) and not SEPARATEUR.match(ligne)
        if not par_bloc:
            if utile:
                yield from _decouper(pos, fin_ligne)
        else:
            if utile:
                if bloc_debut is None:
                    bloc_debut = pos
                bloc_fin = fin_ligne
            elif bloc_debut is not None:
                yield from _decouper(bloc_debut, bloc_fin)
                bloc_debut = bloc_fin = None
        pos = fin_ligne + 1
    if par_bloc and bloc_debut is not None:
        yield from _decouper(bloc_debut, bloc_fin)


def index_structure(texte: str):
    """Pour chaque position : numéro de page (PDF, par \\f) et numéro de
    section (web, par séparateur ===)."""
    pages = []  # positions de début de page (après chaque \f)
    for m in re.finditer("\f", texte):
        pages.append(m.end())
    sections = []  # (position de la ligne séparateur, titre)
    for m in re.finditer(r"(?m)^=== (.*?) === (\S+)\s*$", texte):
        sections.append((m.start(), m.group(1)))
    return pages, sections


def localiser(pos: int, pages, sections):
    import bisect
    page = bisect.bisect_right(pages, pos) + 1 if pages else None
    sec = bisect.bisect_right([s[0] for s in sections], pos) if sections else 0
    return page, sec


def chercher(texte: str, texte_norm: str, liste_phrases, mots: list[str], pages, sections):
    """Passages (phrases complètes) contenant l'un des mots."""
    motifs = [motif_mot(m) for m in mots]
    resultats = []
    for d, f in liste_phrases:
        segment = texte_norm[d:f]
        marques = []
        for mo in motifs:
            for m in mo.finditer(segment):
                marques.append([m.start(), m.end()])
        if not marques:
            continue
        marques.sort()
        # fusionne les chevauchements
        fusion = []
        for a, b in marques:
            if fusion and a <= fusion[-1][1]:
                fusion[-1][1] = max(fusion[-1][1], b)
            else:
                fusion.append([a, b])
        page, sec = localiser(d, pages, sections)
        ancre = f"page-{page}" if page else (f"s-{sec}" if sec else "")
        resultats.append({
            "t": texte[d:f],
            "m": fusion,
            "p": page,
            "s": sec if sections else None,
            "a": ancre,
            "w": len(texte[d:f].split()),
        })
    return resultats


def surligner(passage: dict) -> str:
    t, marques = passage["t"], passage["m"]
    out, pos = [], 0
    for a, b in marques:
        out.append(e(t[pos:a]))
        out.append("<mark>" + e(t[a:b]) + "</mark>")
        pos = b
    out.append(e(t[pos:]))
    return "".join(out)


# ----------------------------------------------------------------------------
# Chargement des données
# ----------------------------------------------------------------------------

def charger():
    election = json.loads((DATA / "election.json").read_text(encoding="utf-8"))
    sans = json.loads((DATA / "candidats-sans-programme.json").read_text(encoding="utf-8"))
    sujets = json.loads(SUJETS.read_text(encoding="utf-8"))["sujets"]
    candidats = []
    for dossier in sorted((DATA / "candidats").iterdir()):
        meta_p = dossier / "meta.json"
        if not meta_p.is_file():
            continue
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        docs = [d for d in meta["documents"] if d.get("type") in ("programme", "manifeste", "propositions", "projet")]
        # document courant : celui qui n'est remplacé par aucun autre
        remplaces = {d.get("remplace") for d in docs if d.get("remplace")}
        courants = [d for d in docs if d["id"] not in remplaces]
        doc = courants[-1] if courants else docs[-1]
        texte = (dossier / doc["fichier_texte"]).read_text(encoding="utf-8")
        pages, sections = index_structure(texte)
        c = {
            "slug": meta["slug"],
            "nom": meta["nom"],
            "mouvement": meta.get("mouvement"),
            "site_officiel": meta.get("site_officiel"),
            "statut": meta.get("statut"),
            "declaration_date": meta.get("declaration_date"),
            "declaration_source": meta.get("declaration_source"),
            "dossier": dossier,
            "meta": meta,
            "docs": docs,
            "doc": doc,
            "texte": texte,
            "texte_norm": normaliser(texte),
            "pages": pages,
            "sections": sections,
            "mots_total": len(texte.split()),
        }
        c["phrases"] = list(phrases(texte, par_bloc=(doc.get("format") == "pdf")))
        candidats.append(c)
    candidats.sort(key=lambda c: slugify(c["nom"]))
    for s in sans["candidats"]:
        # même convention que les dossiers data/ : nom-prenom (premier mot = prénom)
        parties = s["nom"].split()
        s["slug"] = slugify(" ".join(parties[1:] + parties[:1])) if len(parties) > 1 else slugify(s["nom"])
    sans["candidats"].sort(key=lambda s: slugify(s["nom"]))
    return election, candidats, sans, sujets


# ----------------------------------------------------------------------------
# Gabarit de page
# ----------------------------------------------------------------------------

MENU = [
    ("/", "Tous les programmes"),
    ("/sujets/", "Sujets"),
    ("/comparer/", "Comparer"),
    ("/charte/", "Charte de neutralité"),
    (DEPOT, "Code source"),
]


def page(titre: str, corps: str, courant: str = "", classe_main: str = "", description: str = "") -> str:
    menu = []
    for href, lib in MENU:
        cur = ' aria-current="page"' if href == courant else ""
        ext = ' rel="noopener"' if href.startswith("http") else ""
        menu.append(f'<a href="{e(href)}"{cur}{ext}>{e(lib)}</a>')
    desc = f'<meta name="description" content="{e(description)}">\n' if description else ""
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(titre)} · {SITE_NOM}</title>
{desc}<link rel="stylesheet" href="/static/style.css">
<link rel="icon" href="data:,">
</head>
<body>
<a class="skip" href="#contenu">Aller au contenu</a>
<div class="tricolore" aria-hidden="true"><span class="b"></span><span class="w"></span><span class="r"></span></div>
<header class="site">
<div class="entete">
<a class="logo" href="/" aria-label="{SITE_NOM}, accueil"><span class="barre" aria-hidden="true"><span class="b"></span><span class="r"></span></span><span class="nom">{SITE_NOM}</span></a>
<nav class="menu" aria-label="Navigation principale">
{chr(10).join(menu)}
</nav>
</div>
</header>
<main id="contenu"{(' class="' + classe_main + '"') if classe_main else ''}>
{corps}
</main>
<footer class="site">
<p>Projet associatif, open source et gratuit. Aucune publicité, aucun traceur, aucun financement politique.</p>
<p class="compteurs" id="compteurs" data-modele="[N] visites depuis le [JJ/MM/AAAA] · [N] validations">– visites · – validations</p>
<nav aria-label="Pied de page">
<a href="mailto:{CONTACT}">Contact : {CONTACT}</a>
<a href="{ISSUES}" rel="noopener">Signaler une erreur de texte</a>
<a href="/mentions-legales/">Mentions légales</a>
<a href="/charte/">Charte de neutralité</a>
<a href="/verifier/">Vérifier une empreinte</a>
</nav>
</footer>
<script src="/static/app.js"></script>
</body>
</html>
"""


# ----------------------------------------------------------------------------
# Pages
# ----------------------------------------------------------------------------

def carte_candidat(c: dict) -> str:
    doc = c["doc"]
    if doc.get("format") == "pdf" and doc.get("pages"):
        taille = f"{nombre(doc['pages'])} pages"
    else:
        taille = f"{nombre(c['mots_total'])} mots"
    mouv = c["mouvement"] or "Mouvement non indiqué sur le site"
    return (f'<a class="carte" href="/candidats/{e(c["slug"])}/" data-slug="{e(c["slug"])}">'
            f'<span class="badge">Programme publié</span>'
            f'<span class="nom">{e(c["nom"])}</span>'
            f'<span class="mouv">{e(mouv)}</span>'
            f'<span class="info">Texte intégral · version du {date_fr(doc["capture_date"])} · {taille}</span>'
            f'</a>')


def carte_sans(s: dict) -> str:
    return (f'<a class="carte" href="/candidats/{e(s["slug"])}/" data-slug="{e(s["slug"])}">'
            f'<span class="badge gris">Pas de programme publié</span>'
            f'<span class="nom">{e(s["nom"])}</span>'
            f'<span class="mouv">{e(s.get("mouvement") or "Mouvement non indiqué sur le site")}</span>'
            f'<span class="info">Site officiel vérifié le {date_fr(s["verifie_le"])}</span>'
            f'</a>')


def page_accueil(election, candidats, sans) -> str:
    cartes = [(slugify(c["nom"]), carte_candidat(c)) for c in candidats]
    cartes += [(slugify(s["nom"]), carte_sans(s)) for s in sans["candidats"]]
    cartes.sort(key=lambda x: x[0])
    date_liste = date_fr(election.get("liste_officielle_date_prevue"))
    corps = f"""
<div class="verbatim"><span class="mot">VERBATIM</span><span class="def">· du latin « mot pour mot » : reproduit tel qu'écrit, sans retrait, sans ajout, sans reformulation</span></div>
<h1 class="titre"><span class="bleu">Les programmes.</span><br>Mot pour mot.<br><span class="rouge">Rien d'autre.</span></h1>
<p class="chapeau">Nous n'écrivons rien. Chaque programme est publié tel que le candidat l'a écrit, en entier, avec sa source officielle.</p>
<ul class="engagements" aria-label="Engagements">
<li class="fort">Aucun résumé</li>
<li class="fort">Aucune interprétation</li>
<li class="fort">Aucun favoritisme</li>
<li>Aucun sondage</li>
<li>Aucun financement politique</li>
<li>Code ouvert à tous</li>
</ul>
<div class="soutien">
<button type="button" class="btn" id="btn-soutien"><svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>Je valide la démarche</button>
<p><strong id="nb-soutiens">–</strong> citoyens ont validé cette démarche : un site neutre, sans résumé ni interprétation. <strong>Gratuit, un clic, rien d'autre</strong> : ni argent, ni compte, ni adresse, ni traceur.</p>
</div>
<form class="recherche" action="/recherche/" method="get" role="search">
<label for="q" class="sr-only">Rechercher un mot dans tous les programmes</label>
<input id="q" name="q" type="search" placeholder="Un mot dans tous les programmes : retraite, école, énergie..." required>
<button type="submit" class="btn plein">Rechercher</button>
</form>
<section class="sources" aria-labelledby="h-sources">
<h2 id="h-sources">Trois sources. Aucune autre.</h2>
<p>Tout ce qui est publié ici provient exclusivement de ces trois sources officielles. Aucun article, aucune interview, aucun réseau social, aucune déclaration orale.</p>
<ol>
<li><span class="num" aria-hidden="true">1</span><div><p class="t">Le Conseil constitutionnel</p><p>Sa liste officielle fixe qui est candidat, et dans quel ordre. Nous n'ajoutons ni ne retirons personne.</p></div></li>
<li><span class="num" aria-hidden="true">2</span><div><p class="t">Le ministère de l'Intérieur</p><p>La profession de foi officielle de chaque candidat, validée par la commission de propagande.</p></div></li>
<li><span class="num" aria-hidden="true">3</span><div><p class="t">Le site officiel du candidat</p><p>Son programme complet, tel qu'il le publie lui-même. Chaque version est datée et conservée.</p></div></li>
</ol>
</section>
<section aria-labelledby="h-election">
<div class="liste-tete">
<h2 id="h-election">{e(election["nom"])}</h2>
<div class="tirage" role="status">
<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 3h5v5"/><path d="M4 20L21 3"/><path d="M21 16v5h-5"/><path d="M15 15l6 6"/><path d="M4 4l5 5"/></svg>
<span><strong>Ordre tiré au sort</strong> à chaque affichage. Personne n'est jamais premier.</span>
<button type="button" class="retirer" data-retirer>Retirer au sort</button>
</div>
</div>
<div class="bandeau" role="note">
<span class="etiquette">Candidats déclarés</span>
<p>Liste non officielle. Le Conseil constitutionnel publiera la liste officielle des candidats en {e(date_liste)} ; cette page basculera alors sur celle-ci. Est affiché ici tout candidat qui a annoncé publiquement sa candidature et publié un programme écrit sur son site officiel. Rien d'autre n'entre en compte.</p>
</div>
<p class="note">Aucune étiquette politique, aucune couleur, aucun logo : seuls le nom du candidat et le nom qu'il donne lui-même à son mouvement.</p>
<p class="verif">Sites officiels vérifiés chaque semaine. Dernière vérification : {date_fr(sans.get("derniere_verification"))}.</p>
<div class="cartes" data-shuffle>
{chr(10).join(h for _, h in cartes)}
</div>
</section>
"""
    return page("Les programmes. Mot pour mot. Rien d'autre.", corps, "/", "accueil",
                "Les programmes des candidats à l'élection présidentielle 2027, en texte intégral, avec leur source et leur empreinte. Aucun résumé, aucune interprétation.")


def rendre_texte(c: dict) -> str:
    """Texte intégral : les lignes consécutives forment un paragraphe (pour un PDF,
    les retours à la ligne de mise en page sont rendus comme des espaces ; pour une
    page web, chaque ligne reste une ligne). Les séparateurs « === » deviennent des
    chapitres repliables avec un sommaire. Ancres de page invisibles pour les PDF.
    Aucun mot n'est ajouté, retiré ni modifié."""
    est_pdf = c["doc"].get("format") == "pdf"
    sections = []  # (id, titre, url, [paragraphes])
    courant = None
    bloc = []
    num_page = [1]

    def rendre_ligne(ligne):
        if "\f" not in ligne:
            return e(ligne)
        rendu = []
        for i, mo in enumerate(ligne.split("\f")):
            if i > 0:
                num_page[0] += 1
                rendu.append(f'<span class="pg" id="page-{num_page[0]}"></span>')
            rendu.append(e(mo))
        return "".join(rendu)

    def fermer_bloc():
        nonlocal bloc
        if bloc:
            sep = " " if est_pdf else "<br>"
            if courant is None:
                sections.append([0, None, None, []])
            (sections[-1] if courant is None else courant)[3].append("<p>" + sep.join(bloc) + "</p>")
            bloc = []

    for ligne in c["texte"].split("\n"):
        m = SEPARATEUR.match(ligne)
        if m:
            fermer_bloc()
            courant = [len(sections) + 1, m.group(1), m.group(2), []]
            sections.append(courant)
            continue
        if ligne.strip() == "" or ligne.strip() == "\f":
            if "\f" in ligne:
                bloc.append(rendre_ligne(ligne))
            fermer_bloc()
            continue
        bloc.append(rendre_ligne(ligne))
    fermer_bloc()

    titres = [sct for sct in sections if sct[1]]
    out = []
    if len(titres) > 1:
        items = "".join(f'<li><a href="#s-{sid}">{e(t)}</a></li>' for sid, t, _, _ in titres)
        out.append(f'<nav class="sommaire" aria-label="Sommaire du programme"><p class="lib">Sommaire · {len(titres)} chapitres</p><ol>{items}</ol><p class="aide"><button type="button" data-tout="ouvrir">Tout déplier</button> · <button type="button" data-tout="fermer">Tout replier</button></p></nav>')
    for i, (sid, titre, url, paras) in enumerate(sections):
        corps = "\n".join(paras)
        if titre is None:
            out.append(corps)
        elif len(titres) > 1:
            ouvert = " open" if i == 0 or len(titres) <= 3 else ""
            out.append(f'<details class="chapitre" id="s-{sid}"{ouvert}><summary><h2>{e(titre)}</h2><a class="src" href="{e(url)}" rel="noopener">source ↗</a></summary>{corps}</details>')
        else:
            out.append(f'<h2 id="s-{sid}">{e(titre)} <a class="src" href="{e(url)}" rel="noopener">source ↗</a></h2>\n{corps}')
    return "\n".join(out)


def page_candidat(c: dict, election) -> str:
    doc = c["doc"]
    slug = c["slug"]
    est_pdf = doc.get("format") == "pdf"
    fichier_original = doc.get("fichier_original")
    nom_original = Path(fichier_original).name if fichier_original else None
    nom_texte = Path(doc["fichier_texte"]).name
    pages_sources = doc.get("pages_sources") or []
    nb_versions = len(c["docs"])
    mouv = c["mouvement"] or "Mouvement non indiqué sur le site"

    # Bouton original
    if est_pdf and fichier_original:
        btn_original = f'<a class="btn sobre" href="/candidats/{e(slug)}/original/{e(nom_original)}" download>Télécharger le PDF original</a>'
    elif fichier_original:
        btn_original = f'<a class="btn sobre" href="/candidats/{e(slug)}/original/{e(nom_original)}" download>Télécharger le fichier original</a>'
    else:
        src = doc.get("page_source") or doc["source_url"]
        btn_original = f'<a class="btn sobre" href="{e(src)}" rel="noopener">Voir la page source (site du candidat)</a>'

    # Fiche
    source_url = doc.get("source_url") or c["site_officiel"]
    empreintes = []
    if doc.get("sha256_original"):
        empreintes.append(f'<span class="mono">original sha256 {e(doc["sha256_original"])}</span>')
    empreintes.append(f'<span class="mono">texte sha256 {e(doc["sha256_texte"])}</span>')
    if pages_sources and not doc.get("sha256_original"):
        empreintes.append(f'<a href="#onglet-original">{len(pages_sources)} page{"s" if len(pages_sources) > 1 else ""} web, une empreinte par page</a>')
    fiche = f"""<dl class="fiche">
<dt>Source</dt><dd><a href="{e(source_url)}" rel="noopener">{e(hote(source_url))}</a></dd>
<dt>Capturé le</dt><dd>{date_fr(doc["capture_date"], heure=True)}</dd>
<dt>Empreinte</dt><dd>{"<br>".join(empreintes)}<br><a href="/verifier/">Comment vérifier</a></dd>
<dt>Versions</dt><dd><a href="#onglet-versions">{nb_versions} version{"s" if nb_versions > 1 else ""}</a>{", voir les modifications" if nb_versions > 1 else ""}</dd>
</dl>"""

    # Onglet original
    if est_pdf and fichier_original:
        url_pdf = f"/candidats/{slug}/original/{nom_original}"
        onglet_original = (f'<p class="kicker">Document original · PDF tel que téléchargé depuis le site du candidat</p>'
                           f'<div class="original"><object data="{e(url_pdf)}" type="application/pdf" aria-label="PDF original du programme">'
                           f'<p>Votre navigateur n\'affiche pas les PDF dans la page. <a href="{e(url_pdf)}">Télécharger le PDF original</a>.</p>'
                           f'</object></div>')
        lib_original = "Voir l'original (PDF)"
    else:
        lignes = []
        for p in pages_sources:
            lignes.append(f'<tr><td><a href="{e(p["url"])}" rel="noopener">{e(p.get("titre") or p["url"])}</a><br><span class="mono">{e(p["url"])}</span></td>'
                          f'<td class="mono">{e(p.get("html_sha256") or "")}</td><td>{date_fr(p.get("capture_date"), heure=True)}</td></tr>')
        note = doc.get("original_note")
        extra = ""
        if fichier_original:
            extra = f'<p><a href="/candidats/{e(slug)}/original/{e(nom_original)}" download>Télécharger le fichier original</a> (sha256 <span class="mono">{e(doc.get("sha256_original") or "")}</span>)</p>'
        onglet_original = (f'<p class="kicker">Pages sources · texte relevé sur le site du candidat, empreinte SHA-256 du HTML de chaque page</p>'
                           + (f'<p class="legende">{e(note)}</p>' if note else "")
                           + extra
                           + '<table class="tab"><thead><tr><th scope="col">Page</th><th scope="col">Empreinte SHA-256 (HTML)</th><th scope="col">Capture</th></tr></thead><tbody>'
                           + "".join(lignes) + "</tbody></table>")
        lib_original = "Voir l'original (pages web)"

    # Onglet versions
    lignes_v = []
    for d in c["docs"]:
        remplace = f' · remplace {e(d["remplace"])}' if d.get("remplace") else ""
        lignes_v.append(f'<tr><td>{e(d["id"])}</td><td>{e(d.get("titre") or "")}</td><td>{date_fr(d.get("capture_date"), heure=True)}</td><td class="mono">{e(d.get("sha256_texte") or "")}{remplace}</td></tr>')
    onglet_versions = ('<p class="kicker">Versions · toutes les versions capturées sont conservées</p>'
                       '<table class="tab"><thead><tr><th scope="col">Version</th><th scope="col">Titre</th><th scope="col">Capture</th><th scope="col">Empreinte du texte</th></tr></thead><tbody>'
                       + "".join(lignes_v) + "</tbody></table>"
                       + (f'<p class="legende">Une seule version capturée à ce jour. Si le candidat modifie son programme, la nouvelle version sera capturée et les différences affichées ici.</p>' if nb_versions == 1 else ""))

    kicker_texte = ("Texte intégral · l'onglet « Voir l'original » affiche le PDF du candidat tel quel, dans la page"
                    if est_pdf else "Texte intégral · l'onglet « Voir l'original » liste les pages du site du candidat et leur empreinte")

    corps = f"""
<aside>
<a class="retour" href="/">← {e(election["nom"].replace("Élection présidentielle", "Présidentielle"))}</a>
<div>
<h1>{e(c["nom"])}</h1>
<p class="mouv">{e(mouv)}</p>
</div>
{fiche}
<div class="actions">
{btn_original}
<a class="btn sobre" aria-disabled="true" role="link" tabindex="-1">Profession de foi officielle</a>
<p class="mention">Profession de foi : pas encore disponible (publiée par la CNCCEP après la liste officielle).</p>
</div>
<p class="legende">Texte reproduit sans aucune modification. Les titres et l'ordre sont ceux du document original.</p>
</aside>
<div class="contenu">
<div role="tablist" aria-label="Affichage du programme">
<button type="button" role="tab" id="tab-texte" aria-selected="true" aria-controls="onglet-texte">Texte intégral</button>
<button type="button" role="tab" id="tab-original" aria-selected="false" aria-controls="onglet-original" tabindex="-1">{e(lib_original)}</button>
<button type="button" role="tab" id="tab-versions" aria-selected="false" aria-controls="onglet-versions" tabindex="-1">Versions ({nb_versions})</button>
</div>
<section class="panneau" id="onglet-texte" role="tabpanel" aria-labelledby="tab-texte">
<p class="kicker">{kicker_texte}</p>
<h2 class="doc">{e(doc.get("titre") or "")}</h2>
<div class="texte" lang="fr">{rendre_texte(c)}</div>
<p class="legende haut">Fichier texte : <a href="/candidats/{e(slug)}/texte/{e(nom_texte)}">{e(nom_texte)}</a> · sha256 <span class="mono">{e(doc["sha256_texte"])}</span></p>
</section>
<section class="panneau" id="onglet-original" role="tabpanel" aria-labelledby="tab-original" hidden>
{onglet_original}
</section>
<section class="panneau" id="onglet-versions" role="tabpanel" aria-labelledby="tab-versions" hidden>
{onglet_versions}
</section>
</div>
"""
    return page(f"{c['nom']} · programme intégral", corps, "/", "programme",
                f"Programme de {c['nom']} ({mouv}) en texte intégral, capturé le {date_fr(doc['capture_date'])}, avec sa source et son empreinte.")


def page_sans(s: dict, election, sans) -> str:
    mouv = s.get("mouvement") or "Mouvement non indiqué sur le site"
    decl = ""
    if s.get("declaration_date") or s.get("declaration_source"):
        src = f', <a href="{e(s["declaration_source"])}" rel="noopener">source</a>' if s.get("declaration_source") else ""
        decl = f'<dt>Candidature annoncée</dt><dd>{date_fr(s.get("declaration_date"))}{src}</dd>'
    corps = f"""
<a class="retour" href="/">← {e(election["nom"].replace("Élection présidentielle", "Présidentielle"))}</a>
<div class="sans-tete">
<span class="badge gris">Pas de programme publié</span>
<h1>{e(s["nom"])}</h1>
<p>{e(mouv)} · site officiel : <a href="{e(s["site_officiel"])}" rel="noopener">{e(s["site_officiel"])}</a></p>
</div>
<section class="encadre">
<p class="fort">Aucun programme publié par le candidat à ce jour.</p>
<p>Ce qui circule ailleurs (résumés, comparateurs, articles) est reconstitué à partir de déclarations ou de documents de parti. Ce ne sont pas ses mots : nous ne les reproduisons pas.</p>
<p>Cette page sera mise à jour le jour où le candidat publie un programme sur son site officiel.</p>
</section>
<dl class="fiche large">
<dt>Ce que nous avons vérifié</dt><dd>{e(s["constat"])}</dd>
<dt>Dernière vérification</dt><dd>{date_fr(s["verifie_le"])}, vérification chaque semaine</dd>
{decl}
</dl>
<p class="signal">Vous avez connaissance d'un programme publié sur le site officiel de ce candidat ? <a href="{ISSUES}" rel="noopener">Signalez-le-nous</a>.</p>
"""
    return page(f"{s['nom']} · pas de programme publié", corps, "/", "etroit")


def pilules(sujets, courant: str | None) -> str:
    out = []
    for s in sujets:
        cur = ' aria-current="page"' if s["id"] == courant else ""
        out.append(f'<a href="/sujets/{e(s["id"])}/"{cur}>{e(s["nom"])}</a>')
    return '<div class="pilules">' + "".join(out) + "</div>"


INTRO_SUJETS = ("Un sujet est une simple liste de mots. Le site cherche ces mots dans chaque programme et affiche "
                "les passages exacts. Personne ne décide de quoi parle un candidat : seuls ses mots comptent.")
URL_SUJETS = DEPOT + "/blob/main/sujets/sujets.json"


def page_sujets_index(sujets) -> str:
    lignes = "".join(f'<li><a href="/sujets/{e(s["id"])}/">{e(s["nom"])}</a> : {e(" · ".join(s["mots"]))}</li>' for s in sujets)
    corps = f"""
<h1 class="serif">Par sujet</h1>
<p class="intro">{INTRO_SUJETS}</p>
{pilules(sujets, None)}
<section class="cadre">
<div><p class="t">Les listes de mots</p><p>Identiques pour tous les candidats, publiques et modifiables par quiconque : <a href="{URL_SUJETS}" rel="noopener">sujets/sujets.json sur le code source</a>.</p></div>
<div><p class="t">Ordre d'affichage</p><p>Tiré au sort à chaque chargement de la page. Rechargez : l'ordre change. Personne n'est jamais premier.</p></div>
</section>
<div class="prose"><ul>{lignes}</ul></div>
"""
    return page("Par sujet", corps, "/sujets/")


def bloc_passages(passages, lien: str, classe_blockquote: str = "passage", lieu: bool = False) -> str:
    def bq(p):
        ou = ""
        if lieu:
            where = f"p. {p['p']} · " if p.get("p") else ""
            ou = f'<span class="ou">{where}<a href="{e(lien)}#{e(p["a"])}">lire dans le texte intégral</a></span>' if p.get("a") else f'<span class="ou">{where}<a href="{e(lien)}">lire dans le texte intégral</a></span>'
        return f'<blockquote class="{classe_blockquote}">« {surligner(p)} »{ou}</blockquote>'
    visibles = "".join(bq(p) for p in passages[:PASSAGES_VISIBLES])
    reste = passages[PASSAGES_VISIBLES:]
    if reste:
        visibles += (f'<details class="suite"><summary>Afficher les {nombre(len(reste))} autres passages</summary><div>'
                     + "".join(bq(p) for p in reste) + "</div></details>")
    return visibles


def page_sujet(sujet, resultats_sujet, candidats, sans, sujets) -> str:
    blocs, absents = [], []
    for c in candidats:
        r = resultats_sujet[c["slug"]]
        if r["n"] == 0:
            absents.append(c)
            continue
        lien = f"/candidats/{c['slug']}/"
        blocs.append((slugify(c["nom"]), f"""<section class="resultat" id="{e(c["slug"])}" data-slug="{e(c["slug"])}">
<div class="tete"><h2>{e(c["nom"])}</h2><span class="n">{nombre(r["n"])} passage{"s" if r["n"] > 1 else ""}</span></div>
{bloc_passages(r["passages"], lien, lieu=True)}
<a class="tous" href="{e(lien)}">Tous les passages, dans le texte intégral →</a>
</section>"""))
    blocs.sort(key=lambda x: x[0])
    p_absents = ""
    if absents:
        noms = ", ".join(e(c["nom"]) for c in absents)
        p_absents = f'<p class="absents">Aucun de ces mots n\'apparaît dans le programme de : {noms}. Cela ne signifie pas qu\'il n\'a pas de position sur le sujet.</p>'
    p_sans = ""
    if sans["candidats"]:
        noms = ", ".join(f'<a href="/candidats/{e(s["slug"])}/">{e(s["nom"])}</a>' for s in sans["candidats"])
        p_sans = f'<p class="absents">Pas de programme publié : {noms}.</p>'
    corps = f"""
<h1 class="serif">Par sujet</h1>
<p class="intro">{INTRO_SUJETS}</p>
{pilules(sujets, sujet["id"])}
<section class="cadre">
<div><p class="t">Mots recherchés pour « {e(sujet["nom"])} »</p><p>{e(" · ".join(sujet["mots"]))} · <a href="{URL_SUJETS}" rel="noopener">liste publique, modifiable sur le code source</a></p>
<p class="haut-petit">Mot entier ou suivi d'une marque de pluriel, sans tenir compte de la casse ni des accents. Un passage est la phrase complète qui contient le mot.</p></div>
<div><p class="t">Ordre d'affichage</p><p>Tiré au sort à chaque chargement de la page. Rechargez : l'ordre change. Personne n'est jamais premier.</p>
<p class="haut-moyen"><a href="/comparer/?sujet={e(sujet["id"])}">Comparer 2 à 4 candidats sur ce sujet →</a></p></div>
</section>
<div class="resultats" data-shuffle>
{chr(10).join(h for _, h in blocs)}
</div>
{p_absents}
{p_sans}
"""
    return page(f"Sujet : {sujet['nom']}", corps, "/sujets/")


def page_comparer(sujets, candidats) -> str:
    options = "".join(f'<option value="{e(s["id"])}">{e(s["nom"])}</option>' for s in sujets)
    cases = "".join(f'<label data-slug="{e(c["slug"])}"><input type="checkbox" name="c" value="{e(c["slug"])}"> {e(c["nom"])}</label>' for c in candidats)
    corps = f"""
<h1 class="serif">Comparer sur un sujet</h1>
<p class="intro">Choisissez un sujet et jusqu'à quatre candidats. Chaque colonne affiche, mot pour mot, les passages de son programme qui contiennent les mots du sujet. Rien n'est résumé, rien n'est noté.</p>
<form class="comparer" id="form-comparer" action="/comparer/" method="get">
<div class="champ"><label class="lib" for="sujet">Sujet</label><select id="sujet" name="sujet">{options}</select></div>
<div class="champ large"><span class="lib" id="lib-cand">Candidats (ordre tiré au sort, 4 au plus)</span><div class="cases" role="group" aria-labelledby="lib-cand" data-shuffle>{cases}</div></div>
<button type="submit" class="btn plein petit">Comparer</button>
</form>
<p class="mots" id="mots-sujet"></p>
<div class="colonnes" id="colonnes" aria-live="polite"></div>
<noscript><p class="absents">La comparaison se fait dans votre navigateur et nécessite JavaScript. Sans lui, consultez la <a href="/sujets/">recherche par sujet</a>, qui fonctionne sans script.</p></noscript>
"""
    return page("Comparer sur un sujet", corps, "/comparer/")


def page_recherche() -> str:
    corps = f"""
<h1 class="serif">Rechercher un mot</h1>
<p class="intro">Le site cherche le mot dans chaque programme et affiche les phrases exactes qui le contiennent, mot entier ou suivi d'une marque de pluriel, sans tenir compte de la casse ni des accents. Rien n'est résumé.</p>
<form class="recherche" action="/recherche/" method="get" role="search">
<label for="q" class="sr-only">Rechercher un mot dans tous les programmes</label>
<input id="q" name="q" type="search" placeholder="Un mot dans tous les programmes : retraite, école, énergie..." required>
<button type="submit" class="btn plein">Rechercher</button>
</form>
<p class="mots" id="etat-recherche"></p>
<div class="resultats" id="resultats-recherche" aria-live="polite"></div>
<p class="absents" id="absents-recherche"></p>
<noscript><p class="absents">La recherche se fait dans votre navigateur et nécessite JavaScript. Sans lui, consultez la <a href="/sujets/">recherche par sujet</a>, qui fonctionne sans script.</p></noscript>
"""
    return page("Rechercher", corps, "")


# Markdown minimal (titres, paragraphes, listes, gras, italique, code, liens)

def inline_md(s: str) -> str:
    s = e(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def markdown_min(md: str) -> str:
    out, para, liste, code = [], [], None, None

    def flush_para():
        if para:
            out.append("<p>" + inline_md(" ".join(para)) + "</p>")
            para.clear()

    def flush_liste():
        nonlocal liste
        if liste:
            out.append(f"</{liste}>")
            liste = None

    for ligne in md.split("\n"):
        if code is not None:
            if ligne.startswith("```"):
                out.append("<pre><code>" + e("\n".join(code)) + "</code></pre>")
                code = None
            else:
                code.append(ligne)
            continue
        if ligne.startswith("```"):
            flush_para(); flush_liste(); code = []
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", ligne)
        if m:
            flush_para(); flush_liste()
            n = len(m.group(1))
            out.append(f"<h{n}>{inline_md(m.group(2))}</h{n}>")
            continue
        m = re.match(r"^\s*[-*]\s+(.*)$", ligne)
        if m:
            flush_para()
            if liste != "ul":
                flush_liste(); out.append("<ul>"); liste = "ul"
            out.append("<li>" + inline_md(m.group(1)) + "</li>")
            continue
        m = re.match(r"^\s*\d+\.\s+(.*)$", ligne)
        if m:
            flush_para()
            if liste != "ol":
                flush_liste(); out.append("<ol>"); liste = "ol"
            out.append("<li>" + inline_md(m.group(1)) + "</li>")
            continue
        if not ligne.strip():
            flush_para(); flush_liste()
            continue
        para.append(ligne.strip())
    flush_para(); flush_liste()
    return "\n".join(out)


def page_charte() -> str:
    md = CHARTE.read_text(encoding="utf-8")
    corps = f'<div class="prose">{markdown_min(md)}<p><a href="{DEPOT}/blob/main/CHARTE.md" rel="noopener">Historique des modifications de la charte sur le code source</a></p></div>'
    return page("Charte de neutralité", corps, "/charte/")


def page_mentions() -> str:
    def champ(x):
        return f'<span class="a-completer">[{e(x)}]</span>'
    corps = f"""
<div class="prose">
<h1>Mentions légales</h1>
<h2>Éditeur</h2>
<p>{champ("ÉDITEUR : nom de l'association, forme juridique, adresse du siège, numéro RNA")}</p>
<p>Directeur de la publication : {champ("DIRECTEUR DE PUBLICATION")}</p>
<p>Contact : <a href="mailto:{CONTACT}">{CONTACT}</a></p>
<h2>Hébergeur</h2>
<p>{champ("HÉBERGEUR : Scalingo SAS, 3 place de Haguenau, 67000 Strasbourg, France")}</p>
<h2>Données personnelles</h2>
<p>Le site ne dépose aucun cookie, n'utilise aucun traceur et ne charge aucune ressource depuis un domaine tiers. Deux compteurs anonymes sont tenus : le nombre de visites et le nombre de validations de la démarche. Pour limiter les clics répétés sur le bouton de validation, une empreinte salée (SHA-256) de l'adresse IP et du jour est conservée en mémoire pendant 24 heures ; l'adresse IP elle-même n'est ni enregistrée ni journalisée par le site.</p>
<h2>Contenus</h2>
<p>Les programmes reproduits restent la propriété de leurs auteurs. Ils sont publiés à des fins d'information des électeurs, sans modification, avec indication de leur source, de leur date de capture et de leur empreinte. Toute demande concernant un texte : <a href="mailto:{CONTACT}">{CONTACT}</a> ou <a href="{ISSUES}" rel="noopener">signalement public</a>.</p>
<h2>Code source</h2>
<p>Le code du site est publié sous licence MIT : <a href="{DEPOT}" rel="noopener">{DEPOT}</a>.</p>
</div>
"""
    return page("Mentions légales", corps, "")


def page_verifier(candidats, verifications) -> str:
    lignes = []
    for v in verifications:
        etat = "identique" if v["ok"] is True else ("DIFFÉRENTE" if v["ok"] is False else "non recalculée")
        lignes.append(f'<tr><td>{e(v["candidat"])}</td><td><a href="{e(v["url"])}">{e(v["fichier"])}</a></td><td class="mono">{e(v["sha256"])}</td><td>{e(etat)}</td></tr>')
    corps = f"""
<div class="prose">
<h1>Vérifier une empreinte</h1>
<p>Chaque fichier publié porte une empreinte SHA-256, inscrite dans son <code>meta.json</code> sur le code source. Pour vérifier qu'un fichier téléchargé ici est bien celui qui a été capturé sur le site du candidat :</p>
<ol>
<li>Téléchargez le fichier (colonne « Fichier »).</li>
<li>Dans un terminal, calculez son empreinte :<pre><code>shasum -a 256 chemin/vers/le/fichier</code></pre>(sous Windows : <code>certutil -hashfile chemin\\vers\\le\\fichier SHA256</code>).</li>
<li>Comparez le résultat à la colonne « Empreinte ». Un seul caractère différent signifie que le fichier a été modifié.</li>
</ol>
<p>Pour les programmes publiés sous forme de pages web, l'empreinte conservée est celle du HTML de chaque page tel que servi au moment de la capture ; elle figure dans l'onglet « Voir l'original » de la page du candidat.</p>
<p>La colonne « À la génération » indique si l'empreinte a été recalculée sur le fichier réellement publié, au moment où ce site a été construit, et si elle est identique à celle déclarée.</p>
</div>
<table class="tab haut">
<thead><tr><th scope="col">Candidat</th><th scope="col">Fichier</th><th scope="col">Empreinte SHA-256 déclarée</th><th scope="col">À la génération</th></tr></thead>
<tbody>{"".join(lignes)}</tbody>
</table>
"""
    return page("Vérifier une empreinte", corps, "")


def page_404() -> str:
    corps = '<div class="prose"><h1>Page introuvable</h1><p>Cette adresse ne correspond à aucune page. <a href="/">Retour à l\'accueil</a>.</p></div>'
    return page("Page introuvable", corps, "")


# ----------------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------------

def ecrire(p: Path, contenu: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(contenu, encoding="utf-8")


def construire(dist: Path):
    election, candidats, sans, sujets = charger()
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)
    shutil.copytree(STATIC, dist / "static")

    # Recherche par sujet, précalculée
    resultats = {}
    for s in sujets:
        resultats[s["id"]] = {}
        for c in candidats:
            ps = chercher(c["texte"], c["texte_norm"], c["phrases"], s["mots"], c["pages"], c["sections"])
            resultats[s["id"]][c["slug"]] = {"n": len(ps), "mots": sum(p["w"] for p in ps), "passages": ps}

    # Pages
    ecrire(dist / "index.html", page_accueil(election, candidats, sans))
    verifications = []
    for c in candidats:
        ecrire(dist / "candidats" / c["slug"] / "index.html", page_candidat(c, election))
        doc = c["doc"]
        # copie des fichiers texte et original + vérification des empreintes
        src_t = c["dossier"] / doc["fichier_texte"]
        dst_t = dist / "candidats" / c["slug"] / "texte" / src_t.name
        dst_t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_t, dst_t)
        h = sha256_fichier(src_t)
        ok = h == doc.get("sha256_texte")
        if not ok:
            print(f"AVERTISSEMENT : empreinte du texte différente pour {c['slug']} ({h})", file=sys.stderr)
        verifications.append({"candidat": c["nom"], "fichier": f"texte/{src_t.name}", "url": f"/candidats/{c['slug']}/texte/{src_t.name}", "sha256": doc.get("sha256_texte") or "", "ok": ok})
        if doc.get("fichier_original"):
            src_o = c["dossier"] / doc["fichier_original"]
            if src_o.is_file():
                dst_o = dist / "candidats" / c["slug"] / "original" / src_o.name
                dst_o.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src_o, dst_o)
                h = sha256_fichier(src_o)
                ok = h == doc.get("sha256_original")
                if not ok:
                    print(f"AVERTISSEMENT : empreinte de l'original différente pour {c['slug']} ({h})", file=sys.stderr)
                verifications.append({"candidat": c["nom"], "fichier": f"original/{src_o.name}", "url": f"/candidats/{c['slug']}/original/{src_o.name}", "sha256": doc.get("sha256_original") or "", "ok": ok})
    for s in sans["candidats"]:
        ecrire(dist / "candidats" / s["slug"] / "index.html", page_sans(s, election, sans))
    ecrire(dist / "sujets" / "index.html", page_sujets_index(sujets))
    for s in sujets:
        ecrire(dist / "sujets" / s["id"] / "index.html", page_sujet(s, resultats[s["id"]], candidats, sans, sujets))
    ecrire(dist / "comparer" / "index.html", page_comparer(sujets, candidats))
    ecrire(dist / "recherche" / "index.html", page_recherche())
    ecrire(dist / "charte" / "index.html", page_charte())
    ecrire(dist / "mentions-legales" / "index.html", page_mentions())
    ecrire(dist / "verifier" / "index.html", page_verifier(candidats, verifications))
    ecrire(dist / "404.html", page_404())

    # Données pour le client
    (dist / "data").mkdir()
    cands_json = [{"slug": c["slug"], "nom": c["nom"], "mouvement": c["mouvement"] or "Mouvement non indiqué sur le site",
                   "mots_total": c["mots_total"], "format": c["doc"].get("format")} for c in candidats]
    sujets_json = {
        "sujets": [{"id": s["id"], "nom": s["nom"], "mots": s["mots"]} for s in sujets],
        "candidats": cands_json,
        "resultats": {sid: {slug: {"n": r["n"], "mots": r["mots"], "passages": [{k: p[k] for k in ("t", "m", "p", "a", "w")} for p in r["passages"]]}
                            for slug, r in rs.items()} for sid, rs in resultats.items()},
    }
    ecrire(dist / "data" / "sujets.json", json.dumps(sujets_json, ensure_ascii=False, separators=(",", ":")))

    textes = {c["slug"]: {"nom": c["nom"], "mouvement": c["mouvement"] or "Mouvement non indiqué sur le site",
                          "format": c["doc"].get("format"), "texte": c["texte"]} for c in candidats}
    brut = json.dumps(textes, ensure_ascii=False, separators=(",", ":"))
    if len(brut.encode("utf-8")) > LIMITE_TEXTES:
        (dist / "data" / "textes").mkdir()
        for slug, t in textes.items():
            ecrire(dist / "data" / "textes" / f"{slug}.json", json.dumps(t, ensure_ascii=False, separators=(",", ":")))
        ecrire(dist / "data" / "textes.json", json.dumps({"decoupe": True, "candidats": cands_json}, ensure_ascii=False, separators=(",", ":")))
    else:
        ecrire(dist / "data" / "textes.json", brut)

    ecrire(dist / "data" / "candidats.json", json.dumps(cands_json, ensure_ascii=False, separators=(",", ":")))
    ecrire(dist / "robots.txt", "User-agent: *\nAllow: /\nDisallow: /api/\n")

    n_pages = sum(1 for _ in dist.rglob("index.html"))
    print(f"dist/ généré : {n_pages} pages HTML, {len(candidats)} programmes, {len(sans['candidats'])} candidats sans programme, {len(sujets)} sujets")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=str(RACINE / "dist"))
    args = ap.parse_args()
    return construire(Path(args.dist))


if __name__ == "__main__":
    sys.exit(main())
