# Pippo Transcript

**Status: alpha.** Pippo Transcript is useful for local PDF/image transcription experiments, but every output should be reviewed before automated, legal, financial, or high-stakes use.

Pippo Transcript is a Python CLI and local web UI for turning PDFs and images into reviewable Markdown, HTML, JSON, TXT, page images, table crops, visual crops, and extracted PDF images.

[French documentation](README.fr.md)

## What It Does

- Accepts one file or a recursive folder.
- Supports PDF, PNG, JPG, JPEG, TIFF, WEBP, and BMP.
- Preserves the input folder structure in the output folder.
- Creates Markdown, HTML, JSON, TXT, rendered page images, table crops, visual crops, and extracted embedded images.
- Creates an `index.html` when the input is a folder.
- Uses native PDF text when available.
- Uses Tesseract OCR when native text is missing, or when OCR is forced.
- Supports OCR language selection with `--ocr-langs`.
- Automatically corrects image orientation before OCR.
- Reflows paragraphs so PDF/OCR line breaks inside sentences do not dominate the Markdown.
- Keeps table and visual crops when a region cannot be reconstructed reliably.
- Reconstructs selected tables into Markdown.
- Keeps charts/graphs as visual crops in clean Markdown instead of inventing fragile chart data.
- Stores experimental graph details in JSON and audit mode.
- Keeps document crops and OCR text for scanned or photographed receipts, cards, and pages.
- Provides careful structured extraction for receipts and business cards only when requested.
- Provides specialized extractors for selected BKI, Kostenrahmen, piezometric, SGS, SOL-ESSAIS, and other recurring technical table layouts.
- Provides ordered `pages[].elements[]` in JSON, with text/table/visual/image elements and bounding boxes.

## Design Philosophy

Pippo Transcript favors faithful, reviewable transcription over pretending to understand every document perfectly.

- Keep the full page image and crops when confidence is limited.
- Produce readable Markdown for humans.
- Keep detailed data in JSON.
- Avoid inventing structure when confidence is low.
- Make uncertainty visible.

## Installation

From the project folder:

```bash
cd pippo-transcript
pip install -e .
```

For optional non-LLM graph/image helpers:

```bash
pip install -e ".[vision]"
```

For development:

```bash
pip install -e ".[dev]"
```

## Tesseract OCR

Tesseract is required whenever `--ocr` is `auto` or `always`.

On macOS:

```bash
brew install tesseract
```

To install a broad set of Tesseract language files on macOS:

```bash
brew install tesseract-lang
```

You can also install specific Tesseract language files through the package helper:

```bash
pippo-transcript-langs install fra eng
pippo-transcript-langs install fra deu ita spa eng
```

List common OCR languages and their status:

```bash
pippo-transcript-langs list
```

List all installed OCR languages:

```bash
pippo-transcript-langs list --all
```

Check Tesseract directly:

```bash
tesseract --list-langs
```

## Quick Start

Transcribe one file:

```bash
pippo-transcript "document.pdf"
pippo-transcript "scan.jpg"
```

Transcribe a folder recursively:

```bash
pippo-transcript ./documents
```

Choose the output folder:

```bash
pippo-transcript ./documents --output ./pippo-transcripted-files
pippo-transcript ./documents -o ./pippo-transcripted-files
```

By default, outputs are written to `pippo-transcripted-files`.

## Local Web UI

Start the local browser UI:

```bash
pippo-transcript-gui
```

Or:

```bash
python -m pippo_transcript.gui
```

The UI lets you choose a file or folder, sets a sensible output folder automatically, lets you override it when needed, runs the transcription, and opens generated reports.

## CLI Reference

General form:

```bash
pippo-transcript INPUT [options]
```

Supported input:

- one PDF or image file;
- one folder, processed recursively.

### Output Folder

```bash
pippo-transcript INPUT -o OUTPUT_DIR
pippo-transcript INPUT --output OUTPUT_DIR
```

### OCR Mode

```bash
pippo-transcript INPUT --ocr auto
pippo-transcript INPUT --ocr always
pippo-transcript INPUT --ocr never
```

- `auto`: use native PDF text when available; OCR pages/images only when needed.
- `always`: force OCR.
- `never`: disable OCR.

### OCR Languages

```bash
pippo-transcript INPUT --ocr-langs auto
pippo-transcript INPUT --ocr-langs fra+eng
pippo-transcript INPUT --ocr-langs fra+deu+ita+spa+eng
pippo-transcript INPUT --ocr-langs spa,eng
```

Common Tesseract language codes:

- `fra`: French
- `deu`: German
- `ita`: Italian
- `spa`: Spanish
- `eng`: English

`auto` uses the preferred installed languages, in this order when available:

```text
fra+deu+ita+spa+eng
```

If a requested language is missing, the command stops and prints the installed languages.

### Document Type

```bash
pippo-transcript INPUT --document-type classic
pippo-transcript INPUT --document-type receipt
pippo-transcript INPUT --document-type business-card
pippo-transcript INPUT --document-type auto
```

- `classic`: normal document, report, notice, scanned page, or PDF. This is the default.
- `receipt`: enables cautious structured receipt extraction.
- `business-card`: enables cautious structured business-card extraction.
- `auto`: tries to classify receipt/business-card automatically.

Use `classic` unless you explicitly want receipt or business-card fields.

### Markdown Mode

