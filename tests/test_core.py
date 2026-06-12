from pathlib import Path
from io import StringIO

from pippo_transcript.core import (
    annotate_repeated_text_blocks,
    embedded_images_for_markdown,
    experimental_graph_analysis,
    extract_business_card_content,
    extract_receipt_content,
    extract_structured_content,
    is_bki_document_text,
    markdown_table_from_rows,
    markdown_table_to_html,
    page_elements,
    page_text_outside_regions,
    write_page_element_markdown,
)


def test_markdown_cell_reflows_newlines_without_br():
    table = markdown_table_from_rows(["A"], [["ligne 1\nligne 2"]])
    assert "<br>" not in table
    assert "ligne 1 ligne 2" in table


def test_clean_markdown_hides_tiles():
    page = {
        "width": 100,
        "height": 100,
        "embedded_images": [
            {"display_role": "tile", "image": "tile.png"},
            {"display_role": "micro", "image": "micro.png"},
            {"display_role": "duplicate", "image": "duplicate.png"},
            {"display_role": "image", "image": "image.png"},
        ],
    }
    assert embedded_images_for_markdown(page, markdown_mode="clean") == [
        {"display_role": "image", "image": "image.png"}
    ]
    assert len(embedded_images_for_markdown(page, markdown_mode="audit")) == 4


def test_markdown_table_to_html():
    table = markdown_table_from_rows(["Date", "Valeur"], [["05/05/24", "2,92"]])
    rendered = markdown_table_to_html(table)
    assert "<table>" in rendered
    assert "05/05/24" in rendered
    assert "2,92" in rendered


def test_page_text_outside_regions_reads_two_columns_column_by_column():
    page = {
        "width": 600,
        "height": 800,
        "native_text": "",
        "table_crops": [],
        "visual_crops": [],
        "text_blocks": [
            {"bbox": [50, 100, 250, 120], "text": "Gauche 1 phrase longue"},
            {"bbox": [330, 100, 550, 120], "text": "Droite 1 phrase longue"},
            {"bbox": [50, 145, 250, 165], "text": "Gauche 2 phrase longue"},
            {"bbox": [330, 145, 550, 165], "text": "Droite 2 phrase longue"},
        ],
    }

    text = page_text_outside_regions(page)

    assert text.index("Gauche 1") < text.index("Gauche 2")
    assert text.index("Gauche 2") < text.index("Droite 1")
    assert text.index("Droite 1") < text.index("Droite 2")


def test_page_text_outside_regions_keeps_single_column_top_down_order():
    page = {
        "width": 600,
        "height": 800,
        "native_text": "",
        "table_crops": [],
        "visual_crops": [],
        "text_blocks": [
            {"bbox": [50, 100, 520, 120], "text": "Premier paragraphe"},
            {"bbox": [70, 145, 500, 165], "text": "Deuxieme paragraphe"},
            {"bbox": [50, 190, 520, 210], "text": "Troisieme paragraphe"},
        ],
    }

    text = page_text_outside_regions(page)

    assert text.index("Premier") < text.index("Deuxieme")
    assert text.index("Deuxieme") < text.index("Troisieme")


def test_page_elements_keep_table_between_text_blocks():
    page = {
        "width": 600,
        "height": 800,
        "text_blocks": [
            {"bbox": [50, 100, 550, 130], "text": "Texte avant tableau"},
            {"bbox": [50, 360, 550, 390], "text": "Texte après tableau"},
        ],
        "table_crops": [{
            "bbox": [50, 180, 550, 320],
            "label": "Tableau central",
            "image": "table.png",
            "markdown_table": "| A |\n|---|\n| 1 |",
        }],
        "visual_crops": [],
        "embedded_images": [],
    }

    elements = page_elements(page)

    assert [element["type"] for element in elements] == ["text", "table", "text"]
    assert elements[1]["label"] == "Tableau central"


def test_clean_page_elements_hide_full_page_ocr_when_visual_dominates():
    page = {
        "width": 800,
        "height": 500,
        "text_blocks": [],
        "ocr_text": "OCR bruité d'un dashboard graphique",
        "table_crops": [],
        "visual_crops": [{
            "bbox": [0, 0, 790, 490],
            "label": "Graphique expérimental",
            "image": "graph.png",
            "source": "experimental-graph-image",
        }],
        "embedded_images": [],
    }

    elements = page_elements(page, markdown_mode="clean")

    assert [element["type"] for element in elements] == ["visual"]


def test_clean_page_elements_hide_decorative_sol_essais_noise():
    page = {
        "width": 595,
        "height": 842,
        "text_blocks": [
            {"bbox": [30, 60, 145, 90], "text": "SOITESSAIS\nËruDFs cÉorrcHNrourE"},
            {"bbox": [45, 120, 250, 145], "text": "Projet de construction"},
            {"bbox": [50, 770, 550, 815], "text": "FORAGËS - PEiIETROMETRES - SIRET444"},
        ],
        "ocr_text": "",
        "table_crops": [],
        "visual_crops": [],
        "embedded_images": [],
    }

    elements = page_elements(page, markdown_mode="clean")

    assert [element["text"] for element in elements if element["type"] == "text"] == [
        "Projet de construction"
    ]


def test_clean_markdown_writes_text_without_technical_heading():
    buffer = StringIO()

    write_page_element_markdown(
        buffer,
        {"type": "text", "text": "Une phrase\nqui continue sur la ligne suivante."},
        markdown_mode="clean",
    )

    rendered = buffer.getvalue()
    assert "### Texte" not in rendered
    assert "Une phrase qui continue" in rendered


