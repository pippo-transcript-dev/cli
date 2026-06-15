# Pippo Transcript

**Status: alpha.** Pippo Transcript is useful for local document transcription experiments, but outputs should be reviewed before automated, legal, financial or high-stakes use.

Pippo Transcript est une commande Python pour transcrire des PDF et des images en Markdown, HTML, JSON et TXT.

Elle rend aussi les pages en images, extrait les images intégrées aux PDF, repère les tableaux et graphiques, et peut traiter récursivement un dossier complet en conservant son arborescence.

## Fonctionnalités

- Entrée fichier ou dossier.
- Formats supportés : PDF, PNG, JPG, JPEG, TIFF, WEBP, BMP.
- Sorties par document : HTML, Markdown, JSON, TXT, images de pages, images intégrées.
- Index HTML automatique quand l'entrée est un dossier.
- JSON par page avec `elements[]` ordonnés : texte, tableau, visuel, image, bbox, source et confiance.
- Extraction de crops de tableaux dans `table_crops/`.
- Extraction de graphiques et visuels vectoriels dans `visuals/`.
- Reconstruction Markdown de certains tableaux détectés.
- Rendu Markdown/HTML dans l'ordre de la page : texte, tableaux, visuels et images sont replacés selon leurs coordonnées.
- Détection prudente des graphiques et conservation de leurs crops dans `visuals/`.
- Analyse graphique expérimentale disponible dans le JSON et le mode `audit`, mais masquée du Markdown propre quand elle n'est pas suffisamment fiable.
- Correction fuzzy de libellés OCR et validation géométrique de certains graphiques à barres quand l'option `vision` est installée.
- Pour les donuts/camemberts, le script conserve le crop du graphique pour analyse humaine, sans inventer de tableau de segments.
- Mode Markdown lisible ou audit avec `--markdown-mode clean|audit`.
- Classification des images intégrées : image utile, logo/icône, doublon, micro-image ou tuile PDF.
- Masquage automatique des tuiles techniques dans le Markdown propre.
- Masquage automatique des headers/footers répétés dans le Markdown propre.
- Le texte brut écrit dans le Markdown exclut les zones déjà interprétées en tableaux/visuels.
- Le Markdown reforme les paragraphes pour éviter les sauts de ligne PDF/OCR au milieu d'une phrase.
- Les retours à la ligne internes aux cellules de tableau sont repliés en espaces dans le Markdown.
- Conservation de l'arborescence quand l'entrée est un dossier.
- Texte natif PDF quand disponible.
- OCR avec Tesseract si le texte natif manque, ou OCR forcé avec `--ocr always`.
- Choix des langues OCR avec `--ocr-langs`.
- Correction automatique de l'orientation des images avant OCR.
- Recadrage automatique du document principal dans une photo quand une carte, un reçu ou une page est détectable.
- Extraction structurée prudente pour les reçus quand `--document-type receipt` est demandé.
- Extraction structurée prudente pour les cartes de visite quand `--document-type business-card` est demandé.
- Reconnaissance spécialisée de certains tableaux de type Kostenrahmen.
- Reconnaissance spécialisée des tableaux de suivi piézométrique avec données JSON structurées.
- Analyse automatique des courbes piézométriques à partir du tableau de mesures.
- Reconnaissance OCR spécialisée de certains tableaux scannés, par exemple les tableaux de Fahrradstellplätze.
- Reconnaissance OCR de pages BKI : tableaux de valeurs et visuels Kostenkennwerte avec crops de contrôle.

## Philosophie

Pippo Transcript privilégie une transcription fidèle et vérifiable :

- conserver l'image de page et les crops quand une zone n'est pas reconstruite parfaitement ;
- produire un Markdown propre pour la lecture ;
- garder les détails exploitables dans le JSON ;
- éviter d'inventer une structure lorsque la confiance est trop faible ;
- rendre les limites visibles plutôt que masquer les incertitudes.

## Installation

Depuis le dossier du projet :

```bash
cd pippo-transcript
pip install -e .
```

Pour activer les améliorations non-LLM sur les graphiques, installer l'extra `vision` :

```bash
pip install -e ".[vision]"
```

Le moteur OCR utilise Tesseract. Sur macOS, installer d'abord Tesseract :

```bash
brew install tesseract
```

Tesseract est obligatoire dès que `--ocr` vaut `auto` ou `always`. Si le binaire ou les langues demandées manquent, la commande s'arrête avec un message explicite.