```bash
pippo-transcript INPUT --markdown-mode clean
pippo-transcript INPUT --markdown-mode audit
pippo-transcript INPUT --markdown-mode bki-tables
```

- `clean`: readable Markdown. Technical headings, PDF tiles, duplicate micro-images, and fragile graph analysis are hidden.
- `audit`: exposes more raw/technical details for debugging.
- `bki-tables`: optimized output for BKI-style table documents. It enables specialized BKI table rendering and reduces redundant labels/crops.

The old name `bki` is still accepted as an alias:

```bash
pippo-transcript INPUT --markdown-mode bki
```

### DPI

```bash
pippo-transcript INPUT --dpi 200
pippo-transcript INPUT --dpi 300
```

Higher DPI gives sharper page images and crops, but larger files and slower processing.

### Debug Blocks

```bash
pippo-transcript INPUT --include-blocks
```

Adds native text block coordinates to Markdown. Useful for debugging layout issues, but noisy for normal reading.

### Existing Outputs

Skip already processed documents:

```bash
pippo-transcript INPUT --skip-existing
```

Do not clean an existing document output folder before regeneration:

```bash
pippo-transcript INPUT --no-clean
```

## Language Helper

```bash
pippo-transcript-langs list
pippo-transcript-langs list --all
pippo-transcript-langs install fra eng
pippo-transcript-langs install spa
```

## Output Structure

For `documents/subfolder/example.pdf`, Pippo Transcript creates:

```text
pippo-transcripted-files/subfolder/example/
├── example_transcription.md
├── example_transcription.html
├── example_transcription.json
├── example_transcription.txt
├── pages/
│   ├── page_001.png
│   └── page_002.png
├── embedded_images/
├── table_crops/
└── visuals/
```

When processing a folder, an `index.html` is also created at the output root.

## Output Files

### Markdown

The Markdown output contains, page by page:

- the full page image;
- useful embedded images;
- detected tables, with Markdown tables when reconstruction is reliable;
- table crop images for visual review;
- detected charts/visuals as crops;
- OCR/native text outside already interpreted table/visual regions;
- reflowed paragraphs for more readable text.

Clean Markdown does not display experimental chart details by default. Chart details remain available in JSON and audit mode.

### HTML

Each document also receives an HTML report with:

- a page-by-page view;
- the full rendered page image;
- HTML tables when available;
- table and visual crops;
- reconstructed text;
- reliable structured data when available.

### JSON

The JSON output is intended for automation and downstream tooling.

Important fields:

- `pages[]`: page-level data;
- `pages[].elements[]`: ordered text/table/visual/image elements;
- `pages[].elements[].bbox`: element bounding box;
- `pages[].elements[].confidence`: indicative confidence;
- `table_crops[]`: detected tables;
- `table_crops[].markdown_table`: reconstructed Markdown table when available;
- `table_crops[].data_rows`: structured rows for specialized extractors;
- `visual_crops[]`: detected charts and visuals;
- `embedded_images[]`: extracted embedded images;
- `embedded_images[].display_role`: `image`, `logo-or-icon`, `duplicate`, `micro`, or `tile`;
- `text_blocks[]`: native PDF text blocks with coordinates;
- `structured`: optional structured data for selected document types.

Example `structured` payload for recognized piezometric measurements:

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
  "summary": "Numeric summary of level evolution."
}
```

### TXT

The TXT output contains raw page text, page by page.

## What Works Well

- Local batch processing of files and folders.
- Reviewable Markdown/HTML/JSON/TXT outputs.
- Keeping visual evidence through full page images and crops.
- Reconstructing many regular tables.
- Preserving table/visual crops when full reconstruction is not safe.
- OCR text for scanned images and photographed documents.
- Conservative chart handling: keep the visual, avoid unreliable automatic chart readings in clean Markdown.
- Specialized handling for selected BKI and technical table layouts.

## Known Limits

- This is alpha software, not a guaranteed document understanding engine.
- OCR quality depends on image quality, DPI, orientation, and installed language packs.
- Borderless, rotated, dense, or highly graphical tables may require manual review.
- Complex charts, donuts, and pie charts are usually kept as crops for human review.
- Experimental graph reading is available in JSON/audit mode but is not a substitute for a reliable chart parser.
- Multi-column reading order is heuristic.
- New document families may require specialized extractors.

## Quality Notes

See [QUALITY.md](QUALITY.md).

Recommended public wording:

> Alpha toolkit for local PDF/image transcription with reviewable Markdown, JSON, HTML and visual crops.

Avoid promising perfect OCR, perfect table extraction, or guaranteed document understanding.

## Public Repo Checklist

Before publishing or tagging a release:

```bash
python -m py_compile pippo_transcript/core.py pippo_transcript/cli.py pippo_transcript/langs.py pippo_transcript/gui.py
pytest tests -q
pippo-transcript --help
pippo-transcript-langs --help
python -m pippo_transcript.gui --help
```

Also check:

- do not commit private PDFs, receipts, invoices, contracts, client files, or generated output folders;
- keep only synthetic or anonymized examples in `examples/`;
- keep `.gitignore` up to date for caches, virtual environments, and generated outputs;
- present the project as alpha;
- use public fixtures for tests whenever possible.

## Tests

```bash
pip install -e ".[dev]"
pytest tests -q
```

## Development

```bash
python -m pip install -e ".[dev]"
pippo-transcript --help
pippo-transcript-langs --help
python -m pippo_transcript.gui --help
```

## License

MIT.