def test_clean_markdown_keeps_visual_as_crop_without_analysis(tmp_path):
    image_path = tmp_path / "graph.png"
    image_path.write_bytes(b"fake")
    buffer = StringIO()

    write_page_element_markdown(
        buffer,
        {
            "type": "visual",
            "payload": {
                "label": "Graphique expérimental",
                "image": str(image_path),
                "analysis": "Analyse graphique expérimentale niveau 2",
                "graph_metrics": [{"label": "A", "value": "1"}],
            },
        },
        markdown_mode="clean",
    )

    rendered = buffer.getvalue()
    assert "Graphique ou visuel à vérifier" in rendered
    assert "Analyse graphique expérimentale" not in rendered
    assert "Métriques détectées" not in rendered
    assert str(image_path.resolve()) in rendered


def test_clean_markdown_writes_table_without_label_when_parsed():
    buffer = StringIO()

    write_page_element_markdown(
        buffer,
        {
            "type": "table",
            "payload": {
                "label": "Tableau 1",
                "markdown_table": "| A |\n|---|\n| 1 |",
            },
        },
        markdown_mode="clean",
    )

    rendered = buffer.getvalue()
    assert "### Tableau" not in rendered
    assert "| A |" in rendered


def test_repeated_header_footer_blocks_are_hidden_from_clean_elements():
    result = {
        "pages": [
            {
                "page": index,
                "width": 600,
                "height": 800,
                "text_blocks": [
                    {"bbox": [50, 20, 550, 40], "text": "Rapport confidentiel 2026"},
                    {"bbox": [50, 120, 550, 150], "text": f"Contenu page {index}"},
                ],
                "table_crops": [],
                "visual_crops": [],
                "embedded_images": [],
            }
            for index in range(1, 4)
        ]
    }

    annotate_repeated_text_blocks(result)
    elements = page_elements(result["pages"][0])

    assert [element["text"] for element in elements if element["type"] == "text"] == ["Contenu page 1"]


def test_extract_receipt_content_from_text_without_filename_pattern():
    result = {
        "source": "receipt.jpg",
        "pages": [{
            "ocr_text": "\n".join([
                "TOTALENERGIES RELAIS NICE",
                "Ticket CB",
                "Date 01/05/2026",
                "TOTAL TTC 60,18 EUR",
            ])
        }],
    }

    structured = extract_receipt_content(result)

    assert structured["type"] == "receipt"
    assert structured["merchant"] == "Totalenergies Relais Nice"
    assert structured["total"] == "60,18 EUR"
    assert "01/05/2026" in structured["detected_dates"]


def test_bki_document_is_not_classified_as_receipt():
    result = {
        "source": "T1-2026_1z.png",
        "source_type": "image",
        "page_count": 1,
        "pages": [{
            "native_text": "",
            "ocr_text": "\n".join([
                "Büro- und Verwaltungsgebäude",
                "Kostenkennwerte für die Kosten des Bauwerks",
                "Kostengruppen 300+400 nach DIN 276",
                "Baukosteninformationszentrum",
                "von 1.500€/m2 bis 3.240€/m2",
            ]),
        }],
    }

    assert is_bki_document_text(result["pages"][0]["ocr_text"])
    assert extract_receipt_content(result) == {}
    assert extract_structured_content(result) == {}

    assert is_bki_document_text(
        "LB 008 Wasserhaltungsarbeiten Kostenstand:1.Quartal 2026 "
        "Nr. Positionen Einheit brutto € netto € © BKI Baukosteninfomationszentrum"
    )


def test_extract_business_card_content():
    result = {
        "source": "card.jpg",
        "pages": [{
            "ocr_text": "\n".join([
                "Marie Dupont",
                "Studio Atlas Architecture",
                "Architecte Associée",
                "marie.dupont@studio-atlas.fr",
                "+33 6 12 34 56 78",
                "www.studio-atlas.fr",
                "12 rue Victor Hugo, 06000 Nice",
            ])
        }],
    }

    structured = extract_business_card_content(result)

    assert structured["type"] == "business_card"
    assert structured["name"] == "Marie Dupont"
    assert structured["company"] == "Studio Atlas Architecture"
    assert structured["email"] == "marie.dupont@studio-atlas.fr"
    assert structured["phone"] == "+33 6 12 34 56 78"
    assert structured["website"] == "www.studio-atlas.fr"


def test_extract_business_card_rejects_long_document_with_contact_footer():
    result = {
        "source": "plan.pdf",
        "source_type": "pdf",
        "page_count": 1,
        "pages": [{
            "ocr_text": "\n".join(
                ["LEGENDE BAUARTEN", "Mauerwerk", "Wandhydrant mit Brandmelder"]
                + [f"Ligne technique {index}" for index in range(25)]
                + ["post@kollendtwaldschmidt.de", "+49 30 8179 7710"]
            )
        }],
    }

    assert extract_business_card_content(result) == {}


def test_experimental_graph_analysis_detects_basic_chart(tmp_path):
    from PIL import Image, ImageDraw

    image_path = Path(tmp_path) / "chart.png"
    img = Image.new("RGB", (320, 220), "white")
    draw = ImageDraw.Draw(img)
    draw.line((40, 180, 300, 180), fill="black", width=2)
    draw.line((40, 30, 40, 180), fill="black", width=2)
    draw.line((45, 160, 120, 120, 200, 130, 280, 60), fill="red", width=4)
    img.save(image_path)

    analysis, features, metrics, graph_types, spatial_rows, dashboard_summary = experimental_graph_analysis(
        image_path, "Graphique niveau NGF 2,92 3,61"
    )
    assert "niveau 2" in analysis
    assert features["color_series_estimate"] >= 1
    assert metrics
    assert graph_types
    assert isinstance(spatial_rows, list)
    assert isinstance(dashboard_summary, dict)
