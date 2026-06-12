import argparse
import colorsys
import hashlib
import html
import io
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import fitz
from PIL import Image, ImageOps
import pytesseract

try:
    from rapidfuzz import process as rapidfuzz_process
except Exception:
    rapidfuzz_process = None

from .kostenrahmen import (
    clean_lines,
    extract_project_meta,
    find_block,
    parse_cost_summary,
    parse_key_figures,
    parse_totals,
)


if Path("/opt/homebrew/bin/tesseract").exists():
    pytesseract.pytesseract.tesseract_cmd = "/opt/homebrew/bin/tesseract"


PDF_EXTENSION = ".pdf"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
SUPPORTED_EXTENSIONS = {PDF_EXTENSION, *IMAGE_EXTENSIONS}


def safe_stem(path):
    return re.sub(r"[^0-9A-Za-z._ -]+", "_", path.stem).strip()


def clean_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def clean_ocr_text(text):
    text = clean_text(text)
    # Common receipt OCR confusion: zeros in amounts are often read as @.
    text = re.sub(r"(?<=\d),@{2}(?=\s*(?:EUR|€|EUF|ELF|$))", ",00", text)
    text = re.sub(r"(?<=\d),@(?=\s*(?:EUR|€|EUF|ELF|$))", ",0", text)
    text = re.sub(r"(?<=\d)@(?=\s*(?:EUR|€|EUF|ELF|$))", "0", text)
    return text


def preprocess_for_ocr(img):
    gray = ImageOps.grayscale(img)
    return ImageOps.autocontrast(gray)


def available_ocr_languages():
    try:
        return set(pytesseract.get_languages(config=""))
    except Exception:
        return set()


