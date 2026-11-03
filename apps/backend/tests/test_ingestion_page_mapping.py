from app.services.ingestion_job_service import IngestionJobService


def test_page_range_results_map_chunks_assets_and_nested_locators_to_original_pdf_pages() -> None:
    records = [{
        "page_start": 1, "page_end": 2, "source_locator": {"page": 1, "page_end": 2},
        "children": [{"page_start": 2, "page_end": 2, "source_locator": {"page_number": 2}}],
    }, {"page_number": 3, "source_locator": {"page_number": 3}}]

    mapped = IngestionJobService._map_original_pdf_page_numbers(records, {"source_page_numbers": [7, 9]})

    assert mapped[0]["page_start"] == 7
    assert mapped[0]["page_end"] == 9
    assert mapped[0]["source_locator"] == {"page": 7, "page_end": 9}
    assert mapped[0]["children"][0]["page_start"] == 9
    assert mapped[0]["children"][0]["source_locator"] == {"page_number": 9}
    assert mapped[0]["metadata"]["mineru_subpdf_page_start"] == 1
    assert mapped[1]["page_number"] == 3
    assert "page_mapping_warning" in mapped[1]["metadata"]
