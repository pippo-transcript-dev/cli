from pathlib import Path

from pippo_transcript.core import (
    annotate_repeated_text_blocks,
    embedded_images_for_markdown,
    experimental_graph_analysis,
    extract_business_card_content,
    extract_receipt_content,
    markdown_table_from_rows,
    markdown_table_to_html,
    page_elements,
    page_text_outside_regions,
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
