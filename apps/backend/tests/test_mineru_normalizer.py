from app.services.mineru_result_normalizer import MinerUResultNormalizer


def test_markdown_normalizer_creates_parent_and_child_hierarchy() -> None:
    markdown = """# 材料\n\n低甲醛环保板材应核验检测报告。\n\n## 施工\n\n封边工艺和现场通风条件必须同步核查。"""

    chunks = MinerUResultNormalizer(parent_max_chars=200, child_max_chars=40).markdown_to_parent_child_chunks(markdown)

    assert len(chunks) == 2
    assert chunks[0]["section_path"] == "材料"
    assert chunks[1]["section_path"] == "材料 / 施工"
    assert chunks[1]["children"][0]["content"].startswith("封边工艺")
    assert chunks[1]["children"][0]["ordinal"] == 0


def test_markdown_normalizer_splits_long_parent_content() -> None:
    chunks = MinerUResultNormalizer(parent_max_chars=10, child_max_chars=10).markdown_to_parent_child_chunks(
        "第一段内容。\n\n第二段内容。"
    )

    assert len(chunks) == 2
    assert [item["ordinal"] for item in chunks] == [0, 1]
    assert all(item["children"] for item in chunks)
