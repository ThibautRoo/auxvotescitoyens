/* auxvotescitoyens.fr : script unique, sans dépendance, sans cookie, sans
   requête vers un autre domaine. */
(function () {
  "use strict";

  /* ---------- Utilitaires ---------- */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function nombre(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " "); }
  function dateFr(iso) {
    if (!iso) return "–";
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
    return m ? m[3] + "/" + m[2] + "/" + m[1] : iso;
  }

  /* ---------- Ordre tiré au sort (Fisher-Yates) ---------- */
  function melanger(conteneur) {
    var enfants = Array.prototype.slice.call(conteneur.children);
    for (var i = enfants.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = enfants[i]; enfants[i] = enfants[j]; enfants[j] = t;
    }
    enfants.forEach(function (el) { conteneur.appendChild(el); });
  }
  $$("[data-shuffle]").forEach(melanger);
  $$("[data-retirer]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      $$("[data-shuffle]").forEach(melanger);
      var t = btn.closest(".tirage");
      if (t) { t.classList.remove("anim"); void t.offsetWidth; t.classList.add("anim"); }
    });
  });

  /* ---------- Onglets (page programme) ---------- */
  var tablist = $('[role="tablist"]');
  if (tablist) {
    var tabs = $$('[role="tab"]', tablist);
    function activer(tab) {
      tabs.forEach(function (t) {
        var sel = t === tab;
        t.setAttribute("aria-selected", sel ? "true" : "false");
        t.tabIndex = sel ? 0 : -1;
        var panneau = document.getElementById(t.getAttribute("aria-controls"));
        if (panneau) panneau.hidden = !sel;
      });
    }
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { activer(t); });
      t.addEventListener("keydown", function (ev) {
        var k = ev.key, n = null;
        if (k === "ArrowRight") n = tabs[(i + 1) % tabs.length];
        if (k === "ArrowLeft") n = tabs[(i - 1 + tabs.length) % tabs.length];
        if (n) { ev.preventDefault(); activer(n); n.focus(); }
      });
    });
    // Un lien vers #onglet-xxx ouvre l'onglet correspondant
    function ouvrirDepuisHash() {
      var h = location.hash.replace("#", "");
      if (!h) return;
      var cible = document.getElementById(h);
      if (!cible) return;
      var panneau = cible.closest ? cible.closest('[role="tabpanel"]') : null;
      if (panneau) {
        var tab = tabs.filter(function (t) { return t.getAttribute("aria-controls") === panneau.id; })[0];
        if (tab) { activer(tab); cible.scrollIntoView(); }
      }
    }
    ouvrirDepuisHash();
    window.addEventListener("hashchange", ouvrirDepuisHash);
    $$('a[href^="#onglet-"]').forEach(function (a) {
      a.addEventListener("click", function () {
        var id = a.getAttribute("href").slice(1);
        var tab = tabs.filter(function (t) { return t.getAttribute("aria-controls") === id; })[0];
        if (tab) activer(tab);
      });
    });
  }

  /* ---------- Compteurs (visites, soutiens) ---------- */
  function afficherCompteurs(d) {
    var p = $("#compteurs");
    if (p && d && typeof d.visites === "number") {
      p.textContent = nombre(d.visites) + " visites depuis le " + dateFr(d.depuis) + " · " + nombre(d.soutiens) + " validations";
    }
    var s = $("#nb-soutiens");
    if (s && d && typeof d.soutiens === "number") s.textContent = nombre(d.soutiens);
  }
  function api(methode, chemin) {
    return fetch(chemin, { method: methode, credentials: "omit", cache: "no-store", keepalive: methode === "POST" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }
  api("POST", "/api/visite").then(function (d) {
    if (d) afficherCompteurs(d); else api("GET", "/api/compteurs").then(afficherCompteurs);
  });
  var btn = $("#btn-soutien");
  if (btn) {
    btn.addEventListener("click", function () {
      btn.disabled = true;
      api("POST", "/api/soutien").then(function (d) {
        if (d) { afficherCompteurs(d); btn.lastChild.textContent = d.deja ? "Déjà compté aujourd'hui" : "Merci, c'est compté"; }
        else { btn.disabled = false; }
      });
    });
  }

  /* ---------- Recherche mécanique (même règle que site/build.py) ---------- */
  function normaliser(t) {
    // minuscules, sans accents, même longueur que l'original (caractère par caractère)
    var out = "";
    for (var i = 0; i < t.length; i++) {
      var c = t[i];
      var d = c.normalize("NFD");
      out += (d.length ? d[0] : c).toLowerCase();
    }
    return out;
  }
  function motifMot(mot) {
    var n = normaliser(mot).trim().split(/\s+/).map(function (p) { return p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }).join("\\s+");
    return new RegExp("(?<![\\p{L}\\p{N}_])" + n + "(?:s|x|es)?(?![\\p{L}\\p{N}_])", "gu");
  }
  var SEP = /^=== (.*?) === (\S+)\s*$/;
  function phrases(texte) {
    var res = [], pos = 0, lignes = texte.split("\n");
    for (var li = 0; li < lignes.length; li++) {
      var ligne = lignes[li], finLigne = pos + ligne.length;
      if (ligne.trim() && !SEP.test(ligne)) {
        var debut = pos, morceaux = ligne.split(/(?<=[.!?])\s+/);
        for (var k = 0; k < morceaux.length; k++) {
          var d = debut, f = d + morceaux[k].length;
          while (d < f && /\s/.test(texte[d])) d++;
          while (f > d && /\s/.test(texte[f - 1])) f--;
          if (f > d) res.push([d, f]);
          debut += morceaux[k].length;
          while (debut < finLigne && /\s/.test(texte[debut])) debut++;
        }
      }
      pos = finLigne + 1;
    }
    return res;
  }
  function structure(texte) {
    var pages = [], re = /\f/g, m;
    while ((m = re.exec(texte))) pages.push(m.index + 1);
    var sections = [], re2 = /^=== (.*?) === (\S+)\s*$/gm;
    while ((m = re2.exec(texte))) sections.push(m.index);
    return { pages: pages, sections: sections };
  }
  function compterAvant(tab, pos) { var n = 0; while (n < tab.length && tab[n] <= pos) n++; return n; }
  function chercher(texte, mots) {
    var norm = normaliser(texte), motifs = mots.map(motifMot), st = structure(texte), out = [];
    phrases(texte).forEach(function (df) {
      var seg = norm.slice(df[0], df[1]), marques = [];
      motifs.forEach(function (re) { re.lastIndex = 0; var m; while ((m = re.exec(seg))) { marques.push([m.index, m.index + m[0].length]); if (m[0].length === 0) re.lastIndex++; } });
      if (!marques.length) return;
      marques.sort(function (a, b) { return a[0] - b[0]; });
      var fusion = [];
      marques.forEach(function (ab) { if (fusion.length && ab[0] <= fusion[fusion.length - 1][1]) fusion[fusion.length - 1][1] = Math.max(fusion[fusion.length - 1][1], ab[1]); else fusion.push(ab.slice()); });
      var page = st.pages.length ? compterAvant(st.pages, df[0]) + 1 : null;
      var sec = st.sections.length ? compterAvant(st.sections, df[0]) : 0;
      var t = texte.slice(df[0], df[1]);
      out.push({ t: t, m: fusion, p: page, a: page ? "page-" + page : (sec ? "s-" + sec : ""), w: t.split(/\s+/).filter(Boolean).length });
    });
    return out;
  }
  function surligner(p) {
    var out = "", pos = 0;
    p.m.forEach(function (ab) { out += esc(p.t.slice(pos, ab[0])) + "<mark>" + esc(p.t.slice(ab[0], ab[1])) + "</mark>"; pos = ab[1]; });
    return out + esc(p.t.slice(pos));
  }
  function blocPassages(passages, lien, visibles) {
    function bq(p) {
      var ou = (p.p ? "p. " + p.p + " · " : "") + '<a href="' + esc(lien) + (p.a ? "#" + esc(p.a) : "") + '">lire dans le texte intégral</a>';
      return '<blockquote class="passage">« ' + surligner(p) + ' »<span class="ou">' + ou + "</span></blockquote>";
    }
    var h = passages.slice(0, visibles).map(bq).join("");
    var reste = passages.slice(visibles);
    if (reste.length) h += '<details class="suite"><summary>Afficher les ' + nombre(reste.length) + " autres passages</summary><div>" + reste.map(bq).join("") + "</div></details>";
    return h;
  }
  function chargerJSON(url) { return fetch(url, { credentials: "omit" }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }); }

  /* ---------- Comparer ---------- */
  var formComparer = $("#form-comparer");
  if (formComparer) {
    var cases = $$('input[name="c"]', formComparer), MAX = 4;
    function majCases() {
      var coches = cases.filter(function (c) { return c.checked; }).length;
      cases.forEach(function (c) {
        c.disabled = !c.checked && coches >= MAX;
        c.parentNode.classList.toggle("coche", c.checked);
      });
    }
    cases.forEach(function (c) { c.addEventListener("change", majCases); });
    var params = new URLSearchParams(location.search);
    var sujetId = params.get("sujet");
    var choisis = params.getAll("c").slice(0, MAX);
    var select = $("#sujet");
    if (sujetId && select) select.value = sujetId;
    cases.forEach(function (c) { c.checked = choisis.indexOf(c.value) >= 0; });
    majCases();
    var colonnes = $("#colonnes"), pMots = $("#mots-sujet");
    if (choisis.length) {
      pMots.textContent = "Chargement…";
      chargerJSON("/data/sujets.json").then(function (data) {
        var sujet = data.sujets.filter(function (s) { return s.id === (select.value || sujetId); })[0] || data.sujets[0];
        pMots.innerHTML = "Mots recherchés pour « " + esc(sujet.nom) + " » : " + esc(sujet.mots.join(" · ")) + '. <a href="https://github.com/ThibautRoo/auxvotescitoyens/blob/main/sujets/sujets.json" rel="noopener">Voir ou proposer une modification de la liste</a>.';
        var html = "";
        // ordre : celui des cases (déjà tiré au sort)
        var ordre = cases.filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
        ordre.forEach(function (slug) {
          var cand = data.candidats.filter(function (c) { return c.slug === slug; })[0];
          var r = data.resultats[sujet.id][slug];
          if (!cand || !r) return;
          var lien = "/candidats/" + slug + "/";
          var tete = '<div class="tete"><h2>' + esc(cand.nom) + '</h2><p class="mouv">' + esc(cand.mouvement) + "</p>";
          if (r.n === 0) {
            html += '<section class="colonne">' + tete + '<div class="jauge"><div class="barre"></div><span>0 passage</span></div></div>' +
              '<div class="vide">Aucun de ces mots n\'apparaît dans son programme. Cela ne signifie pas qu\'il n\'a pas de position sur le sujet : il peut en parler avec d\'autres mots. <a href="' + lien + '">Lire son programme intégral</a>.</div></section>';
            return;
          }
          var pct = cand.mots_total ? Math.round(1000 * r.mots / cand.mots_total) / 10 : 0;
          html += '<section class="colonne">' + tete +
            '<div class="jauge"><div class="barre" role="img" aria-label="' + pct + ' % des mots du programme"><div data-pct="' + Math.min(100, pct) + '"></div></div><span>' + nombre(r.n) + " passage" + (r.n > 1 ? "s" : "") + " · " + nombre(r.mots) + " mots</span></div>" +
            '<p class="explic">La barre indique la part du programme contenant ces mots (' + pct + " % de " + nombre(cand.mots_total) + " mots). C'est un comptage, pas une évaluation.</p></div>" +
            '<div class="corps">' + blocPassages(r.passages, lien, 5) +
            '<a class="tous" href="/sujets/' + esc(sujet.id) + "/#" + esc(slug) + '">Tous les passages (' + nombre(r.n) + ") →</a></div></section>";
        });
        colonnes.innerHTML = html;
        $$("[data-pct]", colonnes).forEach(function (el) { el.style.width = el.getAttribute("data-pct") + "%"; });
      }).catch(function () { pMots.textContent = "Les données n'ont pas pu être chargées. Rechargez la page."; });
    } else {
      pMots.textContent = "Cochez 2 à 4 candidats, puis « Comparer ».";
    }
  }

  /* ---------- Recherche libre ---------- */
  var resultatsRecherche = $("#resultats-recherche");
  if (resultatsRecherche) {
    var q = (new URLSearchParams(location.search).get("q") || "").trim();
    var champ = $("#q"), etat = $("#etat-recherche"), absents = $("#absents-recherche");
    if (champ) champ.value = q;
    if (q.length >= 2) {
      document.title = "« " + q + " » · auxvotescitoyens.fr";
      etat.textContent = "Recherche de « " + q + " » dans tous les programmes…";
      chargerJSON("/data/textes.json").then(function (data) {
        if (data.decoupe) {
          return Promise.all(data.candidats.map(function (c) {
            return chargerJSON("/data/textes/" + c.slug + ".json").then(function (t) { t.slug = c.slug; return t; });
          }));
        }
        return Object.keys(data).map(function (slug) { data[slug].slug = slug; return data[slug]; });
      }).then(function (textes) {
        var blocs = [], sans = [], total = 0;
        textes.forEach(function (t) {
          var ps = chercher(t.texte, [q]);
          if (!ps.length) { sans.push(t.nom); return; }
          total += ps.length;
          var lien = "/candidats/" + t.slug + "/";
          blocs.push('<section class="resultat"><div class="tete"><h2>' + esc(t.nom) + '</h2><span class="n">' + nombre(ps.length) + " passage" + (ps.length > 1 ? "s" : "") + "</span></div>" +
            blocPassages(ps, lien, 5) + '<a class="tous" href="' + lien + '">Tous les passages, dans le texte intégral →</a></section>');
        });
        // ordre tiré au sort
        for (var i = blocs.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)); var tmp = blocs[i]; blocs[i] = blocs[j]; blocs[j] = tmp; }
        resultatsRecherche.innerHTML = blocs.join("");
        etat.textContent = total ? nombre(total) + " passage" + (total > 1 ? "s" : "") + " contenant « " + q + " », dans " + blocs.length + " programme" + (blocs.length > 1 ? "s" : "") + ". Ordre tiré au sort." : "Aucun passage ne contient « " + q + " ».";
        absents.textContent = sans.length ? "Ce mot n'apparaît pas dans le programme de : " + sans.join(", ") + ". Cela ne signifie pas qu'il n'a pas de position sur le sujet." : "";
      }).catch(function () { etat.textContent = "Les textes n'ont pas pu être chargés. Rechargez la page."; });
    } else if (q) {
      etat.textContent = "Saisissez au moins deux caractères.";
    }
  }
})();
