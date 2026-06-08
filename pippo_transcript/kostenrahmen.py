import argparse
import json
import re
from pathlib import Path

import fitz


def clean_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def normalize_money(text):
    text = text.strip()
    if text in {"-", "- €", "€"}:
        return "-"
    text = text.replace(" ", "")
    text = text.replace("€", "")
    return text


def normalize_percent(text):
    text = text.strip()
    if text in {"-%", "- %", "-"}:
        return "-"
    return text.replace(" ", "")


def render_pages(pdf_path, image_dir, dpi=160):
    doc = fitz.open(pdf_path)
    image_paths = []
    for index, page in enumerate(doc, 1):
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        path = image_dir / f"page_{index:03d}.png"
        pix.save(path)
        image_paths.append(str(path))
    return image_paths


def extract_project_meta(page1_lines):
    meta = {
        "document_type": "",
        "page_info": "",
        "project_number": "",
        "project_name": "",
        "phase": "",
        "title": "",
        "project": [],
        "client": [],
        "creator": [],
        "date": "",
        "contact": {},
    }

    if len(page1_lines) >= 2:
        meta["document_type"] = " / ".join(page1_lines[:2])

    for i, line in enumerate(page1_lines):
        if line.startswith("Seite "):
            meta["page_info"] = line
        elif re.fullmatch(r"\d{6}", line):
            meta["project_number"] = line
        elif i > 0 and page1_lines[i - 1] == meta["project_number"]:
            meta["project_name"] = line
        elif line.startswith("LPH"):
            meta["phase"] = line
        elif line.startswith("Kostenrahmen nach"):
            meta["title"] = line
        elif line.startswith("Stand "):
            meta["date"] = line.replace("Stand ", "")
        elif line.startswith("Tel.:"):
            meta["contact"]["tel"] = line.replace("Tel.:", "").strip()
        elif line.startswith("Fax:"):
            meta["contact"]["fax"] = line.replace("Fax:", "").strip()
        elif line.startswith("Web:"):
            meta["contact"]["web"] = line.replace("Web:", "").strip()
        elif line.startswith("Mail:"):
            meta["contact"]["mail"] = line.replace("Mail:", "").strip()

    sections = {
        "Projekt:": "project",
        "Bauherr:": "client",
        "Ersteller:": "creator",
    }

    current = None
    stop_labels = set(sections.keys()) | {"Prüfvermerke:"}

    for line in page1_lines:
        if line in sections:
            current = sections[line]
            continue
        if line in stop_labels:
            current = None
            continue
        if line.startswith(("Tel.:", "Fax:", "Web:", "Mail:", "Stand ")):
            continue
        if current:
            meta[current].append(line)

    return meta


def find_block(lines, start_label, end_labels):
    try:
        start = lines.index(start_label)
    except ValueError:
        return []

    end = len(lines)
    for label in end_labels:
        try:
            candidate = lines.index(label, start + 1)
        except ValueError:
            continue
        end = min(end, candidate)

    return lines[start:end]


def parse_cost_summary(block):
    rows = []
    i = 0
    while i < len(block):
        if re.fullmatch(r"\d{3}", block[i]):
            nr = block[i]
            name = block[i + 1] if i + 1 < len(block) else ""
            menge = block[i + 2] if i + 2 < len(block) else ""
            einheit = block[i + 3] if i + 3 < len(block) else ""
            einzelpreis = ""
            gesamtpreis = ""
            anteil = ""

            j = i + 4
            money_values = []
            while j < len(block) and not re.fullmatch(r"\d{3}", block[j]) and not block[j].startswith("Summe"):
                if "€" in block[j] or block[j] == "-":
                    money_values.append(block[j])
                if "%" in block[j]:
                    anteil = block[j]
                j += 1

            if len(money_values) >= 1:
                einzelpreis = money_values[0]
            if len(money_values) >= 2:
                gesamtpreis = money_values[1]
            if nr == "800" and einheit == "€":
                menge = ""
                einheit = ""
                einzelpreis = ""
                gesamtpreis = "-"

            rows.append({
                "nr": nr,
                "kostengruppe": name,
                "menge": menge,
                "einheit": einheit,
                "einzelpreis": normalize_money(einzelpreis),
                "gesamtpreis": normalize_money(gesamtpreis),
                "anteil": normalize_percent(anteil),
            })
            i = j
            continue
        i += 1
    return rows


def parse_totals(lines):
    totals = {}
    for i, line in enumerate(lines):
        if line in {"Summe (netto)", "19% Mehrwertsteuer", "Summe (brutto)"}:
            totals[line] = normalize_money(lines[i + 1]) if i + 1 < len(lines) else ""
    return totals