Ensuite, deux options existent pour les langues OCR.

Option 1 : installer le pack complet Homebrew :

```bash
brew install tesseract-lang
```

Option 2 : installer seulement les langues souhaitées avec la commande du package :

```bash
pippo-transcript-langs install fra eng
pippo-transcript-langs install fra ita spa eng
```

Voir les langues courantes et leur statut :

```bash
pippo-transcript-langs list
```

Voir toutes les langues installées :

```bash
pippo-transcript-langs list --all
```

Vérifier directement côté Tesseract :

```bash
tesseract --list-langs
```

Vérifier que la commande est disponible :

```bash
pippo-transcript --help
```

## Statut Alpha

Le projet est publiable comme alpha, pas comme moteur de production garanti.

À vérifier manuellement selon les documents :

- tableaux sans bordures ou très denses ;
- graphiques complexes ;
- reçus ou cartes très bruités ;
- PDF avec ordre de lecture inhabituel ;
- documents où une erreur de chiffre aurait une conséquence métier.

Pour comprendre les détails de qualité : voir `QUALITY.md`.

## Comment Faire

### 1. Transcrire Un Fichier

Commande :

```bash
pippo-transcript "document.pdf"
```

Par défaut, les sorties sont créées dans `pippo-transcripted-files`.
Le type de document par défaut est `classic` : le script traite le fichier comme un document normal et ne tente pas de générer un résumé de reçu ou de carte de visite.

Choisir un autre dossier de destination :

```bash
pippo-transcript "document.pdf" -o sortie-test
pippo-transcript "document.pdf" --output sortie-test
```

Exemple :

```bash
pippo-transcript "../0340-Gesamtkosten/0342-KS-Kostenschätzung (LPH 2)/031090 LPH2-Kostenkontrolle LPH0-KR_LPH2-V2 190109.pdf" -o sortie-test
```

### 2. Transcrire Un Dossier Complet

Commande :

```bash
pippo-transcript ./documents
```

La commande parcourt le dossier récursivement et traite tous les fichiers supportés. L'arborescence d'entrée est conservée dans le dossier de sortie.
Un fichier `index.html` est aussi créé à la racine de la sortie.

Choisir un autre dossier de destination :

```bash
pippo-transcript ./documents --output ./sortie-personnalisee
```

Exemple :

```bash
pippo-transcript "../samples/0340-Gesamtkosten" -o "../sortie-pippo-test/0340-Gesamtkosten"
```

### 3. Choisir Le Type De Document

Par défaut :

```bash
pippo-transcript "document.pdf" --document-type classic
```

Utiliser l'extraction structurée pour un reçu :

```bash
pippo-transcript "recu.jpg" --document-type receipt
```

Utiliser l'extraction structurée pour une carte de visite :

```bash
pippo-transcript "carte-visite.jpg" --document-type business-card
```

Revenir à l'ancienne détection automatique :

```bash
pippo-transcript "image.jpg" --document-type auto
```

Les valeurs possibles sont :

- `classic` : document normal, PDF, rapport, notice, dossier, image scannée ;
- `receipt` : reçu, ticket, facturette simple ;
- `business-card` : carte de visite ;
- `auto` : tente de reconnaître automatiquement reçu/carte, à utiliser seulement si ce comportement est souhaité.

### 4. Forcer L'OCR

Par défaut, `--ocr auto` utilise le texte natif du PDF quand il existe, et lance l'OCR seulement quand le texte manque.

Forcer l'OCR partout :

```bash
pippo-transcript ./documents -o ./pippo-transcripted-files --ocr always
```

Désactiver l'OCR :

```bash
pippo-transcript ./documents -o ./pippo-transcripted-files --ocr never
```

### 5. Choisir Les Langues OCR

Par défaut, `--ocr-langs auto` choisit automatiquement les langues préférées disponibles, dans cet ordre :

```text
fra+deu+ita+spa+eng
```

Utiliser seulement certaines langues :

```bash
pippo-transcript ./documents -o ./pippo-transcripted-files --ocr-langs fra+eng
pippo-transcript ./documents -o ./pippo-transcripted-files --ocr-langs fra+ita+eng
pippo-transcript ./documents -o ./pippo-transcripted-files --ocr-langs spa+eng
```

On peut aussi écrire les langues avec des virgules :

