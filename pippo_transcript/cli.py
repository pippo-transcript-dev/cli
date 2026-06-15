import argparse
import html
import json
import sys
from pathlib import Path

from .core import SUPPORTED_EXTENSIONS, safe_stem, transcribe_path


def is_inside(path, maybe_parent):
    path = path.resolve()
    maybe_parent = maybe_parent.resolve()
    try:
        path.relative_to(maybe_parent)
        return True
    except ValueError:
        return False


def iter_supported_files(input_dir, out_dir):
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if is_inside(path, out_dir):
            continue
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield path


def output_dir_for_file(input_path, input_root, out_root):
    if input_root.is_dir():
        relative = input_path.relative_to(input_root)
        return out_root / relative.parent / safe_stem(input_path)
    return out_root / safe_stem(input_path)


def output_dir_for_file_with_collisions(input_path, input_root, out_root, collisions):
    if not input_root.is_dir():
        return out_root / safe_stem(input_path)

    relative = input_path.relative_to(input_root)
    base = safe_stem(input_path)
    key = (relative.parent, base)
    if collisions.get(key, 0) > 1:
        suffix = input_path.suffix.lower().lstrip(".")
        base = f"{base}_{suffix}"
    return out_root / relative.parent / base


def expected_result_paths(input_path, target_dir):
    stem = safe_stem(input_path)
    return {
        "source": input_path,
        "out_dir": target_dir,
        "json": target_dir / f"{stem}_transcription.json",
        "markdown": target_dir / f"{stem}_transcription.md",
        "text": target_dir / f"{stem}_transcription.txt",
        "html": target_dir / f"{stem}_transcription.html",
    }


def output_is_complete(result):
    return all(
        Path(result[key]).exists()
        for key in ("json", "markdown", "text", "html")
    )


def stem_collisions(files, input_root):
    collisions = {}
    if not input_root.is_dir():
        return collisions

    for file_path in files:
        relative = file_path.relative_to(input_root)
        key = (relative.parent, safe_stem(file_path))
        collisions[key] = collisions.get(key, 0) + 1
    return collisions


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Transcrit des PDF/images en Markdown/JSON/TXT, rend les pages en images "
            "et conserve l'arborescence quand l'entrée est un dossier."
        )
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Fichier PDF/image ou dossier à traiter récursivement.",
    )
    parser.add_argument(
        "-o",
        "--out",
        "--output",
        type=Path,
        default=Path("pippo-transcripted-files"),
        help="Dossier de sortie. Défaut: pippo-transcripted-files.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Résolution de rendu des pages PDF.",
    )
    parser.add_argument(
        "--ocr",
        choices=["auto", "always", "never"],
        default="auto",
        help="auto = OCR seulement si le texte natif manque.",
    )
    parser.add_argument(
        "--ocr-langs",
        default="auto",
        help=(
            "Langues Tesseract à utiliser pour l'OCR, ex. fra+eng, fra+ita+eng, spa+eng. "
            "auto choisit les langues installées préférées."
        ),
    )
    parser.add_argument(
        "--document-type",
        choices=["classic", "receipt", "business-card", "auto"],
        default="classic",
        help=(
            "Type métier à extraire. classic = document normal sans résumé reçu/carte; "
            "receipt = reçu; business-card = carte de visite; auto = ancienne détection automatique."
        ),
    )
    parser.add_argument(
        "--include-blocks",
        action="store_true",
        help="Ajoute les blocs texte avec coordonnées dans le Markdown.",
    )
    parser.add_argument(
        "--markdown-mode",
        choices=["clean", "audit"],
        default="clean",
        help="clean = Markdown lisible; audit = affiche aussi éléments bruts/techniques.",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Ne vide pas le dossier de sortie de chaque document avant régénération.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Saute les documents dont les sorties Markdown, HTML, JSON et TXT existent déjà.",
    )
    return parser.parse_args()


def count_json_items(json_path):
    try:
        data = json.loads(Path(json_path).read_text())
    except Exception:
        return {}
    hidden_images = sum(
        1
        for image in data.get("embedded_images", [])
        if image.get("display_role") in {"tile", "micro", "duplicate"}
    )
    return {
        "pages": data.get("page_count", 0),
        "tables": len(data.get("table_crops", [])),
        "visuals": len(data.get("visual_crops", [])),
        "hidden_images": hidden_images,
        "structured": (data.get("structured") or {}).get("type", ""),
    }


def rel_link(path, base):
    return html.escape(str(Path(path).resolve().relative_to(base.resolve())))


