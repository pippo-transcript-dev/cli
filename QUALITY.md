# Quality Notes

Pippo Transcript aims to produce useful, reviewable transcriptions rather than perfect document understanding.

## What Is Considered Stable

- CLI processing of one file or a recursive folder.
- Output folders with Markdown, HTML, JSON, TXT and page images.
- Tesseract OCR setup and language validation.
- Clean/audit Markdown modes.
- Ordered page `elements[]` in JSON.
- Basic receipt extraction.
- Basic business-card extraction.
- Conservative image and crop retention.

## What Is Conservative By Design

- Donuts and pie charts are kept as visual crops unless reliable segment data is available.
- Unknown chart types are kept for human review.
- Receipt and business-card fields include confidence and may be empty.
- Tables are always kept as crops even when Markdown reconstruction is incomplete.

## Known Limits

- Borderless, rotated or highly graphical tables may need manual review.
- OCR quality depends on image resolution, language packs and document quality.
- Multi-column reading order is heuristic.
- Graph interpretation is experimental and can be disabled indirectly by reviewing clean outputs/crops.
- Specialized document types may need dedicated extractors.

## Recommended Public Wording

Use:

> Alpha toolkit for local PDF/image transcription with reviewable Markdown, JSON, HTML and visual crops.

Avoid:

> Perfect OCR, perfect table extraction or guaranteed document understanding.

