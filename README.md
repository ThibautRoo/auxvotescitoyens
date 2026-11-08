# auxvotescitoyens.fr

**Les programmes électoraux, mot pour mot. Rien d'autre.**

Site citoyen, neutre, open source et gratuit, qui publie en texte intégral (verbatim) les programmes des candidats aux élections françaises, tels qu'ils les ont écrits.

- Aucun résumé
- Aucune interprétation
- Aucun favoritisme
- Aucun sondage
- Aucun financement politique
- Code ouvert à tous

Les règles complètes sont dans la [Charte de neutralité](CHARTE.md). Elle fait foi sur tout le reste.

## Pourquoi

Il existe des comparateurs de programmes, des tests d'affinité, des résumés par thème. Tous résument, classent ou interprètent. Ce site ne fait rien de tout cela : il archive et affiche le texte des candidats, avec sa source, sa date et son empreinte numérique, pour que chacun lise par lui-même.

## Sources

Trois sources, aucune autre :

1. **Le Conseil constitutionnel** : sa liste officielle fixe qui est candidat.
2. **Le ministère de l'Intérieur** (et, pour la présidentielle, la CNCCEP) : la profession de foi officielle de chaque candidat.
3. **Le site officiel du candidat** : son programme complet, tel qu'il le publie lui-même.

Avant la publication de la liste officielle, le site affiche les candidats déclarés, avec un bandeau « liste non officielle », selon la règle d'inclusion définie dans la charte.

## Organisation du dépôt

```
CHARTE.md                 Règles de neutralité (fait foi)
data/
  presidentielle-2027/
    election.json         Métadonnées de l'élection
    candidats-sans-programme.json
                          Candidats déclarés sans programme écrit, avec le constat et la date de vérification
    candidats/
      <slug>/
        meta.json         Candidat, source, date de capture, empreintes
        original/         Documents tels que téléchargés (PDF, HTML)
        texte/            Texte brut extrait, sans modification
sujets/
  sujets.json             Listes de mots publiques pour la recherche par sujet
site/                     Générateur du site statique (à venir)
```

## Vérifier un document

Chaque document porte son empreinte SHA-256 dans `meta.json`. Pour vérifier qu'un fichier n'a pas été modifié :

```
shasum -a 256 chemin/vers/le/fichier.pdf
```

Le résultat doit être identique à celui indiqué dans `meta.json`.

## Signaler une erreur

Si un texte publié ne correspond pas à sa source, ouvrez une *issue* sur ce dépôt ou écrivez à contact@auxvotescitoyens.fr. Toute correction est tracée dans l'historique public.

## Licence

Le code est sous licence MIT. Les programmes des candidats restent la propriété de leurs auteurs ; ils sont reproduits à des fins d'information des électeurs, sans modification, avec indication de la source.