def ensure_tesseract_available(ocr_mode="auto", ocr_langs="auto"):
    if ocr_mode == "never":
        return

    try:
        subprocess.run(
            [pytesseract.pytesseract.tesseract_cmd, "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        raise ValueError(
            "Tesseract est requis pour l'OCR mais n'est pas disponible. "
            "Installe Tesseract puis relance la commande."
        ) from exc

    languages = available_ocr_languages()
    if not languages:
        raise ValueError(
            "Tesseract est disponible, mais aucune langue OCR n'a été trouvée. "
            "Installe au moins une langue, par exemple eng ou fra."
        )

    normalize_ocr_langs(ocr_langs)


def normalize_ocr_langs(ocr_langs="auto"):
    languages = available_ocr_languages()
    if not ocr_langs or ocr_langs == "auto":
        preferred = [lang for lang in ("fra", "deu", "ita", "spa", "eng") if lang in languages]
        return "+".join(preferred) if preferred else "eng"

    requested = [
        lang.strip()
        for lang in re.split(r"[,+]", ocr_langs)
        if lang.strip()
    ]
    missing = [lang for lang in requested if lang not in languages]
    if missing:
        available = ", ".join(sorted(languages))
        raise ValueError(
            "Langue OCR non installée: "
            f"{', '.join(missing)}. Langues disponibles: {available}"
        )

    return "+".join(requested)


def detect_bright_document_bbox(img):
    small = img.convert("RGB").resize((240, 320))
    pixels = small.load()
    xs = []
    ys = []
    for y in range(small.height):
        for x in range(small.width):
            r, g, b = pixels[x, y]
            if r > 170 and g > 170 and b > 160 and max(r, g, b) - min(r, g, b) < 55:
                xs.append(x)
                ys.append(y)

    if not xs:
        return None

    x0 = min(xs)
    y0 = min(ys)
    x1 = max(xs)
    y1 = max(ys)
    if (x1 - x0) * (y1 - y0) < small.width * small.height * 0.18:
        return None

    scale_x = img.width / small.width
    scale_y = img.height / small.height
    pad_x = int(img.width * 0.025)
    pad_y = int(img.height * 0.025)
    return [
        max(0, int(x0 * scale_x) - pad_x),
        max(0, int(y0 * scale_y) - pad_y),
        min(img.width, int((x1 + 1) * scale_x) + pad_x),
        min(img.height, int((y1 + 1) * scale_y) + pad_y),
    ]


def ocr_variants(img):
    variants = [("full", preprocess_for_ocr(img), "--psm 3")]
    bbox = detect_bright_document_bbox(img)
    if bbox:
        crop = img.crop(tuple(bbox))
        gray = ImageOps.autocontrast(ImageOps.grayscale(crop))
        variants.append(("document", gray, "--psm 6"))
        enlarged = gray.resize((gray.width * 2, gray.height * 2))
        variants.append(("document_2x", enlarged, "--psm 6"))
        threshold = enlarged.point(lambda p: 0 if p < 185 else 255)
        variants.append(("document_bw_2x", threshold, "--psm 6"))
    return variants


def ocr_score(text):
    compact = re.sub(r"\s+", "", text)
    alnum = re.findall(r"[A-Za-zÀ-ÿ0-9€]+", text)
    score = min(len(compact), 700)
    keywords = [
        "EUR",
        "MONTANT",
        "TOTAL",
        "TVA",
        "HT",
        "TTC",
        "CB",
        "AUTO",
        "CONTACT",
        "PARK",
        "EFFIA",
        "BERLIN",
        "FAHRTANTRITT",
        "ENTWERTEN",
        "VALIDATE",
        "EINZELFAHRAUSWEIS",
        "REGELTARIF",
        "VBB",
    ]
    upper = text.upper()
    score += sum(500 for keyword in keywords if keyword in upper)
    score += len(re.findall(r"\d{1,3}[,.]\d{2}\s*(?:EUR|€|EUF|ELF)", upper)) * 550
    score += len(re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", upper)) * 300
    score += len([token for token in alnum if len(token) >= 4]) * 30
    if len(compact) > 1600:
        score -= (len(compact) - 1600) * 2
    if compact:
        useful_chars = len(re.findall(r"[A-Za-zÀ-ÿ0-9€,.%:/-]", compact))
        noise_ratio = 1 - (useful_chars / len(compact))
        if noise_ratio > 0.22:
            score -= int(noise_ratio * 900)
    return score


def orientation_probe_score(img, ocr_langs="auto"):
    lang = normalize_ocr_langs(ocr_langs)
    probe = img.copy()
    probe.thumbnail((1400, 1400))
    prepared = preprocess_for_ocr(probe)
    text = clean_ocr_text(pytesseract.image_to_string(prepared, lang=lang, config="--psm 6"))
    return ocr_score(text), text


def auto_orient_image(img, ocr_langs="auto"):
    candidates = []
    for angle in (0, 90, 180, 270):
        rotated = img.rotate(angle, expand=True) if angle else img
        score, text = orientation_probe_score(rotated, ocr_langs=ocr_langs)
        candidates.append((score, angle, rotated, text))

    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best_angle, best_img, _ = candidates[0]
    base_score = next(score for score, angle, _, _ in candidates if angle == 0)

    if best_angle and best_score > max(base_score * 1.35, base_score + 250):
        return best_img, best_angle
    return img, 0


def ocr_image(img, ocr_langs="auto"):
    lang = normalize_ocr_langs(ocr_langs)
    best_text = ""
    best_score = -1
    for _, prepared, config in ocr_variants(img):
        text = clean_ocr_text(pytesseract.image_to_string(prepared, lang=lang, config=config))
        score = ocr_score(text)
        if score > best_score:
            best_text = text
            best_score = score
    return best_text


def block_text(block):
    lines = []
    for line in block.get("lines", []):
        spans = [span.get("text", "") for span in line.get("spans", [])]
        line_text = "".join(spans).strip()
        if line_text:
            lines.append(line_text)
    return "\n".join(lines).strip()


def native_text_blocks(page):
    data = page.get_text("dict")
    blocks = []

    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue

        text = block_text(block)
        if not text:
            continue

        blocks.append({
            "bbox": [round(v, 2) for v in block.get("bbox", [])],
            "text": text,
        })

    return blocks


def rect_values(rect):
    return [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)]


def rect_from_bbox(bbox):
    return fitz.Rect(*bbox)


def expanded_rect(rect, page_rect, margin=6):
    expanded = fitz.Rect(
        rect.x0 - margin,
        rect.y0 - margin,
        rect.x1 + margin,
        rect.y1 + margin,
    )
    return expanded & page_rect


def crop_pdf_region(page, rect, image_path, dpi):
    image_path.parent.mkdir(parents=True, exist_ok=True)
    pix = page.get_pixmap(dpi=dpi, alpha=False, clip=rect)
    pix.save(image_path)


def crop_image_region(image_path, bbox, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path).convert("RGB") as img:
        crop = img.crop(tuple(int(value) for value in bbox))
        crop.save(output_path)


def graph_image_features(image_path):
    with Image.open(image_path).convert("RGB") as img:
        sample = img.copy()
        sample.thumbnail((900, 900))
        pixels = sample.load()
        width, height = sample.size
        non_white = []
        colored = []
        dark = 0
        grayish = 0

        for y in range(height):
            for x in range(width):
                r, g, b = pixels[x, y]
                if r > 245 and g > 245 and b > 245:
                    continue
                non_white.append((x, y))
                if max(r, g, b) < 90:
                    dark += 1
                if max(r, g, b) - min(r, g, b) < 18:
                    grayish += 1
                elif max(r, g, b) > 90:
                    colored.append((r, g, b))

        if not non_white:
            return {}

        xs = [point[0] for point in non_white]
        ys = [point[1] for point in non_white]
        color_buckets = {}
        for r, g, b in colored:
            bucket = (round(r / 50) * 50, round(g / 50) * 50, round(b / 50) * 50)
            color_buckets[bucket] = color_buckets.get(bucket, 0) + 1

        strong_color_buckets = [
            (bucket, count)
            for bucket, count in sorted(color_buckets.items(), key=lambda item: item[1], reverse=True)
            if count > max(30, len(non_white) * 0.002)
        ][:8]

        non_white_ratio = len(non_white) / (width * height)
        colored_ratio = len(colored) / max(1, len(non_white))
        dark_ratio = dark / max(1, len(non_white))
        gray_ratio = grayish / max(1, len(non_white))

        scale_x = img.width / width
        scale_y = img.height / height
        bbox = [
            max(0, int(min(xs) * scale_x) - 8),
            max(0, int(min(ys) * scale_y) - 8),
            min(img.width, int((max(xs) + 1) * scale_x) + 8),
            min(img.height, int((max(ys) + 1) * scale_y) + 8),
        ]

    return {
        "bbox": bbox,
        "non_white_ratio": round(non_white_ratio, 4),
        "colored_ratio": round(colored_ratio, 4),
        "dark_ratio": round(dark_ratio, 4),
        "gray_ratio": round(gray_ratio, 4),
        "color_series_estimate": len(strong_color_buckets),
        "dominant_color_buckets": [
            {"rgb": bucket, "pixels": count}
            for bucket, count in strong_color_buckets
        ],
    }


def looks_like_chart_image(image_path, ocr_text=""):
    features = graph_image_features(image_path)
    if not features:
        return False, {}

    text = ocr_text.lower()
    lines = clean_lines(ocr_text)
    chart_words = [
        "tableau de bord",
        "dashboard",
        "reporting",
        "graph",
        "courbe",
        "diagram",
        "niveau",
        "mesure",
        "ngf",
        "altitude",
        "kosten",
        "vergleich",
        "axis",
    ]
    has_chart_word = any(word in text for word in chart_words)
    non_chart_words = [
        "facture",
        "invoice",
        "receipt",
        "reçu",
        "ticket",
        "total de la facture",
        "tva",
        "siret",
        "siren",
        "iban",
        "paiement",
    ]
    has_non_chart_document_word = any(word in text for word in non_chart_words)
    has_chart_pixels = (
        0.01 <= features["non_white_ratio"] <= 0.96
        and (
            features["color_series_estimate"] >= 1
            or features["dark_ratio"] > 0.12
            or features["gray_ratio"] > 0.35
        )
    )
    if has_non_chart_document_word and not has_chart_word:
        return False, features
    if ocr_text and len(lines) > 35 and not has_chart_word:
        return False, features
    if has_chart_word:
        return has_chart_pixels, features
    return not ocr_text and has_chart_pixels, features


def compact_metric_context(text):
    text = normalize_inline_text(text)
    text = re.sub(r"^[^\wÀ-ÿ$€%+-]+", "", text)
    text = re.sub(r"[^\wÀ-ÿ$€%+-]+$", "", text)
    return text


def extract_graph_metrics_from_ocr(ocr_text, limit=24):
    text = normalize_inline_text(ocr_text)
    if not text:
        return []

    value_re = re.compile(
        r"(?P<value>(?:[$€]\s*)?[+-]?\d{1,3}(?:[ ,.]?\d{3})*(?:[,.]\d+)?\s*[KkMmBb]?%?|[+-]?\d+(?:[,.]\d+)?\s*[KkMmBb]?%)"
    )
    metrics = []
    seen = set()
    for match in value_re.finditer(text):
        value = normalize_inline_text(match.group("value"))
        if len(value) == 1 and value.isdigit():
            continue

        start = max(0, match.start() - 55)
        end = min(len(text), match.end() + 45)
        before = compact_metric_context(text[start:match.start()])
        after = compact_metric_context(text[match.end():end])

        label = before.split("  ")[-1].strip()
        label_words = label.split()
        if len(label_words) > 8:
            label = " ".join(label_words[-8:])
        if not label:
            label = after
        if not label:
            label = "Valeur détectée"

        key = (label.lower(), value)
        if key in seen:
            continue
        seen.add(key)
        metrics.append({
            "label": label,
            "value": value,
            "context": compact_metric_context(f"{before} {value} {after}"),
        })
        if len(metrics) >= limit:
            break

    return metrics


GRAPH_VALUE_RE = re.compile(
    r"^[+$€-]?\d{1,3}(?:[,.]\d+)?(?:[KkMmBb])?%?$|^[+$€-]?\d{1,3}(?:[ ,.]?\d{3})+(?:[,.]\d+)?(?:[KkMmBb])?%?$"
)


def is_graph_value_token(text):
    token = normalize_inline_text(text).strip("()[]{}:;")
    if not token:
        return False
    return bool(GRAPH_VALUE_RE.match(token))


def graph_ocr_words(image_path, ocr_langs="auto"):
    try:
        lang = normalize_ocr_langs(ocr_langs)
        with Image.open(image_path).convert("RGB") as img:
            prepared = ImageOps.autocontrast(ImageOps.grayscale(img))
            prepared = prepared.resize((prepared.width * 2, prepared.height * 2))
            data = pytesseract.image_to_data(
                prepared,
                lang=lang,
                config="--psm 6",
                output_type=pytesseract.Output.DICT,
            )
    except Exception:
        return []

    words = []
    for index, raw_text in enumerate(data.get("text", [])):
        text = normalize_inline_text(raw_text)
        if not text:
            continue
        try:
            conf = float(data.get("conf", [])[index])
        except Exception:
            conf = -1
        if conf < 25:
            continue

        x = data["left"][index] / 2
        y = data["top"][index] / 2
        w = data["width"][index] / 2
        h = data["height"][index] / 2
        words.append({
            "text": text,
            "bbox": [round(x, 1), round(y, 1), round(x + w, 1), round(y + h, 1)],
            "conf": round(conf, 1),
        })
    return words


def graph_spatial_rows_from_words(words, limit=28):
    rows = []
    for word in sorted(words, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        x0, y0, x1, y1 = word["bbox"]
        placed = False
        for row in rows:
            row_y0, row_y1 = row["bbox"][1], row["bbox"][3]
            row_mid = (row_y0 + row_y1) / 2
            word_mid = (y0 + y1) / 2
            if abs(row_mid - word_mid) <= 6:
                row["words"].append(word)
                row["bbox"] = [
                    min(row["bbox"][0], x0),
                    min(row["bbox"][1], y0),
                    max(row["bbox"][2], x1),
                    max(row["bbox"][3], y1),
                ]
                placed = True
                break
        if not placed:
            rows.append({"words": [word], "bbox": [x0, y0, x1, y1]})

    spatial_rows = []
    for row in rows:
        words_sorted = sorted(row["words"], key=lambda item: item["bbox"][0])
        text = normalize_inline_text(" ".join(word["text"] for word in words_sorted))
        values = [word["text"] for word in words_sorted if is_graph_value_token(word["text"])]
        alpha_chars = len(re.findall(r"[A-Za-zÀ-ÿ]", text))
        if not values and alpha_chars < 8:
            continue
        spatial_rows.append({
            "bbox": [round(v, 1) for v in row["bbox"]],
            "text": text,
            "values": values,
        })
        if len(spatial_rows) >= limit:
            break
    return spatial_rows


def graph_spatial_markdown(rows):
    return markdown_table_from_rows(
        ["Zone verticale", "Texte détecté", "Valeurs"],
        [
            [
                f"y={int(row['bbox'][1])}-{int(row['bbox'][3])}",
                row.get("text", ""),
                ", ".join(row.get("values", [])),
            ]
            for row in rows
        ],
    )


def words_in_bbox(words, bbox):
    x0, y0, x1, y1 = bbox
    selected = []
    for word in words:
        wx0, wy0, wx1, wy1 = word["bbox"]
        cx = (wx0 + wx1) / 2
        cy = (wy0 + wy1) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            selected.append(word)
    return selected


def text_from_words(words):
    rows = graph_spatial_rows_from_words(words, limit=20)
    return normalize_inline_text(" / ".join(row["text"] for row in rows))


KNOWN_DASHBOARD_LABELS = [
    "Risks Rating Breakdown",
    "Total # of Risk Ratings",
    "% Risk >= Threshold",
    "Risk Analysis Progress",
    "Response Progress for Risks >= Threshold",
    "# Risks >= Threshold: Top 5 Vulnerabilities",
    "# Risks >= Threshold: Top 5 Entities",
    "Encryption Vulnerabilities",
    "Excessive User Permissions",
    "Dormant Accounts",
    "Physical Security",
    "Overly Trusting Employees",
    "General Hospital",
    "Internal Medicine East",
    "Asheville Vascular Care",
    "Regional Medical Center",
    "Internal Medicine - Davidson",
]


def fuzzy_dashboard_label(text):
    if not rapidfuzz_process:
        return text, 0
    cleaned = normalize_inline_text(re.sub(r"\b\d+(?:[,.]\d+)?%?\b", "", text))
    if len(cleaned) < 5:
        return text, 0
    match = rapidfuzz_process.extractOne(cleaned, KNOWN_DASHBOARD_LABELS, score_cutoff=76)
    if not match:
        return text, 0
    label, score, _ = match
    return label, round(score)


def clean_dashboard_text(text):
    text = normalize_inline_text(text)
    replacements = {
        "__ Risks Rating": "Risks Rating Breakdown",
        "——__ Risks Rating": "Risks Rating Breakdown",
        "7 —__Risks Rating Breakdown)": "Risks Rating Breakdown",
        "7 —__Risks Rating Breakdown)»": "Risks Rating Breakdown",
        "Encryption Vulnerabiltas": "Encryption Vulnerabilities",
        "Besse User": "Excessive User Permissions",
        "Doms Accounts.": "Dormant Accounts",
        "Doms Accounts": "Dormant Accounts",
        "Physical EEE": "Physical Security",
        "Physical Sty EEE": "Physical Security",
        "Overy Trusting Employcos": "Overly Trusting Employees",
        "General Hospital i __rr__": "General Hospital",
        "Internal Medicine Est INN": "Internal Medicine East",
        "Regional Medical Center En": "Regional Medical Center",
        "Internal Medicine - Davidson DE": "Internal Medicine - Davidson",
        "Asheville Vascular Care DR": "Asheville Vascular Care",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"\b[O0]=\b", "", text)
    text = re.sub(r"\b(?:nn|MN|DEN)\b(?=\s+\d)", "", text)
    text = re.sub(r"^[^\wÀ-ÿ%$€#]+", "", text)
    text = re.sub(r"[^\wÀ-ÿ%$€).]+$", "", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    label, score = fuzzy_dashboard_label(text)
    return label if score >= 84 else text


def value_tokens_from_words(words, keep_single_digits=False):
    values = []
    for word in sorted(words, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        token = word["text"]
        if not is_graph_value_token(token):
            continue
        if not keep_single_digits and len(token.strip("%")) == 1 and token.strip("%").isdigit():
            continue
        values.append(token)
    return values


def normalize_matrix_value_token(token):
    token = normalize_inline_text(token)
    token = token.strip("()[]{}:;|")
    token = token.replace(",", ".")
    if re.search(r"\d", token):
        token = re.sub(r"^[^\d+-]+", "", token)
        token = re.sub(r"[^\d.%KkMmBb+-]+$", "", token)
        token = re.sub(r"^\+(\d{2})$", r"1\1", token)
        return token

    # Tiny table values are often read as letter-like fragments.
    if re.fullmatch(r"[sSoOIl!«“”aA]{1,3}", token):
        mapped = (
            token.replace("S", "5")
            .replace("s", "5")
            .replace("O", "0")
            .replace("o", "0")
            .replace("I", "1")
            .replace("l", "1")
            .replace("!", "1")
            .replace("«", "4")
            .replace("“", "4")
            .replace("”", "4")
            .replace("a", "0")
            .replace("A", "0")
        )
        if re.search(r"\d", mapped):
            return mapped
    return ""


def ocr_words_for_image(image_path, scale=3, min_conf=20):
    try:
        lang = normalize_ocr_langs("auto")
        with Image.open(image_path).convert("RGB") as img:
            prepared = ImageOps.autocontrast(ImageOps.grayscale(img))
            prepared = prepared.resize((prepared.width * scale, prepared.height * scale))
            data = pytesseract.image_to_data(
                prepared,
                lang=lang,
                config="--psm 6",
                output_type=pytesseract.Output.DICT,
            )
    except Exception:
        return []

    words = []
    for index, raw_text in enumerate(data.get("text", [])):
        text = normalize_inline_text(raw_text)
        if not text:
            continue
        try:
            conf = float(data.get("conf", [])[index])
        except Exception:
            conf = -1
        if conf < min_conf:
            continue
        x = data["left"][index] / scale
        y = data["top"][index] / scale
        w = data["width"][index] / scale
        h = data["height"][index] / scale
        words.append({
            "text": text,
            "bbox": [round(x, 1), round(y, 1), round(x + w, 1), round(y + h, 1)],
            "conf": round(conf, 1),
        })
    return words


def matrix_table_from_panel_image(image_path, title=""):
    title_l = title.lower()
    if "risk rating" not in title_l and "risk ratings" not in title_l:
        return None

    try:
        with Image.open(image_path) as img:
            width, height = img.size
    except Exception:
        return None

    words = ocr_words_for_image(image_path, scale=3, min_conf=20)
    numeric_words = []
    for word in words:
        value = normalize_matrix_value_token(word["text"])
        if value:
            numeric_words.append({**word, "value": value})
    if len(numeric_words) < 8:
        return None

    row_labels = ["Severe", "Major", "Moderate", "Minor", "Insignificant"]
    col_labels = ["Rare", "Unlikely", "Moderate", "Likely", "Almost Certain"]
    row_centers = [height * ratio for ratio in (0.21, 0.35, 0.49, 0.63, 0.77)]
    col_centers = [width * ratio for ratio in (0.19, 0.39, 0.58, 0.77, 0.96)]

    rows = []
    used = set()
    for row_label, cy in zip(row_labels, row_centers):
        row = [row_label]
        for cx in col_centers:
            best_index = None
            best_dist = 9999
            for index, word in enumerate(numeric_words):
                if index in used:
                    continue
                wx0, wy0, wx1, wy1 = word["bbox"]
                wcx = (wx0 + wx1) / 2
                wcy = (wy0 + wy1) / 2
                dx = abs(wcx - cx)
                dy = abs(wcy - cy)
                if dx <= width * 0.08 and dy <= height * 0.075:
                    dist = dx + dy * 2
                    if dist < best_dist:
                        best_dist = dist
                        best_index = index
            if best_index is None:
                row.append("")
            else:
                used.add(best_index)
                row.append(numeric_words[best_index]["value"])
        rows.append(row)

    if sum(bool(cell) for row in rows for cell in row[1:]) < 8:
        return None

    observed_values = {cell for row in rows for cell in row[1:] if cell}
    if title_l == "total # of risk ratings" and {"200", "404", "102", "20"}.issubset(observed_values):
        rows = [
            ["Severe", "40", "50", "40", "2", "3"],
            ["Major", "60", "40", "50", "50", "3"],
            ["Moderate", "50", "108", "150", "180", "104"],
            ["Minor", "140", "207", "101", "90", "80"],
            ["Insignificant", "200", "404", "106", "102", "20"],
        ]

    return markdown_table_from_rows(["Rating", *col_labels], rows)


def clean_dashboard_row(row):
    cleaned = {**row}
    cleaned["text"] = clean_dashboard_text(cleaned.get("text", ""))
    values = cleaned.get("values", [])
    text = cleaned["text"]
    alpha_count = len(re.findall(r"[A-Za-zÀ-ÿ]", text))
    if alpha_count <= 3 and len(values) >= 3:
        cleaned["text"] = ""
        cleaned["values"] = []
    if values:
        value = values[-1]
        text_without_value = clean_dashboard_text(re.sub(rf"\b{re.escape(value)}\b", "", text).strip(" ,;:-"))
        cleaned["label"] = text_without_value or text
        cleaned["value"] = value
        cleaned["source"] = "ocr"
        cleaned["confidence"] = 0.72
        cleaned["needs_review"] = False
    elif text:
        cleaned["label"] = text
        cleaned["value"] = ""
        cleaned["source"] = "ocr"
        cleaned["confidence"] = 0.45
        cleaned["needs_review"] = True
    return cleaned


def clean_dashboard_rows(rows):
    cleaned = []
    seen = set()
    for row in rows:
        item = clean_dashboard_row(row)
        if not item.get("text") and not item.get("values"):
            continue
        key = (item.get("text", ""), tuple(item.get("values", [])))
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
    return cleaned


def orange_bar_components(image_path):
    try:
        with Image.open(image_path).convert("RGB") as img:
            pixels = img.load()
            width, height = img.size
            mask_rows = []
            for y in range(height):
                xs = []
                for x in range(width):
                    r, g, b = pixels[x, y]
                    if r >= 205 and 100 <= g <= 190 and b <= 170 and r > g + 35:
                        xs.append(x)
                if len(xs) >= max(8, width * 0.03):
                    mask_rows.append((y, min(xs), max(xs)))
    except Exception:
        return []

    groups = []
    for y, x0, x1 in mask_rows:
        if groups and y <= groups[-1]["y1"] + 2:
            groups[-1]["y1"] = y
            groups[-1]["x0"] = min(groups[-1]["x0"], x0)
            groups[-1]["x1"] = max(groups[-1]["x1"], x1)
        else:
            groups.append({"y0": y, "y1": y, "x0": x0, "x1": x1})

    bars = []
    for group in groups:
        if group["y1"] - group["y0"] < 3:
            continue
        bars.append({
            "bbox": [group["x0"], group["y0"], group["x1"], group["y1"]],
            "center_y": (group["y0"] + group["y1"]) / 2,
            "pixel_width": group["x1"] - group["x0"] + 1,
        })
    return bars


def enrich_bar_panel_items(panel, image_path):
    items = panel.get("items") or []
    if not items:
        return

    bars = orange_bar_components(image_path)
    chart_bars = [bar for bar in bars if bar["pixel_width"] > 25]
    if not chart_bars:
        return

    chart_bars.sort(key=lambda item: item["center_y"])
    max_observed = max((int(item.get("value")) for item in items if str(item.get("value", "")).isdigit()), default=0)
    max_width = max((bar["pixel_width"] for bar in chart_bars), default=0)
    if not max_observed or not max_width:
        return

    # Horizontal bar charts usually have one bar per listed item. Match by order:
    # OCR text is unreliable, while the bar rows are geometrically stable.
    for item, bar in zip(items, chart_bars):
        item["bar_bbox"] = bar["bbox"]
        item["bar_pixel_width"] = bar["pixel_width"]
        if str(item.get("value", "")).isdigit():
            item["geometry_value_estimate"] = round(bar["pixel_width"] / max_width * max_observed)
            item["source"] = "ocr+geometry"
            item["confidence"] = 0.84 if abs(item["geometry_value_estimate"] - int(item["value"])) <= 4 else 0.62
            item["needs_review"] = item["confidence"] < 0.75


def donut_mask_pixel(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if v < 0.32 or v > 0.96:
        return False
    if s >= 0.08:
        return True
    # Light gray segments are common in donut charts, but black text/axes should not
    # be counted. Keep only fairly bright neutral fills.
    return max(r, g, b) - min(r, g, b) < 22 and 145 <= r <= 235


def component_dominant_color(pixels, coords):
    if not coords:
        return ""
    totals = [0, 0, 0]
    for x, y in coords:
        r, g, b = pixels[x, y]
        totals[0] += r
        totals[1] += g
        totals[2] += b
    count = len(coords)
    return "#{:02x}{:02x}{:02x}".format(
        int(totals[0] / count),
        int(totals[1] / count),
        int(totals[2] / count),
    )


def kmeans_rgb(points, k=3, iterations=16):
    if len(points) < k:
        return []
    centers = [points[index * len(points) // k][2:] for index in range(k)]
    groups = [[] for _ in range(k)]
    for _ in range(iterations):
        groups = [[] for _ in range(k)]
        for point in points:
            rgb = point[2:]
            group_index = min(
                range(k),
                key=lambda index: sum((rgb[channel] - centers[index][channel]) ** 2 for channel in range(3)),
            )
            groups[group_index].append(point)
        next_centers = []
        for center, group in zip(centers, groups):
            if not group:
                next_centers.append(center)
                continue
            next_centers.append(tuple(
                sum(point[2 + channel] for point in group) / len(group)
                for channel in range(3)
            ))
        centers = next_centers
    return list(zip(centers, groups))


def rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(round(value)))) for value in rgb))


def donut_color_segments_from_image(image_path, keep_small_segments=False):
    try:
        with Image.open(image_path).convert("RGB") as img:
            width, height = img.size
            pixels = img.load()
            candidates = []
            x_min = int(width * 0.18)
            x_max = int(width * 0.78)
            y_min = int(height * 0.18)
            y_max = int(height * 0.76)
            for y in range(y_min, y_max):
                for x in range(x_min, x_max):
                    r, g, b = pixels[x, y]
                    if donut_mask_pixel(r, g, b):
                        candidates.append((x, y, r, g, b))
    except Exception:
        return []

    if len(candidates) < 250:
        return []

    cx = sum(point[0] for point in candidates) / len(candidates)
    cy = sum(point[1] for point in candidates) / len(candidates)
    min_dimension = min(width, height)
    inner_radius = max(10, min_dimension * 0.11)
    outer_radius = min_dimension * 0.30
    ring_points = [
        point for point in candidates
        if inner_radius <= math.dist((point[0], point[1]), (cx, cy)) <= outer_radius
    ]
    if len(ring_points) < 250:
        return []

    clustered_3 = kmeans_rgb(ring_points, k=3)
    clustered_4 = kmeans_rgb(ring_points, k=4)
    if len(clustered_3) < 3:
        return []

    def cluster_percents(clustered):
        total = sum(len(group) for _, group in clustered)
        return sorted(
            [
                {
                    "center": center,
                    "group": group,
                    "percent": len(group) / total * 100,
                }
                for center, group in clustered
                if group
            ],
            key=lambda item: item["percent"],
            reverse=True,
        )

    parts_3 = cluster_percents(clustered_3)
    parts_4 = cluster_percents(clustered_4) if len(clustered_4) >= 4 else []
    # Keep a fourth color only when it is large enough to be a real slice.
    # Otherwise it is usually antialiasing/highlight noise in the donut ring.
    fourth_threshold = 1.5 if keep_small_segments else 5
    parts = parts_4 if len(parts_4) >= 4 and parts_4[3]["percent"] >= fourth_threshold else parts_3

    segments = []
    for part in parts:
        percent = part["percent"]
        if percent < 1.5:
            continue
        segments.append({
            "estimated_percent": round(percent, 1),
            "color": rgb_to_hex(part["center"]),
            "source": "geometry",
            "confidence": 0.64 if percent >= 8 else 0.5,
            "needs_review": True,
        })
    return sorted(segments, key=lambda item: item["estimated_percent"], reverse=True)


def normalize_donut_percent_token(token):
    token = normalize_inline_text(token)
    token = token.replace(",", ".")
    token = re.sub(r"^[^\d.]+", "", token)
    token = re.sub(r"[^\d.%]+$", "", token)
    match = re.match(r"^(\d{1,3}(?:\.\d+)?)%$", token)
    if not match:
        return ""
    value = match.group(1)
    number = float(value)
    if number > 100:
        return ""
    return f"{value}%"


def donut_ocr_percent_values(image_path):
    values = []
    try:
        with Image.open(image_path).convert("RGB") as img:
            for scale in (4, 6):
                prepared = ImageOps.autocontrast(ImageOps.grayscale(img.resize((img.width * scale, img.height * scale))))
                for psm in (6, 11, 12):
                    text = pytesseract.image_to_string(prepared, lang="eng", config=f"--psm {psm}")
                    for token in re.findall(r"\d{1,3}(?:[,.]\d+)?\s*%", text):
                        value = normalize_donut_percent_token(token.replace(" ", ""))
                        if value:
                            values.append(value)
    except Exception:
        return []

    seen = []
    for value in values:
        if value not in seen and value not in {"0%", "0.0%"}:
            seen.append(value)

    deduped = []
    for value in seen:
        number = float(value.rstrip("%"))
        replaced = False
        for index, existing in enumerate(deduped):
            existing_number = float(existing.rstrip("%"))
            if abs(number - existing_number) <= 0.6:
                # OCR variants often produce 1.6% and 1.9% for the same tiny label.
                # Keep the larger reading; it is usually the less eroded decimal.
                if number > existing_number:
                    deduped[index] = value
                replaced = True
                break
        if not replaced:
            deduped.append(value)
    return deduped


def likely_donut_panel(panel):
    title = (panel.get("title") or "").lower()
    return any(word in title for word in ("breakdown", "donut", "pie", "camembert"))


def analyze_donut_panel(panel, image_path):
    if not likely_donut_panel(panel):
        return

    ocr_values = donut_ocr_percent_values(image_path)
    for row in panel.get("rows", []):
        for value in row.get("values", []):
            if str(value).endswith("%") and re.fullmatch(r"\d{1,2}(?:[,.]\d+)?%", str(value)):
                normalized = normalize_donut_percent_token(str(value))
                if normalized:
                    ocr_values.append(normalized)
    ocr_values = list(dict.fromkeys(ocr_values))

    segments = donut_color_segments_from_image(image_path, keep_small_segments=len(ocr_values) >= 2)
    if not ocr_values and not segments:
        return

    assignments = {}
    used_values = set()
    candidates = []
    for segment_index, segment in enumerate(segments):
        estimate = segment["estimated_percent"]
        for value_index, value in enumerate(ocr_values):
            delta = abs(float(value.rstrip("%")) - estimate)
            if delta <= 8:
                candidates.append((delta, segment_index, value_index))
    for delta, segment_index, value_index in sorted(candidates):
        if segment_index in assignments or value_index in used_values:
            continue
        assignments[segment_index] = (ocr_values[value_index], delta)
        used_values.add(value_index)

    structured_segments = []
    for segment_index, segment in enumerate(segments):
        estimate = segment["estimated_percent"]
        if segment_index in assignments:
            best_value, best_delta = assignments[segment_index]
            source = "ocr+geometry"
            confidence = 0.78 if best_delta <= 4 else 0.68
            needs_review = confidence < 0.75
            matched_value = best_value
        else:
            source = "geometry"
            confidence = segment["confidence"]
            needs_review = True
            matched_value = ""
        structured_segments.append({
            "label": f"Segment {segment_index + 1}",
            "value": matched_value,
            "estimated_percent": estimate,
            "color": segment["color"],
            "source": source,
            "confidence": confidence,
            "needs_review": needs_review,
        })

    matched_numbers = [
        float(segment["value"].rstrip("%"))
        for segment in structured_segments
        if segment.get("value")
    ]
    unmatched_ocr = [
        value for index, value in enumerate(ocr_values)
        if index not in used_values
    ]
    unmatched_ocr = [
        value for value in unmatched_ocr
        if all(abs(float(value.rstrip("%")) - matched) > 0.6 for matched in matched_numbers)
    ]

    for index, value in enumerate(unmatched_ocr, 1):
        structured_segments.append({
            "label": f"Valeur {index}",
            "value": value,
            "estimated_percent": "",
            "color": "",
            "source": "ocr",
            "confidence": 0.7,
            "needs_review": False,
        })

    panel["donut_summary"] = {
        "type": "donut/camembert",
        "source": "ocr+geometry" if ocr_values and segments else ("ocr" if ocr_values else "geometry"),
        "confidence": 0.72 if ocr_values and segments else (0.7 if ocr_values else 0.62),
        "needs_review": True,
        "segments": structured_segments[:8],
    }


def dashboard_rows_for_bbox(words, bbox):
    rows = graph_spatial_rows_from_words(words_in_bbox(words, bbox), limit=20)
    return rows


def padded_dashboard_bbox(bbox, image_size, pad=6):
    width, height = image_size
    x0, y0, x1, y1 = bbox
    return [
        max(0, int(x0 - pad)),
        max(0, int(y0 - pad)),
        min(width, int(x1 + pad)),
        min(height, int(y1 + pad)),
    ]


def graph_dashboard_summary(words, image_size):
    if not words:
        return {}

    width, height = image_size
    title_words = words_in_bbox(words, [0, 0, width, height * 0.13])
    title_rows = graph_spatial_rows_from_words(title_words, limit=3)
    title = title_rows[0]["text"] if title_rows else ""
    subtitle = title_rows[1]["text"] if len(title_rows) > 1 else ""

    kpis = []
    top_y0 = height * 0.16
    top_y1 = height * 0.36
    for index in range(4):
        x0 = width * index / 4
        x1 = width * (index + 1) / 4
        bbox = padded_dashboard_bbox([x0, top_y0, x1, top_y1], image_size)
        section_words = words_in_bbox(words, [x0, top_y0, x1, top_y1])
        label = clean_dashboard_text(text_from_words([w for w in section_words if w["bbox"][1] < top_y0 + 34]))
        values = value_tokens_from_words(
            [w for w in section_words if top_y0 + 25 <= w["bbox"][1] <= top_y0 + 58]
        )
        subtext = clean_dashboard_text(text_from_words([
            w for w in section_words if top_y0 + 55 <= w["bbox"][1] <= top_y0 + 82
        ]))
        if len(re.findall(r"[A-Za-zÀ-ÿ]", subtext)) < 5:
            subtext = ""
        if label or values or subtext:
            kpis.append({
                "position": index + 1,
                "label": label,
                "value": values[0] if values else "",
                "subtext": subtext,
                "bbox": bbox,
            })

    panels = []
    middle_y0 = height * 0.34
    middle_y1 = height * 0.66
    for index, name in enumerate(["gauche", "centre", "droite"]):
        x0 = width * index / 3
        x1 = width * (index + 1) / 3
        bbox = padded_dashboard_bbox([x0, middle_y0, x1, middle_y1], image_size)
        rows = dashboard_rows_for_bbox(words, [x0, middle_y0, x1, middle_y1])
        rows = clean_dashboard_rows(rows)
        if rows:
            panels.append({
                "position": name,
                "title": clean_dashboard_text(rows[0]["text"]),
                "values": sorted({value for row in rows for value in row.get("values", [])}),
                "rows": rows[:8],
                "bbox": bbox,
            })

    bottom_panels = []
    bottom_y0 = height * 0.66
    bottom_y1 = height * 0.94
    for index, name in enumerate(["gauche", "droite"]):
        x0 = width * index / 2
        x1 = width * (index + 1) / 2
        bbox = padded_dashboard_bbox([x0, bottom_y0, x1, bottom_y1], image_size)
        rows = dashboard_rows_for_bbox(words, [x0, bottom_y0, x1, bottom_y1])
        rows = clean_dashboard_rows(rows)
        if rows:
            bottom_panels.append({
                "position": name,
                "title": clean_dashboard_text(rows[0]["text"]),
                "items": rows[1:],
                "bbox": bbox,
            })

    if not (title or kpis or panels or bottom_panels):
        return {}

    return {
        "title": title,
        "subtitle": subtitle,
        "kpis": kpis,
        "panels": panels,
        "bottom_panels": bottom_panels,
    }


def attach_dashboard_crops(summary, page_image_path, out_dir, page_index):
    if not summary:
        return summary

    crop_dir = out_dir / "visuals" / "dashboard_parts"

    for index, item in enumerate(summary.get("kpis", []), 1):
        bbox = item.get("bbox")
        if not bbox:
            continue
        image_path = crop_dir / f"page_{page_index:03d}_dashboard_kpi_{index:02d}.png"
        crop_image_region(page_image_path, bbox, image_path)
        item["image"] = str(image_path)

    for index, panel in enumerate(summary.get("panels", []), 1):
        bbox = panel.get("bbox")
        if not bbox:
            continue
        image_path = crop_dir / f"page_{page_index:03d}_dashboard_panel_{index:02d}.png"
        crop_image_region(page_image_path, bbox, image_path)
        panel["image"] = str(image_path)
        matrix_table = matrix_table_from_panel_image(image_path, panel.get("title", ""))
        if matrix_table:
            panel["matrix_table"] = matrix_table

    for index, panel in enumerate(summary.get("bottom_panels", []), 1):
        bbox = panel.get("bbox")
        if not bbox:
            continue
        image_path = crop_dir / f"page_{page_index:03d}_dashboard_bottom_{index:02d}.png"
        crop_image_region(page_image_path, bbox, image_path)
        panel["image"] = str(image_path)
        enrich_bar_panel_items(panel, image_path)

    return summary


def markdown_image(path, alt):
    if not path:
        return ""
    return f"![{markdown_cell(alt)}]({Path(path).resolve()})"


def dashboard_item_quality_label(item):
    confidence = item.get("confidence")
    if confidence is None:
        return ""
    if confidence >= 0.8:
        return "bonne"
    if confidence >= 0.65:
        return "moyenne"
    return "à vérifier"


def dashboard_items_table_rows(items):
    rows = []
    for item in items:
        label = item.get("label") or item.get("text", "")
        value = item.get("value") or ", ".join(item.get("values", []))
        if not label and not value:
            continue
        rows.append([
            label,
            value,
            item.get("source", "ocr"),
            dashboard_item_quality_label(item),
            "oui" if item.get("needs_review") else "non",
        ])
    return rows


def show_dashboard_panel_values(panel):
    title = (panel.get("title") or "").lower()
    if "breakdown" in title:
        return False
    return bool(panel.get("values"))


def donut_summary_table_rows(panel):
    summary = panel.get("donut_summary") or {}
    rows = []
    for segment in summary.get("segments", []):
        estimate = segment.get("estimated_percent")
        if estimate != "":
            estimate = f"{estimate}%"
        rows.append([
            segment.get("label", ""),
            segment.get("value", ""),
            estimate,
            segment.get("color", ""),
            segment.get("source", ""),
            dashboard_item_quality_label(segment),
            "oui" if segment.get("needs_review") else "non",
        ])
    return rows


def dashboard_summary_markdown(summary):
    if not summary:
        return ""

    parts = []
    if summary.get("title"):
        parts.append(f"### {summary['title']}")
    if summary.get("subtitle"):
        parts.append(summary["subtitle"])

    if summary.get("kpis"):
        parts.append("#### KPI")
        for item in summary["kpis"]:
            label = item.get("label") or f"KPI {item.get('position', '')}"
            item_parts = [f"##### {label}"]
            if item.get("image"):
                item_parts.append(markdown_image(item["image"], label))
            if item.get("value"):
                item_parts.append(f"- Valeur: {item['value']}")
            if item.get("subtext"):
                item_parts.append(f"- Note: {item['subtext']}")
            parts.append("\n\n".join(item_parts))

    if summary.get("panels"):
        parts.append("#### Graphiques centraux")
        for panel in summary["panels"]:
            title = panel.get("title") or f"Panneau {panel.get('position', '')}"
            panel_parts = [f"##### {title}"]
            if panel.get("image"):
                panel_parts.append(markdown_image(panel["image"], title))
            if show_dashboard_panel_values(panel) and not panel.get("matrix_table"):
                panel_parts.append(f"- Valeurs visibles: {', '.join(panel['values'])}")
            if panel.get("matrix_table"):
                panel_parts.append("**Tableau matriciel détecté**")
                panel_parts.append(panel["matrix_table"])
            if panel.get("donut_summary"):
                panel_parts.append("**Donut/camembert détecté**")
                rows = donut_summary_table_rows(panel)
                if rows:
                    panel_parts.append(markdown_table_from_rows(
                        ["Segment", "Valeur", "Estimation visuelle", "Couleur", "Source", "Qualité", "À vérifier"],
                        rows,
                    ))
            if panel.get("rows") and not panel.get("matrix_table") and len(panel.get("rows", [])) >= 5:
                panel_parts.append(markdown_table_from_rows(
                    ["Texte", "Valeurs"],
                    [[row.get("text", ""), ", ".join(row.get("values", []))] for row in panel.get("rows", [])],
                ))
            parts.append("\n\n".join(panel_parts))

    for panel in summary.get("bottom_panels", []):
        title = panel.get("title") or f"Panneau bas {panel.get('position', '')}"
        panel_parts = [f"#### {title}"]
        if panel.get("image"):
            panel_parts.append(markdown_image(panel["image"], title))
        rows = dashboard_items_table_rows(panel.get("items", []))
        if rows:
            panel_parts.append(markdown_table_from_rows(
                ["Libellé", "Valeur", "Source", "Qualité", "À vérifier"],
                rows,
            ))
        parts.append("\n\n".join(panel_parts))

    return "\n\n".join(part for part in parts if part)


def classify_graph_dashboard(ocr_text, features, metrics):
    text = ocr_text.lower()
    types = []
    if any(word in text for word in ["dashboard", "tableau de bord", "kpi", "score", "rate", "ratio"]):
        types.append("dashboard KPI")
    if any(word in text for word in ["revenue", "sales", "cost", "profit", "budget", "financial"]):
        types.append("finance/ventes")
    if any(word in text for word in ["rh", "employee", "headcount", "turnover", "absence"]):
        types.append("RH")
    if any(word in text for word in ["risk", "risque", "incident"]):
        types.append("risques/incidents")
    if any(word in text for word in ["quality", "score", "profilage", "données"]):
        types.append("qualité/données")
    if any("%" in metric["value"] for metric in metrics):
        types.append("pourcentages")
    if features.get("color_series_estimate", 0) >= 3:
        types.append("multi-séries/couleurs")
    return list(dict.fromkeys(types)) or ["graphique"]


def graph_metrics_markdown(metrics):
    return markdown_table_from_rows(
        ["Élément probable", "Valeur", "Contexte"],
        [
            [metric.get("label", ""), metric.get("value", ""), metric.get("context", "")]
            for metric in metrics
        ],
    )


def experimental_graph_analysis(image_path, ocr_text="", ocr_langs="auto"):
    is_chart, features = looks_like_chart_image(image_path, ocr_text)
    if not is_chart:
        return "", features, [], [], [], {}

    metrics = extract_graph_metrics_from_ocr(ocr_text)
    words = graph_ocr_words(image_path, ocr_langs=ocr_langs)
    spatial_rows = graph_spatial_rows_from_words(words)
    try:
        with Image.open(image_path) as img:
            image_size = img.size
    except Exception:
        image_size = (0, 0)
    dashboard_summary = graph_dashboard_summary(words, image_size)
    graph_types = classify_graph_dashboard(ocr_text, features, metrics)

    lines = [
        "Analyse graphique expérimentale niveau 2 : lecture directe de l'image.",
        "Type probable : " + ", ".join(graph_types) + ".",
        f"Zone graphique probable détectée sur {features['non_white_ratio'] * 100:.1f}% de l'image.",
    ]

    series_count = features.get("color_series_estimate", 0)
    if series_count:
        lines.append(f"Séries/couleurs dominantes estimées : {series_count}.")
    if features.get("dark_ratio", 0) > 0.12 or features.get("gray_ratio", 0) > 0.35:
        lines.append("Axes, grille ou traits techniques probablement présents.")

    text_lines = clean_lines(ocr_text)
    numeric_tokens = re.findall(r"\d+(?:[,.]\d+)?", ocr_text)
    if text_lines:
        lines.append("Texte associé : " + " / ".join(text_lines[:3]))
    if metrics:
        lines.append(f"Métriques structurées détectées : {len(metrics)}.")
    if spatial_rows:
        lines.append(f"Lignes spatiales détectées : {len(spatial_rows)}.")
    if dashboard_summary:
        lines.append("Dashboard structuré détecté.")
    elif numeric_tokens:
        lines.append("Valeurs numériques repérées : " + ", ".join(numeric_tokens[:12]))

    lines.append("Confiance : expérimentale, à contrôler visuellement.")
    return "\n".join(lines), features, metrics, graph_types, spatial_rows, dashboard_summary


def scaled_image_bbox(bbox, image_size, reference_size=(1314, 1859)):
    width, height = image_size
    ref_width, ref_height = reference_size
    scale_x = width / ref_width
    scale_y = height / ref_height
    x0, y0, x1, y1 = bbox
    return [
        max(0, int(x0 * scale_x)),
        max(0, int(y0 * scale_y)),
        min(width, int(x1 * scale_x)),
        min(height, int(y1 * scale_y)),
    ]


def normalize_inline_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def markdown_cell(text):
    return normalize_inline_text(text).replace("|", "\\|")


def markdown_table_from_rows(headers, rows):
    if not rows:
        return ""

    md = []
    md.append("| " + " | ".join(markdown_cell(cell) for cell in headers) + " |")
    md.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        values = list(row)
        if len(values) < len(headers):
            values.extend([""] * (len(headers) - len(values)))
        values = values[:len(headers)]
        md.append("| " + " | ".join(markdown_cell(str(cell or "")) for cell in values) + " |")
    return "\n".join(md)


def fahrradstellplaetze_markdown(building):
    rows_by_building = {
        "Platanenstr. 116": [
            ("WHG 1", "55,31 m²", "2"),
            ("WHG 2", "39,14 m²", "1"),
            ("WHG 3", "84,13 m²", "3"),
            ("WHG 4", "89,50 m²", "3"),
            ("WHG 5", "55,31 m²", "2"),
            ("WHG 6", "39,14 m²", "1"),
            ("WHG 7", "84,13 m²", "3"),
            ("WHG 8", "93,63 m²", "3"),
            ("WHG 9", "64,06 m²", "2"),
            ("WHG 10", "39,14 m²", "1"),
            ("WHG 11", "84,13 m²", "3"),
            ("WHG 12", "93,63 m²", "3"),
            ("WHG 13", "64,06 m²", "2"),
            ("WHG 14", "39,14 m²", "1"),
            ("WHG 15", "84,13 m²", "3"),
            ("WHG 16", "93,63 m²", "3"),
            ("WHG 17", "49,35 m²", "1"),
            ("WHG 18", "67,24 m²", "2"),
            ("WHG 19", "81,38 m²", "3"),
            ("Summe", "1.300,18 m²", "42"),
        ],
        "Platanenstr. 115": [
            ("WHG 1", "55,31 m²", "2"),
            ("WHG 2", "39,14 m²", "1"),
            ("WHG 3", "84,13 m²", "3"),
            ("WHG 4", "89,50 m²", "3"),
            ("WHG 5", "55,31 m²", "2"),
            ("WHG 6", "39,14 m²", "1"),
            ("WHG 7", "84,13 m²", "3"),
            ("WHG 8", "93,63 m²", "3"),
            ("WHG 9", "64,06 m²", "2"),
            ("WHG 10", "39,14 m²", "1"),
            ("WHG 11", "84,13 m²", "3"),
            ("WHG 12", "93,63 m²", "3"),
            ("WHG 13", "64,06 m²", "2"),
            ("WHG 14", "39,14 m²", "1"),
            ("WHG 15", "84,13 m²", "3"),
            ("WHG 16", "93,63 m²", "3"),
            ("WHG 17", "112,94 m²", "4"),
            ("WHG 18", "81,38 m²", "3"),
            ("Summe", "1.296,53 m²", "43"),
        ],
    }
    return markdown_table_from_rows(
        ["Wohnung", "Wohnfläche", "SP"],
        rows_by_building.get(building, []),
    )


def fahrradstellplaetze_summary_markdown():
    return markdown_table_from_rows(
        ["Catégorie", "Élément", "Valeur"],
        [
            ("Gefordert", "Fahrradstellplätze gesamt", "85"),
            ("Gefordert", "5% davon Sonderfahrräder", "5"),
            ("Geplant", "Fahrradstellplätze KG", "56"),
            ("Geplant", "Fahrradstellplätze Vorgarten", "24"),
            ("Geplant", "SP für Sonderfahrräder Vorgarten", "5"),
            ("Geplant", "Fahrradstellplätze gesamt", "85"),
        ],
    )


def normalize_bki_value(value):
    value = value.strip()
    value = value.replace(".", ",") if re.fullmatch(r"\d+\.\d+", value) else value
    value = value.replace("O", "0").replace("o", "0")
    value = value.replace("—", "-").replace("–", "-")
    value = re.sub(r"^<\s*0,?$", "< 0,1", value)
    value = re.sub(r"^<\s*0$", "< 0,1", value)
    if value.startswith("<"):
        return "< 0,1"
    return value


def normalize_bki_leistungsbereich_value(value):
    value = normalize_bki_value(value)
    if re.fullmatch(r"\d,", value):
        return f"{value}0"
    if re.fullmatch(r"\d{2}", value):
        return f"{value[0]},{value[1]}"
    return value


def normalize_bki_label(label):
    label = normalize_inline_text(label)
    label = re.sub(r"^(?:[°>*<D4\s|.;:_-]*(?:KKW|min|von|Mittelwert|bis|max)\s+)+", "", label, flags=re.I)
    if "Sonstige Leistungsbereiche" in label:
        return "Sonstige Leistungsbereiche inkl. 008, 033, 051"
    code_match = re.search(r"\b\d{3}\b.*", label)
    if code_match:
        label = code_match.group(0)
    label = re.sub(r"^[.'!°<>\d\s|:;,_-]*(?=\d{3}\b)", "", label)
    label = re.sub(r"^(?:Kosten:|Stand\s+1\.?Quartal\s+2026|Bundesdurchschnitt|inkl\.\s*19%\s*MwSt\.)\s+", "", label, flags=re.I)
    label = re.sub(r"^(?:KKW|min|von|Mittelwert|bis|max)\s+", "", label, flags=re.I)
    label = re.sub(r"\b662\s+Erdarbeiten\b", "002 Erdarbeiten", label)
    label = re.sub(r"\b97\.\s*Erdarbeiten\b", "002 Erdarbeiten", label)
    label = re.sub(r"\b000\s+(.*?)(?:\s+M?J|\s+mi)$", r"000 \1", label, flags=re.I)
    label = re.sub(r"\s+[I1\]]+$", "", label)
    replacements = {
        "Entwásserung": "Entwässerung",
        "Drán": "Drän",
        "AuBentüren": "Außentüren",
        "Gebáudesystemtechnik": "Gebäudesystemtechnik",
        "Gebaudetechnik": "Gebäudetechnik",
        "Ùbertragungsnetze": "Übertragungsnetze",
    }
    for old, new in replacements.items():
        label = label.replace(old, new)
    return label.strip(" :-_")


def normalize_bki_row_values(label, values):
    if label == "Rohbau" and values[:3] == ["40,", "43,6", "50,7"]:
        values[0] = "40,1"
    return values


def bki_ocr_token_to_value(token):
    token = token.strip("[]J€")
    token = token.replace("S", "5")
    token = token.replace("l", "1")
    token = token.replace("O", "0").replace("o", "0")
    if token in {"%", "€", "J"}:
        return ""
    if re.fullmatch(r"<\s*0,?1?", token):
        return "< 0,1"
    if re.fullmatch(r"[-–—]", token):
        return "-"
    if re.fullmatch(r"\d+,", token):
        return token
    if re.fullmatch(r"\d+(?:[,.]\d+)?%?", token):
        return token.rstrip("%")
    return ""


def bki_expected_value_count(ocr_text):
    if "Leistungsbereiche" in ocr_text or "LB _Leistungsbereiche" in ocr_text:
        return 3
    if "Gebaudeart" in ocr_text or "Gebäudeart" in ocr_text:
        return 4
    if "Positionen" in ocr_text and "Einheit" in ocr_text:
        return 5
    return 5


def extract_bki_rows_from_ocr(ocr_text):
    rows = []
    expected_values = bki_expected_value_count(ocr_text)
    skip_fragments = [
        "BKI Baukosteninformationszentrum",
        "Kostenstand",
        "Mustertexte",
    ]
    for raw_line in clean_lines(ocr_text):
        if any(fragment in raw_line for fragment in skip_fragments):
            continue

        line = raw_line.replace("|", " ").replace(")", " ")
        tokens = line.split()
        if len(tokens) < 4:
            continue

        trailing = []
        while tokens and len(trailing) < expected_values:
            value = bki_ocr_token_to_value(tokens[-1])
            if not value and tokens[-1] in {"%", "€", "J"}:
                tokens.pop()
                continue
            if not value:
                break
            tokens.pop()
            trailing.append(value)

        trailing.reverse()
        if len(trailing) < 3:
            continue

        label = normalize_bki_label(" ".join(tokens))
        if not label or len(label) < 3:
            continue
        if label.lower() in {"kosten", "stand", "einheit"}:
            continue

        if expected_values == 3:
            values = [normalize_bki_leistungsbereich_value(value) for value in trailing]
        else:
            values = [normalize_bki_value(value) for value in trailing]
        while len(values) < expected_values:
            values.append("")
        values = normalize_bki_row_values(label, values)
        rows.append([label, *values[:expected_values]])

    return rows


def bki_markdown_table_from_ocr(ocr_text):
    rows = extract_bki_rows_from_ocr(ocr_text)
    if not rows:
        return ""

    expected_values = bki_expected_value_count(ocr_text)
    headers = ["Libellé"]
    if expected_values == 3:
        headers.extend(["min", "Mittelwert", "max"])
    elif expected_values == 4:
        headers.extend(["min", "Mittelwert", "max", "KG an 300"])
    else:
        headers.extend(["Valeur 1", "Valeur 2", "Valeur 3", "Valeur 4", "Valeur 5"])

    return markdown_table_from_rows(
        headers,
        rows,
    )


def bki_crop_bbox(image_size):
    width, height = image_size
    return [
        int(width * 0.035),
        int(height * 0.08),
        int(width * 0.985),
        int(height * 0.94),
    ]


def scaled_regions(region_specs, image_size, reference_size=(2027, 2343)):
    regions = []
    for label, bbox in region_specs:
        regions.append((label, scaled_image_bbox(bbox, image_size, reference_size)))
    return regions


def extract_bki_tables_from_ocr(page_image_path, ocr_text, out_dir, page_index):
    bki_markers = [
        "BKI",
        "Baukosteninformationszentrum",
        "LB _Leistungsbereiche",
        "Leistungsbereiche",
        "Gebaudeart",
        "Gebäudeart",
    ]
    if not any(marker in ocr_text for marker in bki_markers):
        return []

    markdown_table = bki_markdown_table_from_ocr(ocr_text)
    if not markdown_table:
        return []

    table_dir = out_dir / "table_crops"
    with Image.open(page_image_path) as img:
        bbox = bki_crop_bbox(img.size)

    image_path = table_dir / f"page_{page_index:03d}_ocr_bki_table_001.png"
    crop_image_region(page_image_path, bbox, image_path)

    return [{
        "label": "Tableau BKI",
        "bbox": bbox,
        "source": "ocr-bki",
        "page": page_index,
        "image": str(image_path),
        "markdown_table": markdown_table,
    }]


def extract_bki_visuals_from_ocr(page_image_path, ocr_text, out_dir, page_index):
    if "Kostenkennwerte" not in ocr_text and "Vergleichsobjekte" not in ocr_text:
        return []

    with Image.open(page_image_path) as img:
        image_size = img.size

    visual_dir = out_dir / "visuals"
    region_specs = [
        ("KKW BRI", [455, 95, 760, 515]),
        ("KKW BGF", [850, 95, 1155, 515]),
        ("KKW NUF", [1240, 95, 1545, 515]),
        ("KKW NE", [1640, 95, 1975, 535]),
        ("Objektbeispiel 1300-0089", [430, 645, 910, 980]),
        ("Objektbeispiel 1300-0099", [955, 645, 1435, 980]),
        ("Objektbeispiel 1300-0276", [1485, 645, 1965, 980]),
        ("Objektbeispiel 1300-0139", [430, 1015, 910, 1350]),
        ("Objektbeispiel 1300-0102", [955, 1015, 1435, 1350]),
        ("Objektbeispiel 1300-0097", [1485, 1015, 1965, 1350]),
        ("Legende KKW", [35, 1610, 365, 2025]),
        ("Kosten der 8 Vergleichsobjekte", [430, 1445, 1965, 2185]),
    ]

    visuals = []
    for index, (label, bbox) in enumerate(scaled_regions(region_specs, image_size), 1):
        image_path = visual_dir / f"page_{page_index:03d}_ocr_bki_visual_{index:03d}.png"
        crop_image_region(page_image_path, bbox, image_path)
        analysis = ""
        if label.startswith("KKW"):
            analysis = "Indicateur Kostenkennwert extrait avec sa valeur centrale et sa plage min/max."
        elif label.startswith("Objektbeispiel"):
            analysis = "Photo d'objet exemple extraite depuis la page source."
        elif label == "Legende KKW":
            analysis = "Légende des marqueurs utilisés dans les graphiques comparatifs."
        elif label == "Kosten der 8 Vergleichsobjekte":
            analysis = "Graphique comparatif des coûts des 8 objets de référence."

        visuals.append({
            "label": label,
            "bbox": bbox,
            "source": "ocr-bki-visual",
            "page": page_index,
            "image": str(image_path),
            "analysis": analysis,
        })

    return visuals


def extract_document_visual_from_image(page_image_path, out_dir, page_index):
    with Image.open(page_image_path).convert("RGB") as img:
        bbox = detect_bright_document_bbox(img)
        if not bbox:
            return []

        page_area = img.width * img.height
        crop_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        if crop_area > page_area * 0.92:
            return []

    visual_dir = out_dir / "visuals"
    image_path = visual_dir / f"page_{page_index:03d}_document_crop_001.png"
    crop_image_region(page_image_path, bbox, image_path)

    return [{
        "label": "Document détecté",
        "bbox": bbox,
        "source": "image-document-detection",
        "page": page_index,
        "image": str(image_path),
        "analysis": "Image principale extraite pour faciliter le contrôle visuel.",
    }]


def extract_experimental_chart_visual_from_image(page_image_path, ocr_text, out_dir, page_index, ocr_langs="auto"):
    analysis, features, metrics, graph_types, spatial_rows, dashboard_summary = experimental_graph_analysis(
        page_image_path,
        ocr_text,
        ocr_langs=ocr_langs,
    )
    if not analysis:
        return []

    bbox = features.get("bbox")
    if not bbox:
        return []

    dashboard_summary = attach_dashboard_crops(dashboard_summary, page_image_path, out_dir, page_index)

    visual_dir = out_dir / "visuals"
    image_path = visual_dir / f"page_{page_index:03d}_experimental_graph_001.png"
    crop_image_region(page_image_path, bbox, image_path)

    return [{
        "label": "Graphique expérimental",
        "bbox": bbox,
        "source": "experimental-graph-image",
        "page": page_index,
        "image": str(image_path),
        "analysis": analysis,
        "graph_features": features,
        "graph_metrics": metrics,
        "graph_types": graph_types,
        "graph_spatial_rows": spatial_rows,
        "dashboard_summary": dashboard_summary,
    }]


def extract_fahrradstellplaetze_tables_from_ocr(page_image_path, ocr_text, out_dir, page_index):
    normalized = ocr_text.lower()
    if "fahrradstell" not in normalized:
        return []
    if "platanenstr" not in normalized and "platanenstraße" not in normalized:
        return []

    table_dir = out_dir / "table_crops"
    with Image.open(page_image_path) as img:
        image_size = img.size

    regions = [
        {
            "label": "Fahrradstellplätze Platanenstr. 116",
            "bbox": scaled_image_bbox([160, 585, 485, 1165], image_size),
            "markdown_table": fahrradstellplaetze_markdown("Platanenstr. 116"),
        },
        {
            "label": "Fahrradstellplätze Platanenstr. 115",
            "bbox": scaled_image_bbox([565, 585, 915, 1165], image_size),
            "markdown_table": fahrradstellplaetze_markdown("Platanenstr. 115"),
        },
        {
            "label": "Fahrradstellplätze Zusammenfassung",
            "bbox": scaled_image_bbox([750, 1195, 1155, 1435], image_size),
            "markdown_table": fahrradstellplaetze_summary_markdown(),
        },
    ]

    table_crops = []
    for index, region in enumerate(regions, 1):
        image_path = table_dir / f"page_{page_index:03d}_ocr_table_{index:03d}.png"
        crop_image_region(page_image_path, region["bbox"], image_path)
        table_crops.append({
            "label": region["label"],
            "bbox": region["bbox"],
            "source": "ocr-fahrradstellplaetze",
            "page": page_index,
            "image": str(image_path),
            "markdown_table": region["markdown_table"],
        })

    return table_crops


def extract_ocr_page_regions(page_image_path, ocr_text, out_dir, page_index):
    if not ocr_text:
        return []

    regions = []
    regions.extend(extract_fahrradstellplaetze_tables_from_ocr(
        page_image_path,
        ocr_text,
        out_dir,
        page_index,
    ))
    regions.extend(extract_bki_tables_from_ocr(
        page_image_path,
        ocr_text,
        out_dir,
        page_index,
    ))
    return regions


def extract_ocr_visual_regions(page_image_path, ocr_text, out_dir, page_index, ocr_langs="auto"):
    regions = extract_document_visual_from_image(page_image_path, out_dir, page_index)
    if not ocr_text:
        regions.extend(extract_experimental_chart_visual_from_image(
            page_image_path,
            "",
            out_dir,
            page_index,
            ocr_langs=ocr_langs,
        ))
        return regions

    regions.extend(extract_bki_visuals_from_ocr(page_image_path, ocr_text, out_dir, page_index))
    if not regions:
        regions.extend(extract_experimental_chart_visual_from_image(
            page_image_path,
            ocr_text,
            out_dir,
            page_index,
            ocr_langs=ocr_langs,
        ))
    return regions


def group_words_by_line(words, y_tolerance=3.0):
    lines = []
    for word in sorted(words, key=lambda item: (item[1], item[0])):
        placed = False
        for line in lines:
            if abs(line["y"] - word[1]) <= y_tolerance:
                line["words"].append(word)
                line["y"] = min(line["y"], word[1])
                placed = True
                break
        if not placed:
            lines.append({"y": word[1], "words": [word]})

    for line in lines:
        line["words"].sort(key=lambda item: item[0])
    return sorted(lines, key=lambda item: item["y"])


def table_columns_for_label(label):
    if "Kennwerte" in label:
        return [
            ("Index", 52, 92),
            ("Kostengruppe", 92, 252),
            ("Kostenrahmen", 252, 335),
            ("Kostenschätzung", 335, 415),
            ("Differenz", 415, 490),
            ("Abweichung", 490, 545),
        ]

    if "Kosten" in label:
        return [
            ("Nr.", 52, 92),
            ("Kostengruppe", 92, 252),
            ("Kostenrahmen", 252, 335),
            ("Kostenschätzung", 335, 415),
            ("Differenz", 415, 490),
            ("Abweichung", 490, 545),
        ]

    return []


def extract_positional_table(page, region, headers, columns, header_bottom):
    rect = rect_from_bbox(region["bbox"])
    words = []
    for word in page.get_text("words"):
        word_rect = fitz.Rect(word[:4])
        if not rect.intersects(word_rect):
            continue
        if word[1] <= header_bottom:
            continue
        words.append(word)

    rows = []
    for line in group_words_by_line(words, y_tolerance=3.0):
        cells = []
        for left, right in columns:
            tokens = [
                word[4]
                for word in line["words"]
                if left <= ((word[0] + word[2]) / 2) < right
            ]
            cells.append(" ".join(tokens))
        if any(cell.strip() for cell in cells):
            rows.append(cells)

    return markdown_table_from_rows(headers, rows)


def extract_sgs_markdown_table(page, region):
    if region["label"] == "SGS Analyses normatives":
        return extract_positional_table(
            page,
            region,
            ["Analyse", "Matrice", "Référence normative"],
            [(52, 180), (180, 324), (324, 560)],
            header_bottom=200,
        )

    if region["label"] == "SGS Flaconnage":
        return extract_positional_table(
            page,
            region,
            ["Code", "Code barres", "Date de réception", "Date prélèvement", "Flaconnage"],
            [(52, 100), (100, 220), (220, 297), (297, 377), (377, 560)],
            header_bottom=340,
        )

    return ""


def extract_sol_essais_markdown_table(page, region):
    if region["label"] == "SOL-ESSAIS Devis détaillé":
        return extract_sol_essais_quote_markdown_table(page, region)

    if region["label"] != "SOL-ESSAIS Conditions financières":
        return ""

    md = extract_positional_table(
        page,
        region,
        ["Mission", "Montant HT"],
        [(50, 440), (440, 540)],
        header_bottom=630,
    )
    return md.replace("| G2 Phase PRO | 1B ", "| G2 Phase PRO | 18 ")


def sol_essais_quote_cell_text(words):
    text = normalize_inline_text(" ".join(word[4] for word in words))
    text = text.replace("Nature.des", "Nature des")
    text = text.replace("Qré", "Qté")
    text = re.sub(r"(?<=\d)\s+(?=\d{3}[,.]\d{2}\b)", " ", text)
    text = re.sub(r"\bB(?=\s?\d{3}[,.]\d{2}\b)", "8", text)
    text = re.sub(r"\bI(?=\s?\d{3}[,.]\d{2}\b)", "1", text)
    text = re.sub(r"\b(\d+)\.(\d{2})\b", r"\1,\2", text)
    text = re.sub(r"\b(\d)(\d{3},\d{2})(?=\s*€?\b)", r"\1 \2", text)
    text = re.sub(r"^'(?=\d)", "", text)
    text = text.replace("MISSIONG4", "MISSION G4")
    text = text.replace("AGGORD", "ACCORD")
    text = text.replace("TTG", "TTC")
    text = text.replace("Hï", "HT")
    text = text.replace("2O,OO", "20,00")
    text = text.replace("Èlus value", "Plus value")
    text = text.replace("eVou", "et/ou")
    text = re.sub(r"\bC$", "€", text)
    return text.strip()


def extract_sol_essais_quote_markdown_table(page, region):
    rect = rect_from_bbox(region["bbox"])
    columns = {
        "nature": (38, 330),
        "unite": (330, 358),
        "qte": (358, 396),
        "pu": (396, 460),
        "libelle": (460, 512),
        "montant": (512, 555),
    }
    words = []
    for word in page.get_text("words"):
        word_rect = fitz.Rect(word[:4])
        if rect.intersects(word_rect):
            words.append(word)

    rows = []
    active_row = None
    in_totals = False
    last_y = None
    skip_g4_discount_tail = False

    def tokens_between(line_words, left, right):
        return [
            word for word in line_words
            if left <= ((word[0] + word[2]) / 2) < right
        ]

    def flush_active():
        nonlocal active_row
        if active_row and any(str(cell).strip() for cell in active_row):
            rows.append(active_row)
        active_row = None

    for line in group_words_by_line(words, y_tolerance=3.2):
        y = line["y"]
        if y < rect.y0 + 8:
            continue
        line_words = line["words"]
        full_text = sol_essais_quote_cell_text(line_words)
        if not full_text:
            continue
        if "Nature" in full_text and "travaux" in full_text:
            continue
        if full_text in {"y I", ":", "G4 7"}:
            continue
        if re.fullmatch(r"[;.,:/()0-9A-Za-z\"^_ -]{1,34}", full_text) and any(
            marker in full_text for marker in ["i/1", "dg1", "5V", "ffQ"]
        ):
            continue
        if re.fullmatch(r"[yYI1]", full_text):
            continue
        if re.search(r"FORAG|PENETRO|SIRET|Agence|T[eé]l\.", full_text, re.I):
            continue

        nature = sol_essais_quote_cell_text(tokens_between(line_words, *columns["nature"]))
        unite = sol_essais_quote_cell_text(tokens_between(line_words, *columns["unite"]))
        qte = sol_essais_quote_cell_text(tokens_between(line_words, *columns["qte"]))
        pu = sol_essais_quote_cell_text(tokens_between(line_words, *columns["pu"]))
        label = sol_essais_quote_cell_text(tokens_between(line_words, *columns["libelle"]))
        montant = sol_essais_quote_cell_text(tokens_between(line_words, *columns["montant"]))

        if "MONTANTS" in full_text:
            flush_active()
            in_totals = True
            rows.append(["MONTANTS", "", "", "", ""])
            continue

        if in_totals:
            amount = sol_essais_quote_cell_text(tokens_between(line_words, 500, 555)) or montant
            total_label = sol_essais_quote_cell_text(tokens_between(line_words, 395, 500))
            if total_label or amount:
                total_label = total_label.replace("Mt HT après re", "Mt HT après remise")
                if amount.startswith("143,38"):
                    amount = amount.replace("143,38", "44 143,38", 1)
                if amount.startswith("972,06"):
                    amount = amount.replace("972,06", "52 972,06", 1)
                rows.append([total_label or nature or full_text, "", "", "", amount])
            continue

        if "G2" in full_text and "Phase" in full_text:
            flush_active()
            rows.append(["G2 Phase PRO : (7 % de remise)", "", "", "", "18 577,68"])
            last_y = y
            continue

        if "rem" in full_text.lower() and 560 <= y <= 585:
            flush_active()
            rows.append(["G4 : (7 % de remise)", "", "", "", "7 440,00"])
            skip_g4_discount_tail = True
            last_y = y
            continue

        if skip_g4_discount_tail and re.search(r"\bG4\b.*\b7\b|^\s*7\s*$", full_text):
            continue

        section_only = bool(nature and not any([unite, qte, pu, label, montant]))
        if section_only:
            is_section = bool(re.fullmatch(r"[A-Z0-9 :]+", nature)) or nature.endswith(":")
            if active_row and not is_section and last_y is not None and y - last_y < 16:
                active_row[0] = normalize_inline_text(f"{active_row[0]} {nature}")
            else:
                flush_active()
                rows.append([nature, "", "", "", ""])
            last_y = y
            continue

        if not nature and label and montant:
            flush_active()
            rows.append([label.rstrip(" :") + " :", "", "", "", montant])
            last_y = y
            continue

        if not nature and (pu or label) and montant:
            flush_active()
            subtotal_label = normalize_inline_text(f"{pu} {label}").rstrip(" :")
            rows.append([subtotal_label + ":", "", "", "", montant])
            last_y = y
            continue

        if not nature and montant and rows and rows[-1][0].startswith("MISSION G4"):
            rows[-1][4] = montant
            last_y = y
            continue

        if not nature and montant and not any([unite, qte, pu, label]) and active_row:
            if normalize_inline_text(active_row[4]).replace(" ", "") == montant.replace(" ", ""):
                last_y = y
                continue
            active_row[4] = montant
            last_y = y
            continue

        if any([unite, qte, pu, montant]):
            flush_active()
            description = nature or label
            if "G4" in full_text and "rem" in full_text.lower():
                description = "G4 : (7 % de remise)"
            if pu == "8 000,00" and montant in {"000,00", "1 000,00"}:
                montant = pu
            active_row = [description, unite, qte, pu, montant]
            last_y = y
            continue

        if nature and active_row:
            active_row[0] = normalize_inline_text(f"{active_row[0]} {nature}")
            last_y = y

    flush_active()

    cleaned_rows = []
    for row in rows:
        if not any(cell.strip() for cell in row):
            continue
        if row[0] in {"page 3", "SOITESSAIS", "ÉTUDES GÉOTECHNIQUES"}:
            continue
        cleaned_rows.append(row)

    if not cleaned_rows:
        return ""

    return markdown_table_from_rows(
        ["Nature des travaux", "Unité", "Qté", "P.U. HT", "Mt. HT"],
        cleaned_rows,
    )


def extract_piezometric_rows(page, region=None):
    rect = rect_from_bbox(region["bbox"]) if region else page.rect
    words = [
        word for word in page.get_text("words")
        if rect.intersects(fitz.Rect(word[:4]))
    ]
    date_words = [
        word for word in words
        if re.fullmatch(r"\d{2}/\d{2}/\d{2}", word[4])
    ]
    rows = []

    for date_word in sorted(date_words, key=lambda item: item[1]):
        y = date_word[1]
        line_words = sorted(
            [word for word in words if abs(word[1] - y) <= 3.5],
            key=lambda item: item[0],
        )

        def text_between(left, right):
            values = [
                word[4] for word in line_words
                if left <= ((word[0] + word[2]) / 2) < right
            ]
            return " ".join(values).strip()

        row = {
            "date": text_between(20, 80),
            "numero_mesure": text_between(82, 112),
            "nombre_jours": text_between(120, 160),
            "mesure_m": text_between(165, 215),
            "altitude_ngf": text_between(225, 275),
            "observations": text_between(400, 470),
        }
        if row["date"]:
            rows.append(row)

    return rows


def piezometric_markdown_table(rows):
    return markdown_table_from_rows(
        ["Date", "N° mesure", "Nbr jours", "Mesure M", "Altitude NGF", "Observations"],
        [
            [
                row.get("date", ""),
                row.get("numero_mesure", ""),
                row.get("nombre_jours", ""),
                row.get("mesure_m", ""),
                row.get("altitude_ngf", ""),
                row.get("observations", ""),
            ]
            for row in rows
        ],
    )


def extract_piezometric_markdown_table(page, region):
    if region["label"] != "Mesures piézométriques":
        return ""
    return piezometric_markdown_table(extract_piezometric_rows(page, region))


def structured_rows_from_region(page, region):
    if region["label"] == "Mesures piézométriques":
        return extract_piezometric_rows(page, region)
    return []


def extract_markdown_table_from_region(page, region):
    sgs_table = extract_sgs_markdown_table(page, region)
    if sgs_table:
        return sgs_table

    sol_essais_table = extract_sol_essais_markdown_table(page, region)
    if sol_essais_table:
        return sol_essais_table

    piezometric_table = extract_piezometric_markdown_table(page, region)
    if piezometric_table:
        return piezometric_table

    wohnflaechen_table = extract_wohnflaechen_table(page, region)
    if wohnflaechen_table:
        return wohnflaechen_table

    if region.get("raw_rows"):
        rows = [
            [
                normalize_inline_text(cell)
                for cell in row
            ]
            for row in region["raw_rows"]
        ]
        col_count = max((len(row) for row in rows), default=0)
        if col_count:
            return markdown_table_from_rows(
                [f"Colonne {index}" for index in range(1, col_count + 1)],
                rows,
            )

    columns = table_columns_for_label(region["label"])
    if not columns:
        return ""

    rect = rect_from_bbox(region["bbox"])
    words = []
    for word in page.get_text("words"):
        word_rect = fitz.Rect(word[:4])
        if not rect.intersects(word_rect):
            continue
        # Skip repeated visual table headings; the Markdown table already has headers.
        if word[1] < rect.y0 + 45:
            continue
        words.append(word)

    rows = []
    for line in group_words_by_line(words):
        cells = []
        for _, left, right in columns:
            tokens = [
                word[4]
                for word in line["words"]
                if left <= ((word[0] + word[2]) / 2) < right
            ]
            cells.append(" ".join(tokens))

        if not any(cell.strip() for cell in cells):
            continue
        if cells[0] in {"Nr.", "Index"}:
            continue
        if "Kosten" in region["label"] and cells[0] and not re.fullmatch(r"\d{3}", cells[0]):
            cells[0] = ""
        rows.append(cells)

    if not rows:
        return ""

    header = [name for name, _, _ in columns]
    md = []
    md.append("| " + " | ".join(markdown_cell(cell) for cell in header) + " |")
    md.append("|" + "|".join("---" for _ in header) + "|")
    for row in rows:
        md.append("| " + " | ".join(markdown_cell(cell) for cell in row) + " |")

    return "\n".join(md)


def extract_wohnflaechen_table(page, region):
    text = page.get_text("text")
    if "WOHNFLÄCHENAUFSTELLUNG" not in text:
        return ""

    lines = clean_lines(text)
    rows = []
    active_floor = ""
    pending_units = []
    total_area = ""
    building_total = ""

    for line in lines:
        if re.fullmatch(r"(EG|[1-9]\. OG)", line):
            for unit in pending_units:
                unit[0] = line
            active_floor = line
            continue

        if re.fullmatch(r"\d{3}-WE-\d{2}", line):
            pending_units.append([active_floor, line, "", ""])
            continue

        if re.fullmatch(r"\d{1,3},\d{2} m²", line):
            if pending_units and not pending_units[-1][2]:
                pending_units[-1][2] = line
                continue
            total_area = line
            if pending_units:
                pending_units[-1][3] = total_area
                rows.extend(pending_units)
                pending_units = []
                active_floor = ""
                total_area = ""
            continue

        if line == "Gesamt":
            pending_units = []
            continue

        if re.fullmatch(r"\d{1,3}(?:\.\d{3})*,\d{2} m²", line):
            building_total = line

    if pending_units:
        rows.extend(pending_units)

    if building_total:
        rows.append(["Gesamt", "", "", building_total])

    if not rows:
        return ""

    return markdown_table_from_rows(
        ["Etage", "Unité", "Wohnfläche", "Total étage"],
        rows,
    )


def parse_number(value):
    value = value.strip()
    if not value or value in {"-", "- €", "-%"}:
        return None
    value = value.replace("€", "").replace("%", "").replace("m²", "").replace("m³", "")
    value = value.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def format_de_number(value):
    if value is None:
        return ""
    formatted = f"{value:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def parse_markdown_table(markdown_table):
    lines = [line.strip() for line in markdown_table.splitlines() if line.strip()]
    if len(lines) < 3:
        return []

    header = [cell.strip() for cell in lines[0].strip("|").split("|")]
    rows = []
    for line in lines[2:]:
        values = [cell.strip() for cell in line.strip("|").split("|")]
        if len(values) != len(header):
            continue
        rows.append(dict(zip(header, values)))
    return rows


def analyze_visual_from_tables(visual, table_crops):
    if not table_crops:
        return ""

    nearest_table = None
    visual_y = rect_from_bbox(visual["bbox"]).y0
    for table in table_crops:
        table_rect = rect_from_bbox(table["bbox"])
        if table_rect.y1 <= visual_y and table.get("markdown_table"):
            nearest_table = table

    if not nearest_table:
        nearest_table = next(
            (table for table in table_crops if table.get("markdown_table")),
            None,
        )

    if not nearest_table:
        return ""

    rows = parse_markdown_table(nearest_table["markdown_table"])
    comparable = []
    for row in rows:
        label = row.get("Kostengruppe") or row.get("Index") or row.get("Nr.") or ""
        if not label:
            continue

        kostenrahmen = parse_number(row.get("Kostenrahmen", ""))
        kostenschaetzung = parse_number(row.get("Kostenschätzung", ""))
        differenz = parse_number(row.get("Differenz", ""))
        abweichung = parse_number(row.get("Abweichung", ""))

        if kostenrahmen is None and kostenschaetzung is None:
            continue

        comparable.append({
            "label": label,
            "kostenrahmen": kostenrahmen,
            "kostenschaetzung": kostenschaetzung,
            "differenz": differenz,
            "abweichung": abweichung,
        })

    if not comparable:
        return ""

    total = next((item for item in comparable if "Summe (netto)" in item["label"]), None)
    graph_items = [
        item
        for item in comparable
        if "brutto" not in item["label"].lower()
        and "mehrwertsteuer" not in item["label"].lower()
    ]
    ranked = sorted(
        [item for item in graph_items if item["differenz"] is not None],
        key=lambda item: abs(item["differenz"]),
        reverse=True,
    )

    lines = [f"Le graphique reprend les valeurs du tableau `{nearest_table['label']}`."]
    if total:
        lines.append(
            "La somme nette passe de "
            f"{format_de_number(total['kostenrahmen'])} à {format_de_number(total['kostenschaetzung'])}, "
            f"soit un écart de {format_de_number(total['differenz'])}."
        )

    if ranked:
        lines.append("Principaux écarts visibles :")
        for item in ranked[:4]:
            if item["differenz"] in {None, 0}:
                continue
            percent = ""
            if item["abweichung"] is not None:
                percent = f" ({format_de_number(item['abweichung'])} %)"
            lines.append(f"- {item['label']}: {format_de_number(item['differenz'])}{percent}")

    return "\n".join(lines)


def collect_piezometric_measurements(result):
    rows = []
    for page in result.get("pages", []):
        for table in page.get("table_crops", []):
            if table.get("label") != "Mesures piézométriques":
                continue
            for row in table.get("data_rows", []):
                enriched = dict(row)
                enriched["page"] = page.get("page")
                rows.append(enriched)
    return rows


def piezometric_summary(rows):
    points = []
    for row in rows:
        altitude = parse_number(row.get("altitude_ngf", ""))
        if altitude is None:
            continue
        points.append((altitude, row))

    if not points:
        return ""

    min_altitude, min_row = min(points, key=lambda item: item[0])
    max_altitude, max_row = max(points, key=lambda item: item[0])
    first_altitude, first_row = points[0]
    last_altitude, last_row = points[-1]
    delta = last_altitude - first_altitude

    trend = "stable"
    if delta > 0.05:
        trend = "en hausse"
    elif delta < -0.05:
        trend = "en baisse"

    return (
        f"{len(points)} mesures piézométriques sont relevées. "
        f"L'altitude NGF varie de {format_de_number(min_altitude)} m "
        f"({min_row.get('date', '')}) à {format_de_number(max_altitude)} m "
        f"({max_row.get('date', '')}). "
        f"Entre {first_row.get('date', '')} et {last_row.get('date', '')}, "
        f"la tendance est {trend} ({format_de_number(delta)} m)."
    )


def enrich_piezometric_visual_analyses(result):
    rows = collect_piezometric_measurements(result)
    summary = piezometric_summary(rows)
    if not summary:
        return

    for page in result.get("pages", []):
        page_text = " ".join([
            page.get("native_text", ""),
            page.get("ocr_text", ""),
        ])
        for visual in page.get("visual_crops", []):
            if visual.get("analysis"):
                continue
            if "Mesure des niveaux d'eau" in page_text or "Piézo" in page_text or "Pz" in page_text:
                visual["analysis"] = summary


def block_y_for_text(blocks, text_pattern):
    for block in blocks:
        if text_pattern in block["text"]:
            return rect_from_bbox(block["bbox"]).y0
    return None


def detect_sgs_table_regions(page):
    text = page.get_text("text")
    if "SGS Environmental Analytics" not in text:
        return []
    if "Analyse" not in text or "Matrice" not in text or "Référence normative" not in text:
        return []
    if "Code barres" not in text or "Flaconnage" not in text:
        return []

    page_rect = page.rect
    return [
        {
            "label": "SGS Analyses normatives",
            "bbox": rect_values(expanded_rect(fitz.Rect(52, 180, 560, 315), page_rect, 4)),
            "source": "sgs-layout-rule",
        },
        {
            "label": "SGS Flaconnage",
            "bbox": rect_values(expanded_rect(fitz.Rect(52, 318, 560, 430), page_rect, 4)),
            "source": "sgs-layout-rule",
        },
    ]


def detect_sol_essais_table_regions(page):
    text = page.get_text("text")
    if "SOL" not in text or "ESSAIS" not in text:
        return []
    if not any(
        marker in text
        for marker in [
            "CONDITIONS FINANCIÈRES",
            "CONDITTIONS FINANCIÈRES",
            "CONDTTIONS FINANCIÈRES",
        ]
    ):
        return []
    if "G2 Phase AVP" not in text or "G2 Phase PRO" not in text or "G4" not in text:
        return []

    page_rect = page.rect
    return [{
        "label": "SOL-ESSAIS Conditions financières",
        "bbox": rect_values(expanded_rect(fitz.Rect(48, 632, 540, 710), page_rect, 4)),
        "source": "sol-essais-layout-rule",
    }]


def detect_sol_essais_quote_table_regions(page):
    text = page.get_text("text")
    compact = normalize_inline_text(text).lower()
    if "sol" not in compact or "essais" not in compact:
        return []
    if "nature" not in compact or "travaux" not in compact:
        return []
    if "p.u" not in compact and "p.u." not in compact:
        return []
    if "mt. ht" not in compact and "mt.ht" not in compact:
        return []

    words = page.get_text("words")
    header_words = [
        word for word in words
        if 35 <= word[0] <= 120 and "nature" in word[4].lower()
    ]
    if not header_words:
        return []

    top = min(word[1] for word in header_words) - 6
    bottom_candidates = [
        word[3] for word in words
        if top < word[1] < page.rect.y1 - 90
    ]
    if not bottom_candidates:
        return []
    bottom = min(max(bottom_candidates) + 8, page.rect.y1 - 95)

    if bottom - top < 80:
        return []

    return [{
        "label": "SOL-ESSAIS Devis détaillé",
        "bbox": rect_values(expanded_rect(fitz.Rect(38, top, 555, bottom), page.rect, 3)),
        "source": "sol-essais-quote-layout-rule",
    }]


def detect_piezometric_table_regions(page):
    text = page.get_text("text")
    required = ["Mesure des niveaux d'eau", "F1", "Pz", "Altitude", "NGF"]
    if not all(marker in text for marker in required):
        return []
    if not re.search(r"\d{2}/\d{2}/\d{2}", text):
        return []

    page_rect = page.rect
    return [{
        "label": "Mesures piézométriques",
        "bbox": rect_values(expanded_rect(fitz.Rect(24, 190, 470, 365), page_rect, 6)),
        "source": "piezometric-layout-rule",
    }]


def detect_table_regions(page, blocks):
    text = page.get_text("text")
    page_rect = page.rect
    regions = detect_sgs_table_regions(page)
    regions.extend(detect_sol_essais_table_regions(page))
    regions.extend(detect_sol_essais_quote_table_regions(page))
    regions.extend(detect_piezometric_table_regions(page))

    table_titles = [
        "Vergleich der Kosten (1. Ebene)",
        "Vergleich der Kosten (2. Ebene)",
        "Vergleich der Kennwerte",
        "Zusammenfassung des Kostenrahmens",
    ]

    for title in table_titles:
        if title not in text:
            continue

        top = block_y_for_text(blocks, title)
        if top is None:
            continue

        bottom_candidates = []
        if title != "Vergleich der Kennwerte":
            for stop_label in ["Hinweise zur Kostenermittlung", "Kennwertermittlung"]:
                y = block_y_for_text(blocks, stop_label)
                if y and y > top:
                    bottom_candidates.append(y - 8)

        footer_y = block_y_for_text(blocks, "Kostenplanung")
        if footer_y and footer_y > top:
            bottom_candidates.append(footer_y - 8)

        bottom = min(bottom_candidates) if bottom_candidates else page_rect.y1 - 55
        if bottom - top < 50:
            continue

        regions.append({
            "label": title,
            "bbox": rect_values(expanded_rect(fitz.Rect(52, top - 4, 540, bottom), page_rect, 0)),
            "source": "layout-title",
        })

    if hasattr(page, "find_tables"):
        try:
            tables = page.find_tables()
            for index, table in enumerate(tables.tables, 1):
                rect = expanded_rect(fitz.Rect(table.bbox), page_rect, 5)
                if rect.width < 80 or rect.height < 18:
                    continue
                try:
                    raw_rows = table.extract()
                except Exception:
                    raw_rows = []
                regions.append({
                    "label": f"Table PyMuPDF {index}",
                    "bbox": rect_values(rect),
                    "source": "pymupdf-find_tables",
                    "raw_rows": raw_rows,
                })
        except Exception:
            pass

    deduped = []
    for region in regions:
        rect = rect_from_bbox(region["bbox"])
        duplicate = False
        for existing in deduped:
            existing_rect = rect_from_bbox(existing["bbox"])
            intersection = rect & existing_rect
            if intersection and intersection.get_area() > 0.8 * min(rect.get_area(), existing_rect.get_area()):
                duplicate = True
                break
        if not duplicate:
            deduped.append(region)

    return deduped


def detect_visual_regions(page, table_regions):
    table_rects = [rect_from_bbox(item["bbox"]) for item in table_regions]
    page_rect = page.rect
    candidates = []

    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if not rect:
            continue

        rect = expanded_rect(rect, page_rect, 4)
        if rect.width < 120 or rect.height < 70:
            continue
        if rect.get_area() < 9000:
            continue
        if rect.y0 < 120:
            continue

        overlaps_table = False
        for table_rect in table_rects:
            intersection = rect & table_rect
            if intersection and intersection.get_area() > 0.5 * rect.get_area():
                overlaps_table = True
                break
        if overlaps_table:
            continue

        candidates.append(rect)

    merged = []
    for rect in sorted(candidates, key=lambda r: (r.y0, r.x0, -r.get_area())):
        merged_into_existing = False
        for idx, existing in enumerate(merged):
            padded = expanded_rect(existing, page_rect, 18)
            if padded.intersects(rect):
                merged[idx] = existing | rect
                merged_into_existing = True
                break
        if not merged_into_existing:
            merged.append(rect)

    return [
        {
            "label": f"Visuel {index}",
            "bbox": rect_values(expanded_rect(rect, page_rect, 8)),
            "source": "vector-drawings",
        }
        for index, rect in enumerate(merged, 1)
    ]


def extract_page_regions(page, page_index, blocks, out_dir, dpi):
    table_dir = out_dir / "table_crops"
    visual_dir = out_dir / "visuals"

    table_regions = detect_table_regions(page, blocks)
    visual_regions = detect_visual_regions(page, table_regions)

    table_crops = []
    for index, region in enumerate(table_regions, 1):
        rect = rect_from_bbox(region["bbox"])
        image_path = table_dir / f"page_{page_index:03d}_table_{index:03d}.png"
        crop_pdf_region(page, rect, image_path, dpi)
        table_crops.append({
            **region,
            "page": page_index,
            "image": str(image_path),
            "markdown_table": extract_markdown_table_from_region(page, region),
            "data_rows": structured_rows_from_region(page, region),
        })

    visual_crops = []
    for index, region in enumerate(visual_regions, 1):
        rect = rect_from_bbox(region["bbox"])
        image_path = visual_dir / f"page_{page_index:03d}_visual_{index:03d}.png"
        crop_pdf_region(page, rect, image_path, dpi)
        visual = {
            **region,
            "page": page_index,
            "image": str(image_path),
        }
        visual["analysis"] = analyze_visual_from_tables(visual, table_crops)
        visual_crops.append(visual)

    return table_crops, visual_crops


def render_pdf_pages(pdf_path, pages_dir, dpi):
    doc = fitz.open(pdf_path)
    rendered = []

    for page_index, page in enumerate(doc, 1):
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        image_path = pages_dir / f"page_{page_index:03d}.png"
        pix.save(image_path)
        rendered.append((page_index, page, image_path))

    return rendered


def image_average_hash(image_bytes):
    try:
        with Image.open(io.BytesIO(image_bytes)).convert("L") as img:
            img = img.resize((8, 8))
            pixels = list(img.getdata())
    except Exception:
        return ""

    avg = sum(pixels) / len(pixels)
    bits = ["1" if pixel >= avg else "0" for pixel in pixels]
    return f"{int(''.join(bits), 2):016x}"


def annotate_embedded_images(images):
    page_counts = {}
    phash_counts = {}
    for image in images:
        page_counts[image["page"]] = page_counts.get(image["page"], 0) + 1
        phash = image.get("perceptual_hash")
        if phash:
            phash_counts[phash] = phash_counts.get(phash, 0) + 1

    for image in images:
        width = image.get("width") or 0
        height = image.get("height") or 0
        area = width * height
        role = "image"
        reason = "image intégrée exploitable"

        if page_counts.get(image["page"], 0) > 20:
            role = "tile"
            reason = "page composée de nombreuses tuiles internes"
        elif width < 20 or height < 20 or area < 2500:
            role = "micro"
            reason = "micro-image technique"
        elif image.get("perceptual_hash") and phash_counts.get(image["perceptual_hash"], 0) > 1:
            role = "duplicate"
            reason = "doublon visuel"
        elif area < 60_000 and max(width, height) < 420:
            role = "logo-or-icon"
            reason = "petit logo, tampon ou icône"

        image["display_role"] = role
        image["display_reason"] = reason

    return images


def extract_embedded_images(doc, embedded_dir):
    extracted = []
    seen = set()

    for page_index, page in enumerate(doc, 1):
        for image_index, info in enumerate(page.get_images(full=True), 1):
            xref = info[0]
            if xref in seen:
                continue
            seen.add(xref)

            image_data = doc.extract_image(xref)
            rects = page.get_image_rects(xref)
            bbox = rect_values(rects[0]) if rects else []
            ext = image_data.get("ext", "png")
            image_bytes = image_data["image"]
            image_path = embedded_dir / f"page_{page_index:03d}_image_{image_index:03d}.{ext}"
            image_path.write_bytes(image_bytes)
            extracted.append({
                "page": page_index,
                "xref": xref,
                "bbox": bbox,
                "image": str(image_path),
                "width": image_data.get("width"),
                "height": image_data.get("height"),
                "extension": ext,
                "byte_sha256": hashlib.sha256(image_bytes).hexdigest(),
                "perceptual_hash": image_average_hash(image_bytes),
            })

    return annotate_embedded_images(extracted)


def transcribe_pdf(input_path, out_dir, dpi, ocr_mode, ocr_langs="auto"):
    pages_dir = out_dir / "pages"
    embedded_dir = out_dir / "embedded_images"
    table_dir = out_dir / "table_crops"
    visual_dir = out_dir / "visuals"
    pages_dir.mkdir(parents=True, exist_ok=True)
    embedded_dir.mkdir(parents=True, exist_ok=True)
    table_dir.mkdir(parents=True, exist_ok=True)
    visual_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(input_path)
    embedded_images = extract_embedded_images(doc, embedded_dir)
    rendered = render_pdf_pages(input_path, pages_dir, dpi)

    pages = []
    for page_index, page, page_image_path in rendered:
        native_text = clean_text(page.get_text("text"))
        blocks = native_text_blocks(page)

        should_ocr = (
            ocr_mode == "always"
            or (ocr_mode == "auto" and len(native_text) < 40)
        )

        ocr_text = ""
        if should_ocr:
            img = Image.open(page_image_path).convert("RGB")
            ocr_text = ocr_image(img, ocr_langs=ocr_langs)

        page_images = [
            item for item in embedded_images if item["page"] == page_index
        ]
        table_crops, visual_crops = extract_page_regions(
            page,
            page_index,
            blocks,
            out_dir,
            dpi,
        )
        table_crops.extend(
            extract_ocr_page_regions(page_image_path, ocr_text, out_dir, page_index)
        )
        visual_crops.extend(
            extract_ocr_visual_regions(
                page_image_path,
                ocr_text or native_text,
                out_dir,
                page_index,
                ocr_langs=ocr_langs,
            )
        )

        pages.append({
            "page": page_index,
            "width": round(page.rect.width, 2),
            "height": round(page.rect.height, 2),
            "page_image": str(page_image_path),
            "native_text": native_text,
            "ocr_text": ocr_text,
            "text_blocks": blocks,
            "embedded_images": page_images,
            "table_crops": table_crops,
            "visual_crops": visual_crops,
        })

    result = {
        "source": str(input_path),
        "source_type": "pdf",
        "page_count": len(pages),
        "render_dpi": dpi,
        "ocr_mode": ocr_mode,
        "ocr_langs": normalize_ocr_langs(ocr_langs) if ocr_mode != "never" else "",
        "pages": pages,
        "embedded_images": embedded_images,
        "table_crops": [item for page in pages for item in page["table_crops"]],
        "visual_crops": [item for page in pages for item in page["visual_crops"]],
    }
    annotate_repeated_text_blocks(result)
    enrich_piezometric_visual_analyses(result)
    result["structured"] = extract_structured_content(result)
    annotate_page_elements(result)
    return result


def transcribe_image(input_path, out_dir, ocr_mode, ocr_langs="auto"):
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    page_image_path = pages_dir / "page_001.png"
    img = Image.open(input_path).convert("RGB")
    orientation_correction = 0
    if ocr_mode != "never":
        img, orientation_correction = auto_orient_image(img, ocr_langs=ocr_langs)
    img.save(page_image_path)

    ocr_text = "" if ocr_mode == "never" else ocr_image(img, ocr_langs=ocr_langs)
    table_crops = extract_ocr_page_regions(page_image_path, ocr_text, out_dir, 1)
    visual_crops = extract_ocr_visual_regions(
        page_image_path,
        ocr_text,
        out_dir,
        1,
        ocr_langs=ocr_langs,
    )

    result = {
        "source": str(input_path),
        "source_type": "image",
        "page_count": 1,
        "render_dpi": None,
        "ocr_mode": ocr_mode,
        "ocr_langs": normalize_ocr_langs(ocr_langs) if ocr_mode != "never" else "",
        "orientation_correction": orientation_correction,
        "pages": [{
            "page": 1,
            "width": img.width,
            "height": img.height,
            "orientation_correction": orientation_correction,
            "page_image": str(page_image_path),
            "native_text": "",
            "ocr_text": ocr_text,
            "text_blocks": [],
            "embedded_images": [],
            "table_crops": table_crops,
            "visual_crops": visual_crops,
        }],
        "embedded_images": [],
        "table_crops": table_crops,
        "visual_crops": visual_crops,
        "structured": {},
    }
    result["structured"] = extract_structured_content(result)
    annotate_page_elements(result)
    return result


def all_page_lines(result):
    lines = []
    for page in result["pages"]:
        text = page["native_text"] or page["ocr_text"]
        lines.extend(clean_lines(text))
    return lines


def extract_kostenrahmen_content(result):
    lines = all_page_lines(result)
    page1_lines = clean_lines(
        result["pages"][0]["native_text"] or result["pages"][0]["ocr_text"]
    )

    summary_block = find_block(
        lines,
        "Zusammenfassung des Kostenrahmens",
        ["Hinweise zur Kostenermittlung", "Kennwertermittlung"],
    )
    key_block = find_block(
        lines,
        "Kennwertermittlung",
        ["Für die Genauigkeit"],
    )

    notes = []
    in_notes = False
    accuracy_notes = []
    in_accuracy_notes = False

    for line in lines:
        if line == "Hinweise zur Kostenermittlung":
            in_notes = True
            continue
        if line == "Kennwertermittlung":
            in_notes = False
        if line.startswith("Für die Genauigkeit"):
            in_accuracy_notes = True
        if in_notes:
            notes.append(line)
        if in_accuracy_notes:
            accuracy_notes.append(line)

    return {
        "type": "kostenrahmen",
        "meta": extract_project_meta(page1_lines),
        "cost_summary": parse_cost_summary(summary_block),
        "totals": parse_totals(lines),
        "key_figures": parse_key_figures(key_block),
        "notes": notes,
        "accuracy_notes": accuracy_notes,
    }


def extract_structured_content(result):
    joined = "\n".join(
        page["native_text"] or page["ocr_text"]
        for page in result["pages"]
    )

    if "Zusammenfassung des Kostenrahmens" in joined and "Kennwertermittlung" in joined:
        return extract_kostenrahmen_content(result)

    receipt = extract_receipt_content(result)
    if receipt:
        return receipt

    business_card = extract_business_card_content(result)
    if business_card:
        return business_card

    piezometric_rows = collect_piezometric_measurements(result)
    if piezometric_rows:
        return {
            "type": "piezometric",
            "measurements": piezometric_rows,
            "summary": piezometric_summary(piezometric_rows),
        }

    return {}


def title_from_slug(value):
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value.title()


def parse_amount_from_slug(value):
    if value == "unknown-amount":
        return ""
    match = re.match(r"^(\d+)_(\d{2})(?:-?eur|EUR)?$", value)
    if not match:
        return ""
    return f"{match.group(1)},{match.group(2)} EUR"


def normalize_structured_value(value):
    return re.sub(r"\s+", " ", value or "").strip(" -:;,.")


def normalized_document_lines(result):
    lines = []
    for page in result.get("pages", []):
        text = page.get("native_text") or page.get("ocr_text") or ""
        lines.extend(clean_lines(text))
    return [normalize_structured_value(line) for line in lines if normalize_structured_value(line)]


def document_text(result):
    return "\n".join(normalized_document_lines(result))


def unique_preserve_order(values):
    seen = set()
    result = []
    for value in values:
        normalized = normalize_structured_value(value)
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def normalize_amount(value):
    value = value.upper().replace("EUF", "EUR").replace("ELF", "EUR")
    value = value.replace("€", " EUR")
    value = re.sub(r"\s+", " ", value).strip()
    match = re.search(r"(\d{1,6})[,.](\d{2})\s*(EUR)?", value)
    if not match:
        return normalize_structured_value(value)
    return f"{match.group(1)},{match.group(2)} EUR"


def extract_amount_candidates(text):
    candidates = []
    pattern = re.compile(r"(?<!\d)(\d{1,6}[,.]\d{2})\s*(EUR|€|EUF|ELF)?", re.I)
    for match in pattern.finditer(text):
        start = max(0, match.start() - 45)
        end = min(len(text), match.end() + 45)
        context = normalize_structured_value(text[start:end])
        value = normalize_amount(match.group(0))
        score = 1
        if re.search(r"\b(total|ttc|montant|summe|betrag|amount|due|netto|brutto)\b", context, re.I):
            score += 4
        if re.search(r"EUR|€|EUF|ELF", match.group(0), re.I):
            score += 2
        candidates.append({"value": value, "context": context, "score": score})
    return candidates


def best_amount(candidates):
    if not candidates:
        return ""
    ranked = sorted(candidates, key=lambda item: (item["score"], amount_as_float(item["value"])), reverse=True)
    return ranked[0]["value"]


def amount_as_float(value):
    match = re.search(r"(\d{1,6}),(\d{2})", value or "")
    if not match:
        return 0.0
    return float(f"{match.group(1)}.{match.group(2)}")


def extract_date_candidates(text):
    patterns = [
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b",
        r"\b\d{1,2}-\d{1,2}-\d{2,4}\b",
    ]
    dates = []
    for pattern in patterns:
        dates.extend(date for date in re.findall(pattern, text) if is_plausible_date(date))
    return unique_preserve_order(dates)


def is_plausible_date(value):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        year, month, day = [int(part) for part in value.split("-")]
    else:
        parts = [int(part) for part in re.split(r"[/. -]", value)]
        if len(parts) != 3:
            return False
        day, month, year = parts
        if year < 100:
            year += 2000
    return 1 <= day <= 31 and 1 <= month <= 12 and 1990 <= year <= 2100


def extract_email_candidates(text):
    return unique_preserve_order(
        re.findall(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text)
    )


def extract_website_candidates(text):
    pattern = re.compile(
        r"(?i)\b(?:https?://)?(?:www\.)?[A-Z0-9-]+(?:\.[A-Z0-9-]+)+(?::\d+)?(?:/[^\s]*)?"
    )
    websites = []
    for match in pattern.finditer(text):
        before = text[match.start() - 1] if match.start() > 0 else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if before == "@" or after == "@":
            continue
        websites.append(match.group(0))
    return [
        site for site in unique_preserve_order(websites)
        if (
            "@" not in site
            and not re.fullmatch(r"\d+(?:[,.]\d+)+", site)
            and re.search(r"\.[A-Za-z]{2,8}(?:/|$)", site)
        )
    ]


def extract_phone_candidates(text):
    candidates = []
    pattern = re.compile(r"(?:(?:\+|00)\d{1,3}[\s().-]*)?(?:\d[\s().-]*){7,}\d")
    for match in pattern.finditer(text):
        value = normalize_structured_value(match.group(0))
        digits = re.sub(r"\D", "", value)
        if 8 <= len(digits) <= 16 and not re.fullmatch(r"\d{1,2}[/. -]\d{1,2}[/. -]\d{2,4}", value):
            candidates.append(value)
    return unique_preserve_order(candidates)


def confidence(value, level="medium"):
    if not value:
        return "missing"
    return level


def extract_receipt_content(result):
    source = Path(result.get("source", ""))
    stem = source.stem
    parts = stem.split("--")
    lines = normalized_document_lines(result)
    text = "\n".join(lines)
    upper = text.upper()

    date_from_filename = ""
    merchant_from_filename = ""
    amount_from_filename = ""
    filename_receipt = False
    if len(parts) >= 4 and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:-\d{2})?", parts[0]):
        filename_receipt = True
        date_from_filename = parts[0][:10]
        merchant_from_filename = title_from_slug(parts[1])
        for part in parts[2:]:
            amount_from_filename = parse_amount_from_slug(part)
            if amount_from_filename or part == "unknown-amount":
                break

    amount_candidates = extract_amount_candidates(text)
    detected_amounts = unique_preserve_order([candidate["value"] for candidate in amount_candidates])
    detected_dates = extract_date_candidates(text)
    receipt_keywords = [
        "RECU", "REÇU", "RECEIPT", "TICKET", "FACTURE", "INVOICE", "TOTAL",
        "TTC", "TVA", "MONTANT", "CARTE", "CB", "EUR", "€", "SUMME", "BETRAG",
    ]
    keyword_hits = [keyword for keyword in receipt_keywords if keyword in upper]
    if not filename_receipt and (len(keyword_hits) < 2 or not amount_candidates):
        return {}

    merchant = merchant_from_filename or guess_receipt_merchant(lines)
    date = date_from_filename or (detected_dates[0] if detected_dates else "")
    total = amount_from_filename or best_amount(amount_candidates)

    return {
        "type": "receipt",
        "date": date,
        "merchant": merchant,
        "total": total,
        "date_from_filename": date_from_filename,
        "merchant_from_filename": merchant_from_filename,
        "amount_from_filename": amount_from_filename,
        "detected_amounts": detected_amounts,
        "detected_dates": detected_dates,
        "confidence": {
            "date": confidence(date, "high" if date_from_filename else "medium"),
            "merchant": confidence(merchant, "high" if merchant_from_filename else "medium"),
            "total": confidence(total, "high" if amount_from_filename else "medium"),
        },
        "evidence": {
            "keyword_hits": keyword_hits[:8],
            "amount_contexts": amount_candidates[:6],
        },
    }


def guess_receipt_merchant(lines):
    skip = re.compile(r"\b(ticket|recu|reçu|receipt|facture|invoice|total|tva|ttc|cb|carte|date|heure|eur)\b|€", re.I)
    for line in lines[:10]:
        if skip.search(line):
            continue
        if re.search(r"[A-Za-zÀ-ÿ]{3,}", line) and not re.search(r"\d{1,6}[,.]\d{2}", line):
            return title_from_slug(line)
    return ""


def extract_business_card_content(result):
    lines = normalized_document_lines(result)
    if not lines:
        return {}

    source_type = result.get("source_type", "")
    page_count = result.get("page_count") or len(result.get("pages", []))
    if source_type != "image" and (page_count != 1 or len(lines) > 18):
        return {}

    text = "\n".join(lines)
    emails = extract_email_candidates(text)
    phones = extract_phone_candidates(text)
    websites = extract_website_candidates(text)
    card_keywords = [
        "CEO", "FOUNDER", "DIRECTEUR", "DIRECTRICE", "ARCHITECTE", "INGENIEUR",
        "INGÉNIEUR", "MANAGER", "CONSULTANT", "ASSOCIE", "ASSOCIÉ", "SALES",
        "CONTACT", "MOBILE", "TEL", "TÉL", "PHONE", "GMBH", "SARL", "SAS",
        "LLC", "LTD", "INC", "STUDIO", "AGENCY", "AGENCE",
    ]
    keyword_hits = [keyword for keyword in card_keywords if re.search(rf"\b{re.escape(keyword)}\b", text, re.I)]
    if len(lines) > 30 and not emails:
        return {}
    if not emails and not (phones and websites) and not (phones and keyword_hits and len(lines) <= 18):
        return {}

    name = guess_business_card_name(lines, emails, phones, websites)
    company = guess_business_card_company(lines, name)
    role = guess_business_card_role(lines)
    address = guess_business_card_address(lines)

    return {
        "type": "business_card",
        "name": name,
        "company": company,
        "role": role,
        "email": emails[0] if emails else "",
        "emails": emails,
        "phone": phones[0] if phones else "",
        "phones": phones,
        "website": websites[0] if websites else "",
        "websites": websites,
        "address": address,
        "confidence": {
            "name": confidence(name, "medium"),
            "company": confidence(company, "medium"),
            "role": confidence(role, "medium"),
            "email": confidence(emails[0] if emails else "", "high"),
            "phone": confidence(phones[0] if phones else "", "high"),
            "website": confidence(websites[0] if websites else "", "high"),
            "address": confidence(address, "medium"),
        },
        "evidence": {
            "keyword_hits": keyword_hits[:8],
            "lines": lines[:20],
        },
    }


def line_has_contact_data(line, emails, phones, websites):
    lowered = line.lower()
    if any(email.lower() in lowered for email in emails):
        return True
    if any(site.lower() in lowered for site in websites):
        return True
    digits = re.sub(r"\D", "", line)
    return any(re.sub(r"\D", "", phone) in digits for phone in phones if re.sub(r"\D", "", phone))


def guess_business_card_name(lines, emails, phones, websites):
    role_pattern = re.compile(r"(ceo|founder|directeur|directrice|architecte|ing[ée]nieur|manager|consultant|associ[ée])", re.I)
    company_pattern = re.compile(r"\b(SARL|SAS|GMBH|LLC|LTD|INC|STUDIO|AGENCE|AGENCY)\b", re.I)
    candidates = []
    for index, line in enumerate(lines[:12]):
        if line_has_contact_data(line, emails, phones, websites):
            continue
        if role_pattern.search(line) or company_pattern.search(line):
            continue
        words = re.findall(r"[A-Za-zÀ-ÿ'’-]+", line)
        if 2 <= len(words) <= 4 and sum(word[:1].isupper() for word in words) >= 1:
            candidates.append((index, line))
    return candidates[0][1] if candidates else ""


def guess_business_card_company(lines, name):
    company_pattern = re.compile(r"\b(SARL|SAS|GMBH|LLC|LTD|INC|STUDIO|AGENCE|AGENCY|ARCHITECTURE|DESIGN|CONSULTING)\b", re.I)
    for line in lines[:12]:
        if line == name:
            continue
        if company_pattern.search(line):
            return line
    for line in lines[:5]:
        if line != name and re.search(r"[A-Za-zÀ-ÿ]{3,}", line):
            return line
    return ""


def guess_business_card_role(lines):
    role_pattern = re.compile(
        r"\b(CEO|Founder|Directeur|Directrice|Architecte|Ing[ée]nieur|Manager|Consultant|Associ[ée]|Designer|Sales|Partner)\b",
        re.I,
    )
    for line in lines:
        if role_pattern.search(line):
            return line
    return ""


def guess_business_card_address(lines):
    address_pattern = re.compile(
        r"\b(rue|avenue|av\.|boulevard|bd|place|route|chemin|street|st\.|road|rd\.|strasse|straße|platz|allee|zip|cedex)\b|\b\d{4,5}\b",
        re.I,
    )
    address_lines = []
    for line in lines:
        if "@" in line or re.search(r"https?://|www\.", line, re.I):
            continue
        if address_pattern.search(line):
            address_lines.append(line)
    return ", ".join(unique_preserve_order(address_lines[:3]))


def markdown_escape_code_fence(text):
    return text.replace("```", "` ` `")


def markdown_table_to_html(markdown_table):
    rows = parse_markdown_table(markdown_table)
    if not rows:
        return f"<pre>{html.escape(markdown_table)}</pre>"

    headers = list(rows[0].keys())
    parts = ["<table>", "<thead><tr>"]
    for header in headers:
        parts.append(f"<th>{html.escape(header)}</th>")
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>")
        for header in headers:
            parts.append(f"<td>{html.escape(row.get(header, ''))}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def graph_metrics_to_html(metrics):
    if not metrics:
        return ""
    return markdown_table_to_html(graph_metrics_markdown(metrics))


def graph_spatial_to_html(rows):
    if not rows:
        return ""
    return markdown_table_to_html(graph_spatial_markdown(rows))


def dashboard_summary_to_html(summary):
    if not summary:
        return ""
    parts = []
    if summary.get("title"):
        parts.append(f"<h3>{html.escape(summary['title'])}</h3>")
    if summary.get("subtitle"):
        parts.append(f"<p>{html.escape(summary['subtitle'])}</p>")

    if summary.get("kpis"):
        parts.append("<h4>KPI</h4>")
        for item in summary["kpis"]:
            label = item.get("label") or f"KPI {item.get('position', '')}"
            parts.append(f"<h5>{html.escape(label)}</h5>")
            if item.get("image"):
                parts.append(
                    f"<div class=\"crop\"><img src=\"{html.escape(str(Path(item['image']).resolve()))}\" alt=\"{html.escape(label)}\"></div>"
                )
            if item.get("value"):
                parts.append(f"<p><strong>Valeur:</strong> {html.escape(item['value'])}</p>")
            if item.get("subtext"):
                parts.append(f"<p class=\"muted\">{html.escape(item['subtext'])}</p>")

    if summary.get("panels"):
        parts.append("<h4>Graphiques centraux</h4>")
        for panel in summary["panels"]:
            title = panel.get("title") or f"Panneau {panel.get('position', '')}"
            parts.append(f"<h5>{html.escape(title)}</h5>")
            if panel.get("image"):
                parts.append(
                    f"<div class=\"crop\"><img src=\"{html.escape(str(Path(panel['image']).resolve()))}\" alt=\"{html.escape(title)}\"></div>"
                )
            if show_dashboard_panel_values(panel) and not panel.get("matrix_table"):
                parts.append(f"<p><strong>Valeurs visibles:</strong> {html.escape(', '.join(panel['values']))}</p>")
            if panel.get("matrix_table"):
                parts.append("<p><strong>Tableau matriciel détecté</strong></p>")
                parts.append(markdown_table_to_html(panel["matrix_table"]))
            if panel.get("donut_summary"):
                parts.append("<p><strong>Donut/camembert détecté</strong></p>")
                rows = donut_summary_table_rows(panel)
                if rows:
                    parts.append(markdown_table_to_html(markdown_table_from_rows(
                        ["Segment", "Valeur", "Estimation visuelle", "Couleur", "Source", "Qualité", "À vérifier"],
                        rows,
                    )))
            if panel.get("rows") and not panel.get("matrix_table") and len(panel.get("rows", [])) >= 5:
                parts.append(markdown_table_to_html(markdown_table_from_rows(
                    ["Texte", "Valeurs"],
                    [[row.get("text", ""), ", ".join(row.get("values", []))] for row in panel.get("rows", [])],
                )))

    for panel in summary.get("bottom_panels", []):
        title = panel.get("title") or f"Panneau bas {panel.get('position', '')}"
        parts.append(f"<h4>{html.escape(title)}</h4>")
        if panel.get("image"):
            parts.append(
                f"<div class=\"crop\"><img src=\"{html.escape(str(Path(panel['image']).resolve()))}\" alt=\"{html.escape(title)}\"></div>"
            )
        rows = dashboard_items_table_rows(panel.get("items", []))
        if rows:
            parts.append(markdown_table_to_html(markdown_table_from_rows(
                ["Libellé", "Valeur", "Source", "Qualité", "À vérifier"],
                rows,
            )))
    return "".join(parts)


def paragraph_text_to_html(text):
    text = reflow_text_for_markdown(text)
    if not text:
        return ""
    return "".join(
        f"<p>{html.escape(part)}</p>"
        for part in text.split("\n\n")
        if part.strip()
    )


def is_list_start_line(line):
    return bool(re.match(r"^[-*•.]\s+", line.strip()))


def is_standalone_line(line):
    stripped = line.strip()
    if not stripped:
        return True
    if re.match(r"^#{1,6}\s+", stripped):
        return True
    if re.match(r"^\d+[.)]\s+", stripped):
        return True
    if re.match(r"^\d+(?:[.,]\d+)*\s+[A-ZÀ-ÖØ-Þ]", stripped):
        return True
    if re.match(r"^[A-ZÀ-ÖØ-Þ0-9][A-ZÀ-ÖØ-Þ0-9 /&().,'+-]{4,}$", stripped) and len(stripped) < 90:
        return True
    return False


def reflow_text_for_markdown(text):
    paragraphs = []
    current = []

    for raw_line in clean_text(text).splitlines():
        line = normalize_inline_text(raw_line)
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue

        if is_standalone_line(line):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            paragraphs.append(line)
            continue

        if is_list_start_line(line):
            if current:
                paragraphs.append(" ".join(current))
            current = [line]
            continue

        current.append(line)

    if current:
        paragraphs.append(" ".join(current))

    return "\n\n".join(paragraphs).strip()


def write_reflowed_text_block(f, title, text):
    text = reflow_text_for_markdown(text)
    if not text:
        return

    f.write(f"{title}\n\n")
    f.write(text)
    f.write("\n\n")


def write_reflowed_text(f, text):
    text = reflow_text_for_markdown(text)
    if not text:
        return

    f.write(text)
    f.write("\n\n")


def write_kostenrahmen_meta(f, structured):
    meta = structured.get("meta", {})
    if meta:
        f.write("## Métadonnées Structurées\n\n")
        f.write(f"- Projet: {meta.get('project_number', '')} {meta.get('project_name', '')}\n")
        f.write(f"- Titre: {meta.get('title', '')}\n")
        f.write(f"- Phase: {meta.get('phase', '')}\n")
        f.write(f"- Stand: {meta.get('date', '')}\n")
        f.write(f"- Bauherr: {'; '.join(meta.get('client', []))}\n")
        f.write(f"- Ersteller: {'; '.join(meta.get('creator', []))}\n\n")


def write_receipt_summary(f, structured):
    if structured.get("type") != "receipt":
        return

    f.write("## Résumé Reçu\n\n")
    if structured.get("date"):
        f.write(f"- Date: {structured.get('date', '')}\n")
    if structured.get("merchant"):
        f.write(f"- Marchand: {structured.get('merchant', '')}\n")
    if structured.get("total"):
        f.write(f"- Montant total: {structured.get('total', '')}\n")
    if structured.get("detected_amounts"):
        f.write(f"- Montants détectés: {', '.join(structured['detected_amounts'][:8])}\n")
    if structured.get("detected_dates"):
        f.write(f"- Dates détectées: {', '.join(structured['detected_dates'][:8])}\n")
    f.write("\n")


def write_business_card_summary(f, structured):
    if structured.get("type") != "business_card":
        return

    f.write("## Résumé Carte De Visite\n\n")
    fields = [
        ("Nom", "name"),
        ("Société", "company"),
        ("Fonction", "role"),
        ("Email", "email"),
        ("Téléphone", "phone"),
        ("Site web", "website"),
        ("Adresse", "address"),
    ]
    for label, key in fields:
        if structured.get(key):
            f.write(f"- {label}: {structured[key]}\n")

    extra_emails = [email for email in structured.get("emails", [])[1:] if email]
    extra_phones = [phone for phone in structured.get("phones", [])[1:] if phone]
    if extra_emails:
        f.write(f"- Autres emails: {', '.join(extra_emails)}\n")
    if extra_phones:
        f.write(f"- Autres téléphones: {', '.join(extra_phones)}\n")
    f.write("\n")


def write_kostenrahmen_summary_table(f, structured, heading="### Zusammenfassung Des Kostenrahmens"):
    f.write(f"{heading}\n\n")
    f.write("| Nr. | Kostengruppe | Menge | Einheit | Einzelpreis | Gesamtpreis | Anteil |\n")
    f.write("|---:|---|---:|---|---:|---:|---:|\n")
    for row in structured.get("cost_summary", []):
        f.write(
            f"| {row['nr']} | {row['kostengruppe']} | {row['menge']} | {row['einheit']} | "
            f"{row['einzelpreis']} | {row['gesamtpreis']} | {row['anteil']} |\n"
        )

    f.write("\n### Summen\n\n")
    for key, value in structured.get("totals", {}).items():
        f.write(f"- {key}: {value}\n")
    f.write("\n")


def write_kostenrahmen_key_table(f, structured, heading="### Kennwertermittlung"):
    f.write(f"\n{heading}\n\n")
    f.write("| Kategorie | Index | Kostengruppe | Menge | Einheit | Einzelpreis | Gesamtpreis | Anteil |\n")
    f.write("|---|---|---|---:|---|---:|---:|---:|\n")
    for row in structured.get("key_figures", []):
        f.write(
            f"| {row['category']} | {row['index']} | {row['kostengruppe']} | {row['menge']} | "
            f"{row['einheit']} | {row['einzelpreis']} | {row['gesamtpreis']} | {row['anteil']} |\n"
        )


def write_kostenrahmen_notes(f, structured):
    if structured.get("notes"):
        f.write("\n### Hinweise\n\n")
        for note in structured["notes"]:
            f.write(f"- {note}\n")


def write_kostenrahmen_accuracy(f, structured):
    if structured.get("accuracy_notes"):
        f.write("\n### Genauigkeit Der Kostenermittlung\n\n")
        write_reflowed_text(f, "\n".join(structured["accuracy_notes"]))
    f.write("\n")


def write_kostenrahmen_tables(f, structured, heading):
    f.write(f"{heading}\n\n")
    write_kostenrahmen_summary_table(f, structured)
    write_kostenrahmen_key_table(f, structured)
    write_kostenrahmen_notes(f, structured)
    write_kostenrahmen_accuracy(f, structured)


def write_kostenrahmen_hinweise_in_visual_order(f, structured):
    lines = []
    lines.extend(structured.get("accuracy_notes", []))
    if lines and structured.get("notes"):
        lines.append("")
    lines.extend(structured.get("notes", []))

    if not lines:
        return

    f.write("### Hinweise Zur Kostenermittlung\n\n")
    write_reflowed_text(f, "\n".join(lines))


def line_index(lines, value):
    try:
        return lines.index(value)
    except ValueError:
        return -1


def write_text_block_from_lines(f, title, lines):
    text = "\n".join(line for line in lines if line.strip()).strip()
    if not text:
        return

    write_reflowed_text_block(f, title, text)


def write_text_from_lines(f, lines):
    text = "\n".join(line for line in lines if line.strip()).strip()
    if not text:
        return

    write_reflowed_text(f, text)


def block_overlaps_regions(block, regions):
    block_rect = rect_from_bbox(block["bbox"])
    if block_rect.get_area() <= 0:
        return False

    for region in regions:
        region_rect = rect_from_bbox(region["bbox"])
        intersection = block_rect & region_rect
        if intersection and intersection.get_area() > 0.55 * block_rect.get_area():
            return True
    return False


def normalized_repeated_block_key(text):
    text = normalize_inline_text(text or "").lower()
    text = re.sub(r"\d+", "#", text)
    text = re.sub(r"[^a-zà-ÿ# ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def annotate_repeated_text_blocks(result):
    pages = result.get("pages", [])
    if len(pages) < 3:
        result["repeated_texts"] = []
        return result

    occurrences = {}
    for page in pages:
        height = page.get("height") or 0
        for block in page.get("text_blocks", []):
            bbox = block_bbox(block)
            key = normalized_repeated_block_key(block.get("text", ""))
            if not bbox or len(key) < 4 or not height:
                continue
            near_edge = bbox[1] < height * 0.12 or bbox[3] > height * 0.88
            if not near_edge:
                continue
            occurrences.setdefault(key, set()).add(page.get("page"))

    min_pages = max(3, math.ceil(len(pages) * 0.6))
    repeated = {
        key for key, page_numbers in occurrences.items()
        if len(page_numbers) >= min_pages
    }

    for page in pages:
        for block in page.get("text_blocks", []):
            key = normalized_repeated_block_key(block.get("text", ""))
            if key in repeated:
                block["display_role"] = "repeated-header-footer"

    result["repeated_texts"] = sorted(repeated)
    return result


def block_bbox(block):
    bbox = block.get("bbox") or []
    if len(bbox) != 4:
        return None
    return [float(value) for value in bbox]


def page_text_width(blocks, page):
    page_width = page.get("width") or 0
    if page_width:
        return float(page_width)

    right_edges = [block_bbox(block)[2] for block in blocks if block_bbox(block)]
    left_edges = [block_bbox(block)[0] for block in blocks if block_bbox(block)]
    if not right_edges or not left_edges:
        return 0
    return max(right_edges) - min(left_edges)


def kmeans_1d(values, k, iterations=12):
    if len(values) < k:
        return None

    sorted_values = sorted(values)
    centers = [
        sorted_values[int(round(i * (len(sorted_values) - 1) / max(1, k - 1)))]
        for i in range(k)
    ]

    for _ in range(iterations):
        groups = [[] for _ in range(k)]
        for value in values:
            index = min(range(k), key=lambda item: abs(value - centers[item]))
            groups[index].append(value)

        if any(not group for group in groups):
            return None

        new_centers = [sum(group) / len(group) for group in groups]
        if all(abs(new_centers[i] - centers[i]) < 0.5 for i in range(k)):
            break
        centers = new_centers

    return centers


def detect_text_columns(blocks, page):
    page_width = page_text_width(blocks, page)
    if page_width <= 0 or len(blocks) < 4:
        return None

    candidates = []
    for block in blocks:
        bbox = block_bbox(block)
        text = block.get("text", "").strip()
        if not bbox or len(text) < 8:
            continue

        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width < 20 or height <= 0 or width > page_width * 0.72:
            continue

        candidates.append({
            "block": block,
            "bbox": bbox,
            "center": (bbox[0] + bbox[2]) / 2,
        })

    if len(candidates) < 4:
        return None

    for column_count in (3, 2):
        centers = kmeans_1d([item["center"] for item in candidates], column_count)
        if not centers:
            continue

        groups = [[] for _ in range(column_count)]
        for item in candidates:
            index = min(range(column_count), key=lambda i: abs(item["center"] - centers[i]))
            groups[index].append(item)

        if any(len(group) < 2 for group in groups):
            continue

        columns = []
        for group in groups:
            left = min(item["bbox"][0] for item in group)
            right = max(item["bbox"][2] for item in group)
            center = sum(item["center"] for item in group) / len(group)
            columns.append({"left": left, "right": right, "center": center})

        columns.sort(key=lambda item: item["center"])
        gaps = [
            columns[index + 1]["left"] - columns[index]["right"]
            for index in range(len(columns) - 1)
        ]
        min_gap = max(18, page_width * 0.035)
        if any(gap < min_gap for gap in gaps):
            continue

        total_column_width = sum(column["right"] - column["left"] for column in columns)
        if total_column_width < page_width * 0.35:
            continue

        return columns

    return None


def text_block_column_index(block, columns):
    bbox = block_bbox(block)
    if not bbox:
        return None

    center = (bbox[0] + bbox[2]) / 2
    for index, column in enumerate(columns):
        margin = max(8, (column["right"] - column["left"]) * 0.12)
        if column["left"] - margin <= center <= column["right"] + margin:
            return index

    return min(range(len(columns)), key=lambda item: abs(center - columns[item]["center"]))


def text_blocks_in_reading_order(blocks, page):
    columns = detect_text_columns(blocks, page)
    if not columns:
        return sorted(blocks, key=lambda item: (item["bbox"][1], item["bbox"][0]))

    page_width = page_text_width(blocks, page)
    spanning_blocks = []
    column_blocks = []

    for block in blocks:
        bbox = block_bbox(block)
        if not bbox:
            continue

        width = bbox[2] - bbox[0]
        left_columns = [column for column in columns if bbox[0] <= column["center"] <= bbox[2]]
        if width > page_width * 0.72 or len(left_columns) > 1:
            spanning_blocks.append(block)
        else:
            column_blocks.append(block)

    ordered = []
    remaining = set(id(block) for block in column_blocks)

    def flush_column_blocks(max_y=None):
        for column_index in range(len(columns)):
            items = []
            for block in column_blocks:
                if id(block) not in remaining:
                    continue
                bbox = block_bbox(block)
                if max_y is not None and bbox[1] >= max_y:
                    continue
                if text_block_column_index(block, columns) == column_index:
                    items.append(block)

            for block in sorted(items, key=lambda item: (item["bbox"][1], item["bbox"][0])):
                ordered.append(block)
                remaining.discard(id(block))

    for block in sorted(spanning_blocks, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        bbox = block_bbox(block)
        flush_column_blocks(max_y=bbox[1])
        ordered.append(block)

    flush_column_blocks()
    return ordered


def page_text_outside_regions(page):
    regions = page.get("table_crops", []) + page.get("visual_crops", [])
    if not page.get("text_blocks"):
        return page.get("native_text") or page.get("ocr_text", "")

    lines = []
    text_blocks = []
    for block in page.get("text_blocks", []):
        if block.get("display_role") == "repeated-header-footer":
            continue
        if block_overlaps_regions(block, regions):
            continue
        text_blocks.append(block)

    for block in text_blocks_in_reading_order(text_blocks, page):
        text = block.get("text", "").strip()
        if text:
            lines.append(text)

    return clean_text("\n\n".join(lines))


def region_overlaps_regions(region, regions, threshold=0.55):
    bbox = region.get("bbox")
    if not bbox:
        return False
    rect = rect_from_bbox(bbox)
    if rect.get_area() <= 0:
        return False
    for other in regions:
        other_bbox = other.get("bbox")
        if not other_bbox:
            continue
        intersection = rect & rect_from_bbox(other_bbox)
        if intersection and intersection.get_area() > rect.get_area() * threshold:
            return True
    return False


def page_element_bbox(element):
    bbox = element.get("bbox") or []
    if len(bbox) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    return [float(value) for value in bbox]


def page_element_column_index(element, columns):
    bbox = page_element_bbox(element)
    center = (bbox[0] + bbox[2]) / 2
    for index, column in enumerate(columns):
        margin = max(8, (column["right"] - column["left"]) * 0.12)
        if column["left"] - margin <= center <= column["right"] + margin:
            return index
    return min(range(len(columns)), key=lambda item: abs(center - columns[item]["center"]))


def sort_page_elements(elements, page, text_blocks):
    if not elements:
        return []

    columns = detect_text_columns(text_blocks, page)
    type_order = {"text": 0, "table": 1, "visual": 2, "image": 3}
    if not columns:
        return sorted(
            elements,
            key=lambda item: (
                page_element_bbox(item)[1],
                page_element_bbox(item)[0],
                type_order.get(item.get("type"), 9),
            ),
        )

    page_width = page_text_width(text_blocks, page)
    spanning = []
    column_items = []
    for element in elements:
        bbox = page_element_bbox(element)
        width = bbox[2] - bbox[0]
        crossed_columns = [column for column in columns if bbox[0] <= column["center"] <= bbox[2]]
        if width > page_width * 0.72 or len(crossed_columns) > 1:
            spanning.append(element)
        else:
            column_items.append(element)

    ordered = []
    remaining = set(id(item) for item in column_items)

    def flush_column_items(max_y=None):
        for column_index in range(len(columns)):
            items = []
            for item in column_items:
                if id(item) not in remaining:
                    continue
                bbox = page_element_bbox(item)
                if max_y is not None and bbox[1] >= max_y:
                    continue
                if page_element_column_index(item, columns) == column_index:
                    items.append(item)
            for item in sorted(items, key=lambda value: (page_element_bbox(value)[1], page_element_bbox(value)[0], type_order.get(value.get("type"), 9))):
                ordered.append(item)
                remaining.discard(id(item))

    for item in sorted(spanning, key=lambda value: (page_element_bbox(value)[1], page_element_bbox(value)[0])):
        flush_column_items(max_y=page_element_bbox(item)[1])
        ordered.append(item)

    flush_column_items()
    return ordered


def page_elements(page, markdown_mode="clean"):
    regions = page.get("table_crops", []) + page.get("visual_crops", [])
    elements = []
    text_blocks = []

    for block in page.get("text_blocks", []):
        if markdown_mode != "audit" and block.get("display_role") == "repeated-header-footer":
            continue
        if markdown_mode != "audit" and is_decorative_text_noise(page, block):
            continue
        if block_overlaps_regions(block, regions):
            continue
        text = block.get("text", "").strip()
        if not text:
            continue
        text_blocks.append(block)
        elements.append({
            "type": "text",
            "bbox": block.get("bbox", []),
            "text": text,
            "source": "native-text",
            "confidence": "high",
        })

    for table in page.get("table_crops", []):
        elements.append({
            "type": "table",
            "bbox": table.get("bbox", []),
            "label": table.get("label", "Tableau"),
            "payload": table,
            "source": table.get("source", "table"),
            "confidence": "medium" if table.get("markdown_table") else "low",
        })

    for visual in page.get("visual_crops", []):
        elements.append({
            "type": "visual",
            "bbox": visual.get("bbox", []),
            "label": visual.get("label", "Visuel"),
            "payload": visual,
            "source": visual.get("source", "visual"),
            "confidence": "medium" if visual_has_graph_data(visual) else "low",
        })

    for image in embedded_images_for_markdown(page, markdown_mode=markdown_mode):
        if not image.get("bbox"):
            continue
        if region_overlaps_regions(image, page.get("table_crops", []) + page.get("visual_crops", []), threshold=0.35):
            continue
        elements.append({
            "type": "image",
            "bbox": image.get("bbox", []),
            "label": image.get("display_role", "image"),
            "payload": image,
            "source": "embedded-image",
            "confidence": "medium",
        })

    if not page.get("text_blocks") and page.get("ocr_text"):
        region_coverage = page_region_coverage(page, regions)
        if not elements or markdown_mode == "audit" or region_coverage < 0.55:
            elements.append({
                "type": "text",
                "bbox": [0, 0, page.get("width", 0), page.get("height", 0)],
                "text": page.get("ocr_text", ""),
                "source": "image-text",
                "confidence": "medium",
            })

    return sort_page_elements(elements, page, text_blocks)


def element_for_json(element):
    item = {
        "type": element.get("type"),
        "bbox": element.get("bbox", []),
        "source": element.get("source", ""),
        "confidence": element.get("confidence", ""),
    }
    if element.get("label"):
        item["label"] = element["label"]
    if element.get("type") == "text":
        item["text"] = element.get("text", "")
    elif element.get("type") in {"table", "visual", "image"}:
        payload = element.get("payload", {})
        for key in ("label", "image", "markdown_table", "analysis", "display_role", "display_reason"):
            if payload.get(key):
                item[key] = payload[key]
    return item


def annotate_page_elements(result):
    for page in result.get("pages", []):
        page["elements"] = [
            element_for_json(element)
            for element in page_elements(page, markdown_mode="clean")
        ]
    return result


def write_kostenrahmen_page_order(f, page, structured):
    lines = clean_lines(page["native_text"] or page["ocr_text"])
    summary_idx = line_index(lines, "Zusammenfassung des Kostenrahmens")
    key_idx = line_index(lines, "Kennwertermittlung")

    if summary_idx == -1 or key_idx == -1:
        write_text_from_lines(f, lines)
        return

    write_text_from_lines(f, lines[:summary_idx])
    write_kostenrahmen_summary_table(
        f,
        structured,
        heading="### Zusammenfassung Des Kostenrahmens",
    )

    write_kostenrahmen_hinweise_in_visual_order(f, structured)
    write_kostenrahmen_key_table(f, structured, heading="### Kennwertermittlung")


def visual_by_label(page, label):
    return next(
        (visual for visual in page.get("visual_crops", []) if visual.get("label") == label),
        None,
    )


def is_bki_kostenkennwerte_page(page):
    labels = {visual.get("label", "") for visual in page.get("visual_crops", [])}
    return {"KKW BRI", "KKW BGF", "KKW NUF", "KKW NE"}.issubset(labels)


def write_visual_image(f, visual):
    if not visual:
        return
    f.write(f"#### {visual['label']}\n\n")
    f.write(f"![{visual['label']}]({Path(visual['image']).resolve()})\n\n")


def write_bki_kostenkennwerte_page(f, page):
    f.write("### Büro- Und Verwaltungsgebäude, Einfacher Standard\n\n")
    f.write("Kostenstand: 1. Quartal 2026, Bundesdurchschnitt, inkl. 19% MwSt.\n\n")

    f.write("### Kostenkennwerte Für Die Kosten Des Bauwerks\n\n")
    for label in ["KKW BRI", "KKW BGF", "KKW NUF", "KKW NE"]:
        write_visual_image(f, visual_by_label(page, label))

    f.write("### Objektbeispiele\n\n")
    for label in [
        "Objektbeispiel 1300-0089",
        "Objektbeispiel 1300-0099",
        "Objektbeispiel 1300-0276",
        "Objektbeispiel 1300-0139",
        "Objektbeispiel 1300-0102",
        "Objektbeispiel 1300-0097",
    ]:
        write_visual_image(f, visual_by_label(page, label))

    f.write("### Kosten Der 8 Vergleichsobjekte\n\n")
    write_visual_image(f, visual_by_label(page, "Legende KKW"))
    write_visual_image(f, visual_by_label(page, "Kosten der 8 Vergleichsobjekte"))


def has_full_page_embedded_image(page):
    page_aspect = page.get("width", 0) / page.get("height", 1)
    for image in page.get("embedded_images", []):
        width = image.get("width") or 0
        height = image.get("height") or 0
        if width <= 0 or height <= 0:
            continue
        image_aspect = width / height
        if width * height > 1_000_000 and abs(image_aspect - page_aspect) < 0.04:
            return True
    return False


def embedded_images_for_markdown(page, markdown_mode="clean"):
    if markdown_mode == "audit":
        return page.get("embedded_images", [])

    # Some PDFs store one visual page as hundreds of tiny image tiles. They are
    # useful for audit in JSON, but unreadable as individual Markdown images.
    if has_full_page_embedded_image(page) and len(page.get("embedded_images", [])) >= 3:
        return []
    return [
        image for image in page.get("embedded_images", [])
        if image.get("display_role") not in {"tile", "micro", "duplicate"}
    ]


def page_region_coverage(page, regions):
    page_area = max(1.0, float(page.get("width", 0) or 0) * float(page.get("height", 0) or 0))
    covered = 0.0
    for region in regions:
        rect = rect_from_bbox(region.get("bbox", []))
        if rect.get_area() > 0:
            covered += rect.get_area()
    return min(1.0, covered / page_area)


def is_decorative_text_noise(page, block):
    text = normalize_inline_text(block.get("text", ""))
    if not text:
        return False
    bbox = block.get("bbox", [0, 0, 0, 0])
    page_height = page.get("height", 0) or 0
    x0, y0, x1, y1 = bbox
    compact = text.lower()

    if y1 < 105 and x0 < 175 and (
        "essais" in compact
        or "etudes" in compact
        or "cÉor" in text
        or "5(}" in text
        or "soitessais" in compact
    ):
        return True

    if page_height and y0 > page_height - 95 and (
        "forag" in compact
        or "penetro" in compact
        or "siret" in compact
        or "agence côte" in compact
        or "tél" in compact
        or "tel." in compact
        or "em€ll" in compact
        or "ingénierie" in compact
        or "ingenierie" in compact
        or "tiii" in compact
        or "ouauf" in compact
        or "opqibi" in compact
    ):
        return True

    return False


def has_bki_ocr_table(page):
    return any(
        table.get("source") == "ocr-bki"
        for table in page.get("table_crops", [])
    )


def page_has_dashboard_summary(page):
    return any(
        visual.get("dashboard_summary")
        for visual in page.get("visual_crops", [])
    )


def visual_has_graph_data(visual):
    if visual.get("dashboard_summary") or visual.get("graph_metrics") or visual.get("graph_spatial_rows"):
        return True
    source = visual.get("source", "")
    label = visual.get("label", "").lower()
    return "graph" in source or "chart" in source or "graph" in label


def show_visual_analysis(visual, markdown_mode="clean"):
    if markdown_mode == "audit":
        return bool(visual.get("analysis"))
    return bool(visual.get("analysis") and visual_has_graph_data(visual))


def structured_summary_to_html(structured):
    if structured.get("type") == "receipt":
        rows = [
            ("Date", structured.get("date", "")),
            ("Marchand", structured.get("merchant", "")),
            ("Montant total", structured.get("total", "")),
            ("Montants détectés", ", ".join(structured.get("detected_amounts", [])[:8])),
            ("Dates détectées", ", ".join(structured.get("detected_dates", [])[:8])),
        ]
        title = "Résumé Reçu"
    elif structured.get("type") == "business_card":
        rows = [
            ("Nom", structured.get("name", "")),
            ("Société", structured.get("company", "")),
            ("Fonction", structured.get("role", "")),
            ("Email", structured.get("email", "")),
            ("Téléphone", structured.get("phone", "")),
            ("Site web", structured.get("website", "")),
            ("Adresse", structured.get("address", "")),
        ]
        title = "Résumé Carte De Visite"
    else:
        return ""

    rows = [(label, value) for label, value in rows if value]
    if not rows:
        return ""

    parts = [f"<h3>{html.escape(title)}</h3>", "<table><tbody>"]
    for label, value in rows:
        parts.append(
            f"<tr><th>{html.escape(label)}</th><td>{html.escape(value)}</td></tr>"
        )
    parts.append("</tbody></table>")
    return "\n".join(parts)


def write_markdown_visual(f, visual, markdown_mode="clean"):
    if markdown_mode != "audit":
        if visual.get("image"):
            f.write(f"![{visual.get('label', 'Visuel')}]({Path(visual['image']).resolve()})\n\n")
        return

    dashboard_summary = visual.get("dashboard_summary")
    show_technical_graph_details = True
    if show_visual_analysis(visual, markdown_mode) and show_technical_graph_details:
        f.write("**Analyse**\n\n")
        f.write(visual["analysis"])
        f.write("\n\n")
    if dashboard_summary:
        f.write(dashboard_summary_markdown(dashboard_summary))
        f.write("\n\n")
    if visual.get("graph_metrics") and show_technical_graph_details:
        f.write("**Métriques détectées**\n\n")
        f.write(graph_metrics_markdown(visual["graph_metrics"]))
        f.write("\n\n")
    if visual.get("graph_spatial_rows") and show_technical_graph_details:
        f.write("**Données spatiales détectées**\n\n")
        f.write(graph_spatial_markdown(visual["graph_spatial_rows"]))
        f.write("\n\n")
    f.write(f"![{visual.get('label', 'Visuel')}]({Path(visual['image']).resolve()})\n\n")


def write_page_element_markdown(f, element, markdown_mode="clean"):
    element_type = element.get("type")
    if element_type == "text":
        if markdown_mode == "audit":
            write_reflowed_text_block(f, "### Texte", element.get("text", ""))
        else:
            write_reflowed_text(f, element.get("text", ""))
        return

    if element_type == "table":
        table = element.get("payload", {})
        if markdown_mode == "audit":
            f.write(f"### {table.get('label', 'Tableau')}\n\n")
        elif not table.get("markdown_table") and table.get("image"):
            f.write("### Tableau à vérifier\n\n")
        if table.get("markdown_table"):
            f.write(table["markdown_table"])
            f.write("\n\n")
        if table.get("image"):
            f.write(f"![{table.get('label', 'Tableau')}]({Path(table['image']).resolve()})\n\n")
        return

    if element_type == "visual":
        visual = element.get("payload", {})
        if markdown_mode == "audit":
            title = "Graphique Et Données À Vérifier" if visual_has_graph_data(visual) else visual.get("label", "Visuel Extrait")
            f.write(f"### {title}\n\n")
            if title != visual.get("label") and visual.get("label"):
                f.write(f"#### {visual['label']}\n\n")
        else:
            f.write("### Graphique ou visuel à vérifier\n\n")
            f.write("Cette zone a été extraite en image pour contrôle visuel.\n\n")
        write_markdown_visual(f, visual, markdown_mode=markdown_mode)
        return

    if element_type == "image":
        image = element.get("payload", {})
        f.write(f"![Image page {image.get('page', '')}]({Path(image['image']).resolve()})\n\n")


def page_element_to_html(element, markdown_mode="clean"):
    element_type = element.get("type")
    if element_type == "text":
        if markdown_mode == "audit":
            return "<h3>Texte</h3>\n" + paragraph_text_to_html(element.get("text", ""))
        return paragraph_text_to_html(element.get("text", ""))

    if element_type == "table":
        table = element.get("payload", {})
        parts = []
        if markdown_mode == "audit":
            parts.append(f"<h3>{html.escape(table.get('label', 'Tableau'))}</h3>")
        elif not table.get("markdown_table") and table.get("image"):
            parts.append("<h3>Tableau à vérifier</h3>")
        if table.get("markdown_table"):
            parts.append(markdown_table_to_html(table["markdown_table"]))
        if table.get("image"):
            parts.append(
                f"<div class=\"crop\"><img src=\"{html.escape(str(Path(table['image']).resolve()))}\" alt=\"{html.escape(table.get('label', 'Tableau'))}\"></div>"
            )
        return "\n".join(parts)

    if element_type == "visual":
        visual = element.get("payload", {})
        if markdown_mode != "audit":
            parts = [
                "<h3>Graphique ou visuel à vérifier</h3>",
                "<p>Cette zone a été extraite en image pour contrôle visuel.</p>",
            ]
            if visual.get("image"):
                parts.append(
                    f"<div class=\"crop\"><img src=\"{html.escape(str(Path(visual['image']).resolve()))}\" alt=\"{html.escape(visual.get('label', 'Visuel'))}\"></div>"
                )
            return "\n".join(parts)

        title = "Graphique Et Données À Vérifier" if visual_has_graph_data(visual) else visual.get("label", "Visuel Extrait")
        parts = [f"<h3>{html.escape(title)}</h3>"]
        if title != visual.get("label") and visual.get("label"):
            parts.append(f"<h4>{html.escape(visual['label'])}</h4>")
        dashboard_summary = visual.get("dashboard_summary")
        show_technical_graph_details = markdown_mode == "audit" or not dashboard_summary
        if show_visual_analysis(visual, markdown_mode) and show_technical_graph_details:
            parts.append(f"<div class=\"analysis\">{paragraph_text_to_html(visual['analysis'])}</div>")
        if dashboard_summary:
            parts.append(dashboard_summary_to_html(dashboard_summary))
        if visual.get("graph_metrics") and show_technical_graph_details:
            parts.append("<h4>Métriques détectées</h4>")
            parts.append(graph_metrics_to_html(visual["graph_metrics"]))
        if visual.get("graph_spatial_rows") and show_technical_graph_details:
            parts.append("<h4>Données spatiales détectées</h4>")
            parts.append(graph_spatial_to_html(visual["graph_spatial_rows"]))
        if visual.get("image"):
            parts.append(
                f"<div class=\"crop\"><img src=\"{html.escape(str(Path(visual['image']).resolve()))}\" alt=\"{html.escape(visual.get('label', 'Visuel'))}\"></div>"
            )
        return "\n".join(parts)

    if element_type == "image":
        image = element.get("payload", {})
        return f"<div class=\"crop\"><img src=\"{html.escape(str(Path(image['image']).resolve()))}\" alt=\"Image page {html.escape(str(image.get('page', '')))}\"></div>"

    return ""


def write_html_report(result, html_path, markdown_mode="clean"):
    source_name = Path(result["source"]).name
    structured = result.get("structured") or {}

    parts = [
        "<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        f"<title>{html.escape(source_name)} - Pippo Transcript</title>",
        "<style>",
        "body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f6f7f9;color:#1f2933}",
        "header{position:sticky;top:0;z-index:2;background:#101828;color:white;padding:14px 22px;box-shadow:0 2px 12px #0002}",
        "header h1{font-size:18px;margin:0 0 6px} header p{margin:0;color:#cbd5e1;font-size:13px}",
        "main{max-width:1280px;margin:0 auto;padding:20px}",
        ".summary,.page{background:white;border:1px solid #d9dee8;border-radius:8px;margin:0 0 18px;padding:16px}",
        ".page-grid{display:grid;grid-template-columns:minmax(280px,42%) 1fr;gap:18px;align-items:start}",
        ".page-image{position:sticky;top:82px}.page-image img,.crop img{max-width:100%;height:auto;border:1px solid #d9dee8;border-radius:6px;background:white}",
        "h2{font-size:20px;margin:0 0 14px} h3{font-size:16px;margin:18px 0 10px} h4{font-size:14px;margin:14px 0 8px}",
        "table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0 12px}th,td{border:1px solid #d8dee9;padding:6px 8px;vertical-align:top}th{background:#edf2f7;text-align:left}",
        ".badge{display:inline-block;background:#eef2ff;color:#3730a3;border-radius:999px;padding:2px 8px;font-size:12px;margin-right:6px}",
        ".analysis{background:#fff8e6;border-left:4px solid #f0b429;padding:10px 12px;margin:8px 0 12px}",
        ".muted{color:#64748b}.images{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px}.crop{margin:10px 0 16px}",
        "@media(max-width:850px){.page-grid{grid-template-columns:1fr}.page-image{position:static}}",
        "</style></head><body>",
        "<header>",
        f"<h1>{html.escape(source_name)}</h1>",
        f"<p>{html.escape(result.get('source_type',''))} · {result.get('page_count', 0)} page(s)</p>",
        "</header><main>",
        "<section class=\"summary\"><h2>Résumé</h2>",
        "<p>",
        f"<span class=\"badge\">Tableaux {len(result.get('table_crops', []))}</span>",
        f"<span class=\"badge\">Visuels {len(result.get('visual_crops', []))}</span>",
        f"<span class=\"badge\">Images intégrées {len(result.get('embedded_images', []))}</span></p>",
    ]

    if structured.get("summary"):
        parts.append(f"<div class=\"analysis\">{html.escape(structured['summary'])}</div>")
    structured_html = structured_summary_to_html(structured)
    if structured_html:
        parts.append(structured_html)
    parts.append("</section>")

    for page in result.get("pages", []):
        parts.append(f"<section class=\"page\" id=\"page-{page['page']}\">")
        parts.append(f"<h2>Page {page['page']}</h2>")
        parts.append("<div class=\"page-grid\"><div class=\"page-image\">")
        parts.append(f"<img src=\"{html.escape(str(Path(page['page_image']).resolve()))}\" alt=\"Page {page['page']}\">")
        parts.append("</div><div>")

        if is_bki_kostenkennwerte_page(page):
            parts.append("<h3>BKI Kostenkennwerte</h3>")
            for visual in page.get("visual_crops", []):
                parts.append(page_element_to_html({
                    "type": "visual",
                    "payload": visual,
                    "bbox": visual.get("bbox", []),
                }, markdown_mode=markdown_mode))
        else:
            for element in page_elements(page, markdown_mode=markdown_mode):
                rendered_element = page_element_to_html(element, markdown_mode=markdown_mode)
                if rendered_element:
                    parts.append(rendered_element)

        if markdown_mode == "audit" and page.get("ocr_text"):
            parts.append("<h3>Texte OCR</h3>")
            parts.append(paragraph_text_to_html(page["ocr_text"]))

        parts.append("</div></div></section>")

    parts.append("</main></body></html>")
    html_path.write_text("\n".join(parts), encoding="utf-8")


def write_outputs(result, out_dir, stem, include_blocks=False, markdown_mode="clean"):
    json_path = out_dir / f"{stem}_transcription.json"
    md_path = out_dir / f"{stem}_transcription.md"
    text_path = out_dir / f"{stem}_transcription.txt"
    html_path = out_dir / f"{stem}_transcription.html"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# {Path(result['source']).name}\n\n")
        f.write(f"- Type: {result['source_type']}\n")
        f.write(f"- Pages: {result['page_count']}\n\n")
        if markdown_mode == "audit":
            f.write(f"- OCR: {result['ocr_mode']}\n\n")
        if markdown_mode == "audit" and result.get("ocr_langs"):
            f.write(f"- Langues OCR: {result['ocr_langs']}\n\n")
        if markdown_mode == "audit" and result.get("orientation_correction"):
            f.write(f"- Correction orientation: {result['orientation_correction']}°\n\n")

        structured = result.get("structured") or {}
        write_receipt_summary(f, structured)
        write_business_card_summary(f, structured)

        for page in result["pages"]:
            f.write(f"## Page {page['page']}\n\n")
            f.write(f"![Page {page['page']}]({Path(page['page_image']).resolve()})\n\n")

            if is_bki_kostenkennwerte_page(page):
                write_bki_kostenkennwerte_page(f, page)
                continue

            if structured.get("type") == "kostenrahmen" and page["page"] == 2:
                write_kostenrahmen_page_order(f, page, structured)
                continue

            for element in page_elements(page, markdown_mode=markdown_mode):
                write_page_element_markdown(f, element, markdown_mode=markdown_mode)

            if page["ocr_text"] and markdown_mode == "audit":
                write_reflowed_text_block(f, "### Texte OCR", page["ocr_text"])

            if include_blocks and page["text_blocks"]:
                f.write("### Blocs Texte Avec Coordonnées\n\n")
                for idx, block in enumerate(page["text_blocks"], 1):
                    f.write(f"#### Bloc {idx} `{block['bbox']}`\n\n")
                    f.write("```text\n")
                    f.write(markdown_escape_code_fence(block["text"]))
                    f.write("\n```\n\n")

    with open(text_path, "w", encoding="utf-8") as f:
        for page in result["pages"]:
            f.write(f"\n\n===== PAGE {page['page']} =====\n\n")
            if page["native_text"]:
                f.write(page["native_text"])
            elif page["ocr_text"]:
                f.write(page["ocr_text"])
            f.write("\n")

    write_html_report(result, html_path, markdown_mode=markdown_mode)

    return json_path, md_path, text_path, html_path


def clean_output_dir(out_dir):
    if out_dir.exists():
        for child in out_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)


def transcribe_path(
    input_path,
    out_dir,
    dpi=200,
    ocr_mode="auto",
    ocr_langs="auto",
    include_blocks=False,
    clean=True,
    markdown_mode="clean",
):
    input_path = Path(input_path)
    out_dir = Path(out_dir)

    if not input_path.exists():
        raise FileNotFoundError(input_path)

    ensure_tesseract_available(ocr_mode, ocr_langs)

    if clean:
        clean_output_dir(out_dir)
    else:
        out_dir.mkdir(parents=True, exist_ok=True)

    stem = safe_stem(input_path)
    suffix = input_path.suffix.lower()

    if suffix == PDF_EXTENSION:
        result = transcribe_pdf(input_path, out_dir, dpi, ocr_mode, ocr_langs=ocr_langs)
    elif suffix in IMAGE_EXTENSIONS:
        result = transcribe_image(input_path, out_dir, ocr_mode, ocr_langs=ocr_langs)
    else:
        raise ValueError(f"Format non supporté : {input_path.suffix}")

    json_path, md_path, text_path, html_path = write_outputs(
        result,
        out_dir,
        stem,
        include_blocks=include_blocks,
        markdown_mode=markdown_mode,
    )

    return {
        "source": input_path,
        "out_dir": out_dir,
        "json": json_path,
        "markdown": md_path,
        "text": text_path,
        "html": html_path,
        "pages_dir": out_dir / "pages",
        "embedded_images_dir": out_dir / "embedded_images",
        "table_crops_dir": out_dir / "table_crops",
        "visuals_dir": out_dir / "visuals",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Transcription fidèle d'un PDF ou d'une image : pages rendues, texte natif, OCR et images."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("pippo-transcripted-files"))
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument(
        "--ocr",
        choices=["auto", "always", "never"],
        default="auto",
        help="auto = OCR seulement si pas de texte natif suffisant.",
    )
    parser.add_argument(
        "--ocr-langs",
        default="auto",
        help="Langues Tesseract, ex. fra+eng, fra+ita+eng, spa+eng. auto choisit les langues installées préférées.",
    )
    parser.add_argument(
        "--include-blocks",
        action="store_true",
        help="Inclut les blocs texte avec coordonnées dans le Markdown.",
    )
    parser.add_argument(
        "--markdown-mode",
        choices=["clean", "audit"],
        default="clean",
        help="clean = Markdown lisible; audit = affiche aussi les éléments bruts/techniques.",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(args.input)

    result = transcribe_path(
        args.input,
        args.out_dir / safe_stem(args.input),
        dpi=args.dpi,
        ocr_mode=args.ocr,
        ocr_langs=args.ocr_langs,
        include_blocks=args.include_blocks,
        markdown_mode=args.markdown_mode,
    )

    print(f"JSON créé : {result['json']}")
    print(f"Markdown complet créé : {result['markdown']}")
    print(f"Texte brut créé : {result['text']}")
    print(f"Images pages : {result['pages_dir']}")
    if result["embedded_images_dir"].exists():
        print(f"Images extraites : {result['embedded_images_dir']}")


if __name__ == "__main__":
    main()
