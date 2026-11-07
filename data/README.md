# Données

Un dossier par élection, un sous-dossier par candidat.

## `election.json`

Métadonnées de l'élection : dates, statut (`candidats-declares` ou `liste-officielle`), liens vers les sources institutionnelles.

## `candidats/<slug>/meta.json`

```json
{
  "slug": "nom-prenom",
  "nom": "Nom tel qu'écrit par le candidat ou sur la liste officielle",
  "mouvement": "Nom du parti ou mouvement, tel que le candidat l'écrit",
  "site_officiel": "https://...",
  "statut": "declare",
  "declaration_date": "AAAA-MM-JJ",
  "declaration_source": "https://... (annonce publique)",
  "documents": [
    {
      "id": "programme-v1",
      "type": "programme",
      "titre": "Titre exact du document",
      "source_url": "https://...",
      "capture_date": "AAAA-MM-JJTHH:MM:SSZ",
      "format": "pdf",
      "fichier_original": "original/programme-v1.pdf",
      "fichier_texte": "texte/programme-v1.txt",
      "sha256_original": "...",
      "sha256_texte": "...",
      "pages": 0,
      "mots": 0,
      "remplace": null
    }
  ]
}
```

- `statut` : `declare` (avant la liste officielle), `officiel`, `non-retenu`, `retire`.
- `type` : `programme`, `profession-de-foi`, `profession-de-foi-falc`.
- `remplace` : identifiant de la version précédente si le candidat a modifié son document. Toutes les versions sont conservées.

## Règles

- Les fichiers de `original/` ne sont jamais modifiés après leur capture.
- Les fichiers de `texte/` contiennent l'extraction brute. Les seules corrections admises sont celles de la charte, section 6, et chacune est tracée dans l'historique du dépôt.
- Rien n'est ajouté qui ne vienne de l'une des trois sources de la charte.