```bash
pippo-transcript ./documents -o ./pippo-transcripted-files --ocr-langs fra,ita,eng
```

Les codes sont ceux de Tesseract, par exemple :

- `fra` : français
- `deu` : allemand
- `ita` : italien
- `spa` : espagnol
- `eng` : anglais

Si une langue demandée n'est pas installée, la commande s'arrête avec la liste des langues disponibles.

Installer une langue manquante :

```bash
pippo-transcript-langs install spa
```

### 6. Changer La Qualité Des Images

`--dpi` contrôle la résolution des pages, crops de tableaux et visuels.

```bash
pippo-transcript ./documents -o ./pippo-transcripted-files --dpi 200
```

`160` est plus léger. `200` ou `300` est plus net, mais les fichiers sont plus lourds.

### 7. Inclure Les Coordonnées Des Blocs Texte

```bash
pippo-transcript ./documents -o ./pippo-transcripted-files --include-blocks
```

Cette option ajoute les blocs texte et leurs coordonnées dans le Markdown. Elle est utile pour déboguer une page, mais elle rend le Markdown plus long.

### 8. Choisir Le Mode Markdown

Par défaut, le Markdown est en mode propre :

```bash
pippo-transcript ./documents -o ./pippo-transcripted-files --markdown-mode clean
```

Le mode `clean` masque les éléments techniques peu lisibles comme les titres `### Texte`, les tuiles internes de PDF, les micro-images, les doublons et les analyses graphiques expérimentales. Les graphiques restent visibles sous forme de crops à vérifier. Les données brutes restent dans le JSON.

Pour une sortie d'audit complète :

```bash
pippo-transcript ./documents -o ./pippo-transcripted-files --markdown-mode audit
```

Le mode `audit` affiche davantage d'éléments bruts dans le Markdown, utile pour comprendre pourquoi un PDF contient beaucoup d'objets internes.

### 9. Reprendre Un Gros Dossier

Pour éviter de retraiter les fichiers déjà transcrits :

```bash
pippo-transcript ./documents --output ./pippo-transcripted-files --skip-existing
```

Un document est ignoré seulement si ses sorties Markdown, HTML, JSON et TXT existent déjà.

## Structure de sortie

Pour un fichier `documents/sous-dossier/exemple.pdf`, la commande crée :

```text
pippo-transcripted-files/sous-dossier/exemple/
├── exemple_transcription.md
├── exemple_transcription.html
├── exemple_transcription.json
├── exemple_transcription.txt
├── pages/
│   ├── page_001.png
│   └── page_002.png
├── embedded_images/
├── table_crops/
└── visuals/
```

## Contenu Des Sorties

### Markdown

Le fichier `.md` contient, page par page :

- l'image de la page complète ;
- les images intégrées utiles ;
- les tableaux détectés, avec une table Markdown quand elle est reconstructible ;
- le crop image du tableau comme référence visuelle ;
- les graphiques/visuels détectés ;
- une analyse textuelle quand le graphique peut être relié à un tableau ;
- le texte hors zones déjà interprétées, reformé en paragraphes lisibles.

Le Markdown privilégie la lecture : les lignes PDF/OCR coupées au milieu d'une phrase sont recollées, et les cellules de tableau multi-lignes sont écrites sur une seule ligne. Le texte brut reste disponible dans le `.json` et le `.txt`.

Quand un PDF stocke une page sous forme de centaines de petites tuiles image, ces tuiles sont masquées dans le Markdown `clean`. Elles restent extraites et documentées dans le JSON.

### HTML

Chaque document reçoit aussi un fichier `.html` autonome :

- résumé du document ;
- navigation page par page ;
- image complète de chaque page ;
- tableaux en HTML natif ;
- crops de tableaux et graphiques ;
- analyses textuelles ;
- texte reconstitué.

Quand l'entrée est un dossier, `index.html` liste tous les documents traités avec les liens vers HTML, Markdown, JSON et TXT.

### JSON

Le fichier `.json` contient les mêmes informations sous forme exploitable :