def parse_key_figures(block):
    rows = []
    current_category = ""
    i = 0
    while i < len(block):
        line = block[i]
        if line in {"Primärkennwerte", "Sekundärkennwerte"}:
            current_category = line
            i += 1
            continue

        if re.fullmatch(r"[A-ZÄÖÜ]{2,4}", line):
            index = line
            name = block[i + 1] if i + 1 < len(block) else ""
            menge = block[i + 2] if i + 2 < len(block) else ""
            einheit = block[i + 3] if i + 3 < len(block) else ""
            einzelpreis = ""
            gesamtpreis = ""
            anteil = ""
            note = ""

            j = i + 4
            money_values = []
            while j < len(block):
                value = block[j]
                if value.startswith("*"):
                    note = value
                    j += 1
                    break
                if value in {"Primärkennwerte", "Sekundärkennwerte"} or re.fullmatch(r"[A-ZÄÖÜ]{2,4}", value):
                    break
                if "€" in value or value == "-":
                    money_values.append(value)
                if "%" in value:
                    anteil = value
                j += 1

            if len(money_values) >= 1:
                einzelpreis = money_values[0]
            if len(money_values) >= 2:
                gesamtpreis = money_values[1]

            rows.append({
                "category": current_category,
                "index": index,
                "kostengruppe": name,
                "menge": menge,
                "einheit": einheit,
                "einzelpreis": normalize_money(einzelpreis),
                "gesamtpreis": normalize_money(gesamtpreis),
                "anteil": normalize_percent(anteil),
                "note": note,
            })
            i = j
            continue
        i += 1
    return rows


def write_outputs(result, out_base):
    json_path = out_base.with_name(out_base.name + "_kostenrahmen.json")
    md_path = out_base.with_name(out_base.name + "_kostenrahmen.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    with open(md_path, "w", encoding="utf-8") as f:
        meta = result["meta"]
        f.write(f"# {meta['project_number']} {meta['project_name']}\n\n")
        f.write(f"{meta['title']}\n\n")
        f.write(f"- Dokument: {meta['document_type']}\n")
        f.write(f"- Phase: {meta['phase']}\n")
        f.write(f"- Stand: {meta['date']}\n")
        f.write(f"- Projekt: {'; '.join(meta['project'])}\n")
        f.write(f"- Bauherr: {'; '.join(meta['client'])}\n")
        f.write(f"- Ersteller: {'; '.join(meta['creator'])}\n\n")

        f.write("## Zusammenfassung Des Kostenrahmens\n\n")
        f.write("| Nr. | Kostengruppe | Menge | Einheit | Einzelpreis | Gesamtpreis | Anteil |\n")
        f.write("|---:|---|---:|---|---:|---:|---:|\n")
        for row in result["cost_summary"]:
            f.write(
                f"| {row['nr']} | {row['kostengruppe']} | {row['menge']} | {row['einheit']} | "
                f"{row['einzelpreis']} | {row['gesamtpreis']} | {row['anteil']} |\n"
            )

        f.write("\n## Summen\n\n")
        for key, value in result["totals"].items():
            f.write(f"- {key}: {value}\n")

        f.write("\n## Kennwertermittlung\n\n")
        f.write("| Kategorie | Index | Kostengruppe | Menge | Einheit | Einzelpreis | Gesamtpreis | Anteil |\n")
        f.write("|---|---|---|---:|---|---:|---:|---:|\n")
        for row in result["key_figures"]:
            f.write(
                f"| {row['category']} | {row['index']} | {row['kostengruppe']} | {row['menge']} | "
                f"{row['einheit']} | {row['einzelpreis']} | {row['gesamtpreis']} | {row['anteil']} |\n"
            )

        f.write("\n## Hinweise\n\n")
        for note in result["notes"]:
            f.write(f"- {note}\n")

        f.write("\n## Volltext\n\n")
        for page in result["pages"]:
            f.write(f"### Seite {page['page']}\n\n")
            f.write("```text\n")
            f.write(page["text"])
            f.write("\n```\n\n")

        f.write("## Bilder\n\n")
        for image in result["images"]:
            f.write(f"- {image}\n")

    return json_path, md_path


def main():
    parser = argparse.ArgumentParser(description="Extrahit Kostenrahmen-PDFs avec texte natif.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("sortie-kostenrahmen"))
    args = parser.parse_args()

    out_dir = args.out_dir / args.pdf.stem
    image_dir = out_dir / "pages"
    image_dir.mkdir(parents=True, exist_ok=True)
    for old_image in image_dir.glob("*.png"):
        old_image.unlink()

    doc = fitz.open(args.pdf)
    pages = []
    for index, page in enumerate(doc, 1):
        text = page.get_text("text").strip()
        pages.append({
            "page": index,
            "text": text,
            "lines": clean_lines(text),
        })

    images = render_pages(args.pdf, image_dir)
    page1_lines = pages[0]["lines"] if pages else []
    all_lines = []
    for page in pages:
        all_lines.extend(page["lines"])

    summary_block = find_block(
        all_lines,
        "Zusammenfassung des Kostenrahmens",
        ["Hinweise zur Kostenermittlung", "Kennwertermittlung"],
    )
    key_block = find_block(
        all_lines,
        "Kennwertermittlung",
        ["Für die Genauigkeit"],
    )

    notes = []
    in_notes = False
    for line in all_lines:
        if line == "Hinweise zur Kostenermittlung":
            in_notes = True
            continue
        if line == "Kennwertermittlung":
            in_notes = False
        if in_notes:
            notes.append(line)

    result = {
        "source_pdf": str(args.pdf),
        "meta": extract_project_meta(page1_lines),
        "cost_summary": parse_cost_summary(summary_block),
        "totals": parse_totals(all_lines),
        "key_figures": parse_key_figures(key_block),
        "notes": notes,
        "pages": pages,
        "images": images,
    }

    json_path, md_path = write_outputs(result, out_dir / args.pdf.stem)
    print(f"JSON créé : {json_path}")
    print(f"Markdown créé : {md_path}")
    print(f"Images créées : {image_dir}")


if __name__ == "__main__":
    main()
