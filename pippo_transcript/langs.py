import argparse
import shutil
import sys
import urllib.request
from pathlib import Path

import pytesseract


TESSDATA_FAST_BASE_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main"

COMMON_LANGUAGES = {
    "eng": "anglais",
    "fra": "français",
    "deu": "allemand",
    "ita": "italien",
    "spa": "espagnol",
    "por": "portugais",
    "nld": "néerlandais",
    "pol": "polonais",
    "rus": "russe",
    "ara": "arabe",
    "chi_sim": "chinois simplifié",
    "chi_tra": "chinois traditionnel",
    "jpn": "japonais",
    "kor": "coréen",
}


def installed_languages():
    try:
        return set(pytesseract.get_languages(config=""))
    except Exception:
        return set()


def tesseract_data_dirs():
    candidates = [
        Path("/opt/homebrew/share/tessdata"),
        Path("/usr/local/share/tessdata"),
        Path("/usr/share/tesseract-ocr/5/tessdata"),
        Path("/usr/share/tesseract-ocr/4.00/tessdata"),
    ]

    tesseract = shutil.which("tesseract")
    if tesseract:
        binary = Path(tesseract).resolve()
        candidates.extend([
            binary.parents[1] / "share" / "tessdata",
            binary.parents[1] / "share" / "tesseract-ocr" / "4.00" / "tessdata",
            binary.parents[1] / "share" / "tesseract-ocr" / "5" / "tessdata",
        ])

    seen = set()
    existing = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists():
            existing.append(candidate)
    return existing


def default_tessdata_dir():
    dirs = tesseract_data_dirs()
    if dirs:
        return dirs[0]
    return Path.home() / ".local" / "share" / "tessdata"


def parse_langs(values):
    langs = []
    for value in values:
        for lang in value.replace(",", "+").split("+"):
            lang = lang.strip()
            if lang:
                langs.append(lang)
    return langs


def list_languages(args):
    installed = installed_languages()
    print("Langues courantes :")
    for code, label in COMMON_LANGUAGES.items():
        marker = "installée" if code in installed else "manquante"
        print(f"- {code:8} {label:24} {marker}")

    if args.all:
        print("\nToutes les langues installées :")
        for lang in sorted(installed):
            print(f"- {lang}")

    dirs = tesseract_data_dirs()
    if dirs:
        print("\nDossiers tessdata détectés :")
        for path in dirs:
            print(f"- {path}")


def download_language(lang, target_dir, force=False):
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{lang}.traineddata"
    if target.exists() and not force:
        print(f"{lang}: déjà installé ({target})")
        return

    url = f"{TESSDATA_FAST_BASE_URL}/{lang}.traineddata"
    print(f"{lang}: téléchargement depuis {url}")
    try:
        with urllib.request.urlopen(url) as response:
            data = response.read()
    except Exception as exc:
        raise RuntimeError(f"impossible de télécharger {lang}: {exc}") from exc

    if len(data) < 1024:
        raise RuntimeError(f"fichier téléchargé trop petit pour {lang}")

    target.write_bytes(data)
    print(f"{lang}: installé dans {target}")


def install_languages(args):
    langs = parse_langs(args.languages)
    if not langs:
        raise SystemExit("Indique au moins une langue, ex. fra ita spa")

    target_dir = Path(args.tessdata_dir).expanduser() if args.tessdata_dir else default_tessdata_dir()
    print(f"Dossier tessdata cible : {target_dir}")
    for lang in langs:
        download_language(lang, target_dir, force=args.force)

    print("\nVérification :")
    installed = installed_languages()
    for lang in langs:
        marker = "OK" if lang in installed else "non visible par Tesseract"
        print(f"- {lang}: {marker}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Liste ou installe des langues Tesseract pour pippo-transcript."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="Liste les langues OCR installées.")
    list_parser.add_argument("--all", action="store_true", help="Affiche toutes les langues installées.")
    list_parser.set_defaults(func=list_languages)

    install_parser = subparsers.add_parser("install", help="Télécharge des langues OCR Tesseract.")
    install_parser.add_argument("languages", nargs="+", help="Codes langue, ex. fra ita spa ou fra+ita+spa.")
    install_parser.add_argument(
        "--tessdata-dir",
        help="Dossier tessdata cible. Par défaut, utilise le dossier Tesseract détecté.",
    )
    install_parser.add_argument("--force", action="store_true", help="Retélécharge même si le fichier existe.")
    install_parser.set_defaults(func=install_languages)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"Erreur: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