- `pages[]` : données par page ;
- `embedded_images[]` : images intégrées extraites ;
- `embedded_images[].display_role` : rôle d'affichage (`image`, `logo-or-icon`, `duplicate`, `micro`, `tile`) ;
- `embedded_images[].display_reason` : raison du classement ;
- `table_crops[]` : tableaux détectés ;
- `table_crops[].markdown_table` : reconstruction Markdown du tableau quand disponible ;
- `table_crops[].data_rows` : lignes structurées quand un extracteur spécialisé existe ;
- `visual_crops[]` : graphiques/visuels détectés ;
- `visual_crops[].analysis` : interprétation textuelle quand disponible ;
- `visual_crops[].graph_features` : mesures expérimentales pour les graphiques lus directement depuis l'image ;
- `text_blocks[]` : blocs texte natifs avec coordonnées.
- `pages[].elements[]` : éléments ordonnés pour reconstruire la page sans perdre la position relative du texte, des tableaux et des visuels ;
- `pages[].elements[].type` : `text`, `table`, `visual` ou `image` ;
- `pages[].elements[].bbox` : position de l'élément sur la page ;
- `pages[].elements[].confidence` : confiance indicative de l'élément ;
- `document_type` : mode demandé, par exemple `classic`, `receipt`, `business-card` ou `auto` ;
- `structured` : données métier reconnues, par exemple `receipt`, `business_card`, `kostenrahmen` ou `piezometric`. En mode `classic`, les reçus et cartes de visite ne sont pas détectés automatiquement.

Pour les suivis piézométriques reconnus, `structured` contient aussi :

```json
{
  "type": "piezometric",
  "measurements": [
    {
      "date": "05/05/24",
      "numero_mesure": "0",
      "mesure_m": "15,98",
      "altitude_ngf": "2,92"
    }
  ],
  "summary": "Résumé chiffré de l'évolution des niveaux."
}
```

### TXT

Le fichier `.txt` contient le texte brut page par page.

## Ce Que Le Script Fait Bien

- Produire une transcription complète et traçable.
- Garder l'ordre des pages.
- Ne pas perdre les tableaux et graphiques : ils sont au minimum présents comme crops.
- Reconstruire certains tableaux réguliers en Markdown.
- Reconstituer certains tableaux scannés fréquents quand la page ne contient qu'une image.
- Relier certains graphiques aux tableaux proches pour produire une analyse simple.
- Produire des données structurées pour certains tableaux métier, par exemple les mesures piézométriques.
- Produire un rapport HTML consultable directement.
- Proposer une lecture graphique niveau 2 expérimentale dans le JSON et le mode audit : crops de chaque KPI/panneau/graphique, matrices/heatmaps OCR quand elles sont détectables, type probable, indices visuels, métriques OCR structurées et lignes OCR spatiales.

## Limites Connues

- Tous les tableaux PDF ne sont pas encore convertis parfaitement cellule par cellule.
- Les graphiques sont conservés comme images/crops à vérifier dans le Markdown propre. L'analyse automatique reste expérimentale et n'est pas affichée par défaut.
- L'analyse graphique niveau 2 est expérimentale : elle détecte zone graphique, couleurs/séries probables, axes/grilles, type probable, valeurs OCR contextualisées, lignes OCR regroupées par position, blocs dashboard probables et certaines matrices/heatmaps. En mode `clean`, ces détails sont masqués pour éviter de présenter une interprétation fragile comme une transcription fidèle. En mode `audit`, les détails techniques restent visibles. Les matrices issues de petits chiffres OCR restent à vérifier visuellement. Cela ne remplace pas encore une lecture métier complète ni une reconstruction géométrique exacte de chaque courbe/barre.
- Les champs de reçus et cartes de visite sont prudents : ils peuvent rester vides ou être marqués avec une confiance moyenne quand les indices sont ambigus. Ils ne sont produits que si `--document-type receipt`, `--document-type business-card` ou `--document-type auto` est utilisé.
- L'ordre multi-colonnes est heuristique et peut nécessiter un contrôle sur des mises en page éditoriales complexes.
- Pour obtenir une extraction parfaite sur un nouveau modèle de document, il faut parfois ajouter une règle spécialisée.

## Exemple De Contrôle

Après une exécution sur un dossier :

```bash
find pippo-transcripted-files -name '*_transcription.md' | wc -l
find pippo-transcripted-files -path '*/table_crops/*' -type f | wc -l
find pippo-transcripted-files -path '*/visuals/*' -type f | wc -l
```

## Tests

Installer les dépendances de développement :

```bash
pip install -e ".[dev]"
```

Lancer les tests :

```bash
pytest tests -q
```

## Développement

```bash
python -m pip install -e .
pippo-transcript --help
```

## Licence

MIT.