def write_index(out_root, results):
    index_path = out_root / "index.html"
    rows = []
    for result in results:
        counts = count_json_items(result["json"]) if result.get("json") else {}
        source = html.escape(str(result["source"]))
        status = html.escape(result.get("status", "ok"))
        error = html.escape(result.get("error", ""))
        html_link = f"<a href=\"{rel_link(result['html'], out_root)}\">HTML</a>" if result.get("html") else ""
        md_link = f"<a href=\"{rel_link(result['markdown'], out_root)}\">MD</a>" if result.get("markdown") else ""
        json_link = f"<a href=\"{rel_link(result['json'], out_root)}\">JSON</a>" if result.get("json") else ""
        text_link = f"<a href=\"{rel_link(result['text'], out_root)}\">TXT</a>" if result.get("text") else ""
        rows.append(
            "<tr>"
            f"<td>{source}</td>"
            f"<td>{status}</td>"
            f"<td>{error}</td>"
            f"<td>{counts.get('pages', '')}</td>"
            f"<td>{counts.get('tables', '')}</td>"
            f"<td>{counts.get('visuals', '')}</td>"
            f"<td>{counts.get('hidden_images', '')}</td>"
            f"<td>{html.escape(counts.get('structured', ''))}</td>"
            f"<td>{html_link}</td>"
            f"<td>{md_link}</td>"
            f"<td>{json_link}</td>"
            f"<td>{text_link}</td>"
            "</tr>"
        )

    html_doc = f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pippo Transcript - Index</title>
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:24px;background:#f6f7f9;color:#1f2933}}
    main{{max-width:1200px;margin:0 auto;background:white;border:1px solid #d9dee8;border-radius:8px;padding:18px}}
    table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{border:1px solid #d8dee9;padding:7px 8px;text-align:left;vertical-align:top}}
    th{{background:#edf2f7}} a{{color:#155eef}}
  </style>
</head>
<body><main>
  <h1>Pippo Transcript - Index</h1>
  <p>{len(results)} document(s) traité(s).</p>
  <table>
    <thead><tr><th>Source</th><th>Statut</th><th>Erreur</th><th>Pages</th><th>Tableaux</th><th>Visuels</th><th>Images masquées</th><th>Structuré</th><th>HTML</th><th>MD</th><th>JSON</th><th>TXT</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</main></body></html>
"""
    index_path.write_text(html_doc, encoding="utf-8")
    return index_path


def main():
    args = parse_args()
    input_path = args.input
    out_root = args.out

    if not input_path.exists():
        raise FileNotFoundError(input_path)

    if input_path.is_file():
        if input_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            raise ValueError(f"Format non supporté : {input_path.suffix}. Formats : {supported}")
        files = [input_path]
    else:
        files = list(iter_supported_files(input_path, out_root))

    if not files:
        print("Aucun fichier PDF/image supporté trouvé.")
        return

    collisions = stem_collisions(files, input_path)
    print(f"{len(files)} fichier(s) à traiter.")
    results = []
    for index, file_path in enumerate(files, 1):
        target_dir = output_dir_for_file_with_collisions(
            file_path,
            input_path,
            out_root,
            collisions,
        )
        expected_result = expected_result_paths(file_path, target_dir)
        if args.skip_existing and output_is_complete(expected_result):
            print(f"[{index}/{len(files)}] {file_path} -> {target_dir} (déjà traité, ignoré)")
            results.append(expected_result)
            continue

        print(f"[{index}/{len(files)}] {file_path} -> {target_dir}")
        try:
            if file_path.stat().st_size == 0:
                raise ValueError("Fichier vide.")
            result = transcribe_path(
                file_path,
                target_dir,
                dpi=args.dpi,
                ocr_mode=args.ocr,
                ocr_langs=args.ocr_langs,
                document_type=args.document_type,
                include_blocks=args.include_blocks,
                clean=not args.no_clean,
                markdown_mode=args.markdown_mode,
            )
        except Exception as exc:
            print(f"Erreur: {exc}", file=sys.stderr)
            if input_path.is_file():
                raise SystemExit(2) from None
            results.append({
                "source": file_path,
                "out_dir": target_dir,
                "json": None,
                "markdown": None,
                "text": None,
                "html": None,
                "status": "error",
                "error": str(exc),
            })
            continue
        print(f"  Markdown: {result['markdown']}")
        print(f"  HTML: {result['html']}")
        print(f"  JSON: {result['json']}")
        result["status"] = "ok"
        results.append(result)

    if input_path.is_dir():
        index_path = write_index(out_root, results)
        print(f"  Index HTML: {index_path}")

    print("Terminé.")


if __name__ == "__main__":
    main()
