from reno.condition_match import assess


def test_controlled_space_mismatch_is_explicit_but_free_text_is_unknown():
    result = assess({"space": "厨房", "conditions": {"基层": "水泥基且无松散"}}, {"空间": "卫生间", "基层": "水泥"})
    assert result["status"] == "mismatch"
    assert [d["status"] for d in result["details"]] == ["mismatch", "unknown"]
    assert result["exceptions_evaluated"] is False


def test_missing_legacy_and_broad_scope_never_fake_a_match():
    assert assess({"conditions": {"base": "水泥"}}, {"基层": "水泥"})["status"] == "unknown"
    assert assess({"space": "全屋"}, {"空间": "卫生间"})["status"] == "unknown"
    assert assess({"space": "卫生间"}, {})["status"] == "unknown"
    assert assess({"conditions": {"基层": {"name": "水泥"}}}, {"基层": "水泥"})["status"] == "unknown"


def test_exact_values_match_with_qualification_not_compliance():
    result = assess({"conditions": {"基层": "水泥"}, "space": "卫生间"}, {"空间": "卫生间", "基层": "水泥"})
    assert result["status"] == "match"
    assert "仍须核对" in result["reason"]
