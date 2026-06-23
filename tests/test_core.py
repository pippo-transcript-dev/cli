from pathlib import Path
from io import StringIO

from pippo_transcript.core import (
    annotate_repeated_text_blocks,
    embedded_images_for_markdown,
    experimental_graph_analysis,
    extract_business_card_content,
    extract_receipt_content,
    extract_structured_content,
    filter_redundant_experimental_visuals,
    bki_compact_page_heading,
    bki_filter_redundant_tables,
    bki_markdown_table_from_ocr,
    bki_native_lifespan_table_from_blocks,
    bki_native_kostengruppen_table_from_blocks,
    bki_text_duplicates_table,
    is_bki_document_text,
    is_payroll_document_text,
    markdown_table_from_rows,
    markdown_table_from_raw_rows,
    markdown_table_without_separator,
    markdown_table_to_html,
    normalize_markdown_mode,
    page_elements,
    page_text_outside_regions,
    parse_bki_compact_price_items,
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


def test_markdown_table_to_html_renders_image_cells():
    table = markdown_table_from_rows(["Photo", "Description"], [["![Photo 1](/tmp/photo.png)", "Texte"]])
    rendered = markdown_table_to_html(table)
    assert '<img src="/tmp/photo.png"' in rendered
    assert "Texte" in rendered


def test_raw_rows_table_uses_real_header_when_available():
    table = markdown_table_from_raw_rows([
        ["Eléments de paie", "", "Base", "Taux", "A déduire", "A payer"],
        ["Salaire de base", "", "151.67", "26.0032", "", "3 943.90"],
    ])

    assert "Colonne" not in table
    assert "| Eléments de paie |" in table
    assert "Salaire de base" in table


def test_raw_rows_layout_text_is_not_rendered_as_table():
    table = markdown_table_from_raw_rows([
        ["On constate de plus que pour la quasi-majorité des organisations proposant des services", "", "", ""],
        ["en lien avec la blockchain, la formalisation de processus est absente.", "", "", ""],
        ["Cette phrase continue sur une autre ligne du paragraphe.", "", "", ""],
    ])

    assert table == ""


def test_raw_rows_key_value_table_without_header_is_kept():
    table = markdown_table_from_raw_rows([
        ["TRI (IRR)", "Taux de Rendement Interne sur les flux."],
        ["Cash-on-Cash", "Cash-flow net de l’année 1 ÷ cash investi initial."],
        ["MOIC", "Multiple On Invested Capital."],
    ])

    assert table.splitlines()[0] == "|  |  |"
    assert "TRI (IRR)" in table
    assert "Cash-on-Cash" in table


def test_clean_page_elements_hide_layout_tables_and_keep_text():
    page = {
        "width": 600,
        "height": 800,
        "text_blocks": [
            {"bbox": [50, 100, 550, 140], "text": "Paragraphe normal à conserver."},
        ],
        "table_crops": [{
            "bbox": [45, 95, 555, 145],
            "label": "Table PyMuPDF 1",
            "image": "layout.png",
            "display_role": "layout",
        }],
        "visual_crops": [],
        "embedded_images": [],
    }

    elements = page_elements(page)

    assert [element["type"] for element in elements] == ["text"]
    assert elements[0]["text"] == "Paragraphe normal à conserver."


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


def test_clean_page_elements_keep_ocr_when_document_crop_dominates():
    page = {
        "width": 800,
        "height": 1200,
        "text_blocks": [],
        "ocr_text": "SMASH CLUB\nTOTAL 87,50 €",
        "table_crops": [],
        "visual_crops": [{
            "bbox": [40, 30, 760, 1160],
            "label": "Document détecté",
            "image": "receipt.png",
            "source": "image-document-detection",
        }],
        "embedded_images": [],
    }

    elements = page_elements(page, markdown_mode="clean")

    assert [element["type"] for element in elements] == ["text", "visual"]
    assert elements[0]["text"] == "SMASH CLUB\nTOTAL 87,50 €"


def test_clean_page_elements_hide_decorative_sol_essais_noise():
    page = {
        "width": 595,
        "height": 842,
        "text_blocks": [
            {"bbox": [30, 60, 145, 90], "text": "SOITESSAIS\nËruDFs cÉorrcHNrourE"},
            {"bbox": [45, 120, 250, 145], "text": "Projet de construction"},
            {"bbox": [50, 770, 550, 815], "text": "FORAGËS - PEiIETROMETRES - SIRET444"},
            {"bbox": [50, 805, 550, 825], "text": "© BKI Baukosteninformationszentrum; Erläuterungen zu den Tabellen siehe Seite 56 101"},
            {"bbox": [520, 178, 530, 501], "text": "Lebensdauern\nGrobelementarten\nStahlbau\nGebäudearten\nKostengruppen\nElementarten"},
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
    assert "Graphique ou visuel à vérifier" not in rendered
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


def test_bki_tables_markdown_mode_is_alias_for_bki():
    assert normalize_markdown_mode("bki-tables") == "bki"
    assert normalize_markdown_mode("bki") == "bki"


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


def test_structured_content_defaults_to_classic_without_receipt_detection():
    result = {
        "source": "receipt.jpg",
        "source_type": "image",
        "page_count": 1,
        "pages": [{
            "native_text": "",
            "ocr_text": "\n".join([
                "TOTALENERGIES RELAIS NICE",
                "Ticket CB",
                "Date 01/05/2026",
                "TOTAL TTC 60,18 EUR",
            ]),
        }],
    }

    assert extract_structured_content(result) == {}
    assert extract_structured_content(result, document_type="auto")["type"] == "receipt"


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


def test_long_classic_document_is_not_classified_as_receipt():
    result = {
        "source": "proposal.pdf",
        "source_type": "pdf",
        "page_count": 14,
        "pages": [{
            "ocr_text": "\n".join([
                "PROPOSITION TECHNIQUE & FINANCIERE",
                "MISSION G2 Phase AVP - G2 Phase PRO",
                "Montant TTC 52 972,06 €",
                "TVA à 20,00%",
            ])
        }],
    }

    assert extract_receipt_content(result) == {}


def test_payroll_document_is_not_classified_as_receipt():
    result = {
        "source": "payroll.pdf",
        "source_type": "pdf",
        "page_count": 1,
        "pages": [{
            "ocr_text": "\n".join([
                "BULLETIN##12-2024##00002##CELI##FILIPPO MARIA",
                "Salaire de base",
                "Salaire brut",
                "Montant net social",
                "Net à payer avant impôt sur le revenu",
                "Charges patronales",
                "Total des cotisations et contributions",
                "Net payé 6 137,91 euros",
            ])
        }],
    }

    text = result["pages"][0]["ocr_text"]
    assert is_payroll_document_text(text)
    assert extract_receipt_content(result) == {}


def test_filter_redundant_experimental_visual_when_table_covers_page():
    visuals = [{
        "source": "experimental-graph-image",
        "bbox": [0, 0, 1000, 1000],
    }]
    tables = [{
        "bbox": [100, 100, 900, 900],
    }]

    assert filter_redundant_experimental_visuals(1000, 1000, tables, visuals) == []


def test_bki_gebaeudeart_table_preserves_percent_column():
    table = bki_markdown_table_from_ocr(
        "353 Gebäudeart > €/Einheit\n"
        "Büro- und Verwaltungsgebäude, einfacher Standard 153,00 168,00 184,00 4,4%\n"
        "Pflegeheime 78,00 142,00 219,00 3,7%\n"
    )

    assert "KG an 300 (%)" in table
    assert "4,4%" in table
    assert "3,7%" in table


def test_bki_compact_price_parser_uses_explicit_lb_not_page_number():
    native_text = "\n".join([
        "151",
        "© BKI Baukosteninformationszentrum",
        "020",
        "Dachdeckungsarbeiten",
        "77",
        "Stundensatz, Facharbeiter/-in",
        "Stundenlohnarbeiten für Vorarbeiter/-in und Gleich-",
        "gestellte; Dachdeckung",
        "h",
        "€ brutto",
        "81",
        "91",
        "96",
        "100",
        "107",
        "€ netto",
        "68",
        "77",
        "81",
        "84",
        "90",
        "78",
        "Stundensatz, Helfer/-in",
        "Stundenlohnarbeiten für Werker/-in und Gleichgestellte;",
        "Dachdeckung",
        "h",
        "€ brutto",
        "53",
        "68",
        "77",
        "80",
        "89",
        "€ netto",
        "45",
        "57",
        "64",
        "67",
        "75",
        "LB 020",
        "Preise €",
    ])

    assert bki_compact_page_heading(native_text) == ("020", "Dachdeckungsarbeiten")

    rows = parse_bki_compact_price_items(native_text)
    assert [row["number"] for row in rows] == ["77", "78"]
    assert rows[0]["description"] == (
        "Stundenlohnarbeiten für Vorarbeiter/-in und Gleichgestellte; Dachdeckung"
    )
    assert rows[1]["net"] == ["45", "57", "64", "67", "75"]


def test_markdown_table_without_separator_removes_only_separator_rows():
    table = "| A | B |\n|---|---|\n| 1 | 2 |"

    assert markdown_table_without_separator(table) == "| A | B |\n| 1 | 2 |"


def test_bki_filter_redundant_tables_keeps_complete_table_and_drops_crop_fragments():
    complete = {
        "markdown_table": (
            "| Nr. | Positionen | Einheit | brutto min | brutto max |\n"
            "| 49 | Bautür, Holz | St | 141 | 517 |\n"
            "| 50 | Witterungsschutz, Fensteröffnung | m2 | 11 | 67 |"
        )
    }
    crop_header = {
        "markdown_table": (
            "| Baustelleneinrichtungen; Verkehrssicherungs- und Sicherheitseinrichtungen | Preise € |"
        )
    }
    duplicate_row = {
        "markdown_table": "| 49 | Bautür, Holz | St | 141 | 517 |"
    }

    assert bki_filter_redundant_tables([complete, crop_header, duplicate_row]) == [complete]


def test_bki_native_kostengruppen_table_reconstructs_pdf_page_blocks():
    table = bki_native_kostengruppen_table_from_blocks([
        {
            "bbox": [52.0, 21.3, 424.04, 33.1],
            "text": "Kostengruppen\n▷\n€/Einheit\n◁\nKG an 300+400",
        },
        {
            "bbox": [52.0, 42.44, 424.19, 81.96],
            "text": "\n".join([
                "380",
                "Baukonstruktive Einbauten",
                "381",
                "Allgemeine Einbauten [m2 BGF]",
                "7,00",
                "41,00",
                "76,00",
                "0,9%",
                "386",
                "Orientierungs- und Informationssysteme [m2 BGF]",
                "–",
                "1,40",
                "–",
                "< 0,1%",
                "389",
                "Sonstiges zur KG 380 [m2 BGF]",
                "–",
                "3,90",
                "–",
                "< 0,1%",
            ]),
        },
        {
            "bbox": [52.0, 92.44, 424.19, 151.96],
            "text": "\n".join([
                "390",
                "Sonstige Maßnahmen für Baukonstruktionen",
                "391",
                "Baustelleneinrichtung [m2 BGF]",
                "15,00",
                "42,00",
                "83,00",
                "2,4%",
            ]),
        },
        {
            "bbox": [52.0, 512.44, 187.35, 521.96],
            "text": "480\nGebäude- und Anlagenautomation",
        },
        {
            "bbox": [52.0, 532.44, 223.11, 541.96],
            "text": "490\nSonstige Maßnahmen für technische Anlagen",
        },
    ])

    assert table
    markdown = table["markdown_table"]
    assert "| KG | Kostengruppe | Einheit | von | Mittel | bis | KG an 300+400 |" in markdown
    assert "| 380 | Baukonstruktive Einbauten |  |  |  |  |  |" in markdown
    assert "| 381 | Allgemeine Einbauten | m² BGF | 7,00 | 41,00 | 76,00 | 0,9% |" in markdown
    assert "| 386 | Orientierungs- und Informationssysteme | m² BGF | – | 1,40 | – | < 0,1% |" in markdown
    assert "| 391 | Baustelleneinrichtung | m² BGF | 15,00 | 42,00 | 83,00 | 2,4% |" in markdown
    assert "| 480 | Gebäude- und Anlagenautomation |  |  |  |  |  |" in markdown
    assert "| 490 | Sonstige Maßnahmen für technische Anlagen |  |  |  |  |  |" in markdown


def test_bki_native_lifespan_table_reconstructs_pdf_page_blocks():
    table = bki_native_lifespan_table_from_blocks([
        {
            "bbox": [52.72, 21.86, 424.72, 31.44],
            "text": "Lebensdauer von Bauteilen in Jahren\ne\nmittel\nf\n0\n25\n50\n75\n100\n125 Jahre",
        },
        {
            "bbox": [52.73, 41.86, 121.71, 51.38],
            "text": "Außenwandöffnungen",
        },
        {
            "bbox": [52.73, 61.86, 266.17, 121.38],
            "text": "\n".join([
                "Fensterbänke, innen",
                "Holz",
                "36",
                "63",
                "99",
                "Naturstein",
                "61",
                "86",
                "121",
            ]),
        },
        {
            "bbox": [52.77, 481.86, 266.22, 501.38],
            "text": "\n".join([
                "Alutür mit Standardbeschlägen, Türschließer und",
                "normalem Schloss",
                "31",
                "46",
                "58",
            ]),
        },
        {
            "bbox": [52.78, 521.86, 266.22, 541.38],
            "text": "\n".join([
                "Kunststofftür mit Standardbeschlägen und Schließan-",
                "lage",
                "25",
                "37",
                "47",
            ]),
        },
        {
            "bbox": [51.0, 564.27, 521.56, 574.21],
            "text": "© BKI Baukosteninformationszentrum; Erläuterungen zu den Tabellen siehe Seite 56\n101",
        },
        {
            "bbox": [171.44, 1.91, 304.56, 10.15],
            "text": "Lizenz für user@example.com, Bestell.-Nr.: 22788",
        },
    ])

    assert table
    markdown = table["markdown_table"]
    assert "| Gruppe | Bauteil | von | Mittel | bis |" in markdown
    assert "| Außenwandöffnungen |  |  |  |  |" in markdown
    assert "| Fensterbänke, innen | Holz | 36 | 63 | 99 |" in markdown
    assert "| Fensterbänke, innen | Naturstein | 61 | 86 | 121 |" in markdown
    assert (
        "| Fensterbänke, innen | Alutür mit Standardbeschlägen, Türschließer und normalem Schloss | 31 | 46 | 58 |"
        in markdown
    )
    assert (
        "| Fensterbänke, innen | Kunststofftür mit Standardbeschlägen und Schließanlage | 25 | 37 | 47 |"
        in markdown
    )
    assert "BKI Baukosteninformationszentrum" not in markdown
    assert "Lizenz für" not in markdown


def test_bki_text_duplicates_table_detects_flattened_table_paragraph():
    table = {
        "markdown_table": (
            "| Gebäudeart | Rohbau | Ausbau | TA |\n"
            "| Einzel- und Doppelgaragen, 7 Objekte, S. 992 | 62,7 | 6,5 | 1,1 |\n"
            "| Mehrfachgaragen, 8 Objekte, S. 998 | 71,8 | 13,0 | 5,1 |\n"
            "| Hochgaragen, 6 Objekte, S. 1004 | 75,4 | 12,0 | 8,0 |\n"
            "| Friedhofsgebäude, 18 Objekte, S. 1126 | 53,7 | 26,0 | 11,5 |"
        )
    }
    text = (
        "Gebäudeart Rohbau Ausbau TA Einzel- und Doppelgaragen, 7 Objekte, S. 992 "
        "62,7 6,5 1,1 Mehrfachgaragen, 8 Objekte, S. 998 71,8 13,0 5,1 "
        "Hochgaragen, 6 Objekte, S. 1004 75,4 12,0 8,0 "
        "Friedhofsgebäude, 18 Objekte, S. 1126 53,7 26,0 11,5"
    )

    assert bki_text_duplicates_table(text, [table])


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
