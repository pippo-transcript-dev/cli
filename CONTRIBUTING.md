# Contributing

Thanks for considering a contribution to Pippo Transcript.

## Project Status

Pippo Transcript is alpha software. The goal is faithful local transcription of PDFs and images into Markdown, HTML, JSON and TXT, with page images and visual crops kept for review.

Outputs should be reviewed before automated or high-stakes use.

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests -q
```

Tesseract is required for OCR-enabled manual tests:

```bash
brew install tesseract tesseract-lang
```

On Linux, install the equivalent `tesseract-ocr` packages.

## Test Expectations

Before opening a pull request:

```bash
python -m py_compile pippo_transcript/core.py pippo_transcript/cli.py
pytest tests -q
pippo-transcript --help
```

Add tests for:

- reading order changes;
- table reconstruction changes;
- receipt or business-card extraction changes;
- graph/visual detection changes;
- any bug that caused a false positive.

## Data And Privacy

Do not commit private PDFs, receipts, IDs, contracts, invoices or generated output folders.

Use small synthetic fixtures whenever possible. If a real document is needed to explain a bug, remove personal data before sharing it.

## Design Principles

- Prefer conservative extraction over hallucinated structure.
- Keep the original page image or crop when data cannot be read reliably.
- Put technical details in JSON or audit mode, not in clean Markdown.
- Preserve page order and visual order before adding specialized interpretation.
- Avoid heavy dependencies unless they are optional.

