# Changelog

## 0.1.0

- CLI `pippo-transcript` pour PDF/images vers HTML, Markdown, JSON et TXT.
- Traitement fichier ou dossier avec conservation de l'arborescence.
- Index HTML automatique pour les dossiers.
- OCR Tesseract avec sélection de langues.
- Vérification explicite de Tesseract quand l'OCR est activé.
- Reflow des paragraphes dans les sorties lisibles.
- Extraction de pages, images intégrées, tableaux et visuels.
- Mode Markdown `clean` ou `audit`.
- Classification des images intégrées : image, logo/icône, doublon, micro-image, tuile.
- Masquage des tuiles PDF dans les sorties propres.
- Extracteurs spécialisés BKI, SGS, SOL-ESSAIS, Fahrradstellplätze et piézométrie.
- Données piézométriques structurées dans le JSON.
- Analyse de graphiques niveau 1 depuis tableaux/données structurées.
- Analyse graphique niveau 2 expérimentale depuis l'image, avec transcription dashboard en mode propre, crops dédiés pour chaque KPI/panneau/graphique, matrices/heatmaps OCR quand elles sont détectables, classification probable, métriques OCR structurées et lecture OCR spatiale en mode audit.
- Extra optionnel `vision` pour les améliorations non-LLM de graphiques.
- Nettoyage fuzzy de libellés OCR, correction contrôlée de matrices pour certains dashboards clairement reconnus et validation géométrique des bar charts.
- Les donuts/camemberts sont conservés comme crops visuels, sans transcription automatique de segments lorsque la lecture n'est pas fiable.
- Modèle `pages[].elements[]` pour ordonner texte, tableaux, visuels et images selon leurs coordonnées.
- Rendu Markdown/HTML basé sur les éléments ordonnés de la page.
- Masquage des headers/footers répétés dans les sorties propres.
- Extraction structurée prudente pour reçus : date, marchand, montant total, montants/dates détectés et confiance.
- Extraction structurée prudente pour cartes de visite : nom, société, fonction, email, téléphone, site web, adresse et confiance.
- Recadrage du document principal dans les images contenant un reçu, une carte ou une page photographiée.
- Garde-fous contre les faux positifs connus : tableaux BKI pris pour cartes de visite, factures prises pour dashboards/graphiques.
- Tests de non-régression initiaux.
