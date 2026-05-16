from __future__ import annotations

import pytest
from digestkit.types import Digest, Item

from read_later_digest.config import Config
from read_later_digest.digester import ReadLaterDigester
from read_later_digest.wiring import (
    _build_summary_blocks,
    _bullet_block,
    _heading_block,
    _make_success_properties,
    _paragraph_block,
    build_digester,
)

# ---------- helpers ----------


def _digest(summary_json: str) -> Digest:
    return Digest(summary=summary_json, tokens_in=0, tokens_out=0, latency_ms=0, model="test")


def _item(id: str = "page-1") -> Item:
    return Item(id=id, payload=None)


def _minimal_config(monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("NOTION_DB_ID", "db-1")
    monkeypatch.setenv("NOTION_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("NOTIFY_CHANNELS", "slack")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    return Config.from_env()


# ---------- block helpers ----------


class TestParagraphBlock:
    def test_returns_paragraph_type(self) -> None:
        block = _paragraph_block("hello")
        assert block["type"] == "paragraph"

    def test_content_matches_input(self) -> None:
        block = _paragraph_block("テスト")
        assert block["paragraph"]["rich_text"][0]["text"]["content"] == "テスト"

    def test_empty_string(self) -> None:
        block = _paragraph_block("")
        assert block["paragraph"]["rich_text"][0]["text"]["content"] == ""


class TestHeadingBlock:
    def test_returns_heading_3_type(self) -> None:
        block = _heading_block("見出し")
        assert block["type"] == "heading_3"

    def test_content_matches_input(self) -> None:
        block = _heading_block("要約")
        assert block["heading_3"]["rich_text"][0]["text"]["content"] == "要約"


class TestBulletBlock:
    def test_returns_bulleted_list_item_type(self) -> None:
        block = _bullet_block("ポイント")
        assert block["type"] == "bulleted_list_item"

    def test_content_matches_input(self) -> None:
        block = _bullet_block("重要")
        assert block["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "重要"


# ---------- _make_success_properties ----------


class TestMakeSuccessProperties:
    def test_includes_type_and_priority_when_both_set(self) -> None:
        digest = _digest('{"summary_lines":["a"],"key_points":["x"],"type":"記事","priority":"中"}')
        item = _item()

        props = _make_success_properties(item, digest)

        assert props == {
            "Type": {"select": {"name": "記事"}},
            "Priority": {"select": {"name": "中"}},
        }

    def test_includes_only_type_when_priority_is_null(self) -> None:
        digest = _digest('{"summary_lines":["a"],"key_points":["x"],"type":"技術","priority":null}')
        item = _item()

        props = _make_success_properties(item, digest)

        assert "Type" in props
        assert "Priority" not in props
        assert props["Type"] == {"select": {"name": "技術"}}

    def test_includes_only_priority_when_type_is_null(self) -> None:
        digest = _digest('{"summary_lines":["a"],"key_points":["x"],"type":null,"priority":"高"}')
        item = _item()

        props = _make_success_properties(item, digest)

        assert "Priority" in props
        assert "Type" not in props
        assert props["Priority"] == {"select": {"name": "高"}}

    def test_returns_empty_dict_when_both_null(self) -> None:
        digest = _digest('{"summary_lines":["a"],"key_points":["x"],"type":null,"priority":null}')
        item = _item()

        props = _make_success_properties(item, digest)

        assert props == {}

    def test_returns_empty_dict_when_fields_absent(self) -> None:
        digest = _digest('{"summary_lines":["a"],"key_points":["x"]}')
        item = _item()

        props = _make_success_properties(item, digest)

        assert props == {}

    @pytest.mark.parametrize(
        "type_val,expected_name",
        [
            ("記事", "記事"),
            ("技術", "技術"),
            ("ネタ", "ネタ"),
            ("仕事", "仕事"),
        ],
    )
    def test_all_article_types(self, type_val: str, expected_name: str) -> None:
        digest = _digest(
            f'{{"summary_lines":["a"],"key_points":["x"],"type":"{type_val}","priority":null}}'
        )
        item = _item()

        props = _make_success_properties(item, digest)

        assert props["Type"]["select"]["name"] == expected_name

    @pytest.mark.parametrize(
        "priority_val,expected_name", [("高", "高"), ("中", "中"), ("低", "低")]
    )
    def test_all_priority_values(self, priority_val: str, expected_name: str) -> None:
        digest = _digest(
            f'{{"summary_lines":["a"],"key_points":["x"],"type":null,"priority":"{priority_val}"}}'
        )
        item = _item()

        props = _make_success_properties(item, digest)

        assert props["Priority"]["select"]["name"] == expected_name

    def test_item_payload_does_not_affect_output(self) -> None:
        digest = _digest('{"summary_lines":["a"],"key_points":["x"],"type":null,"priority":null}')

        props = _make_success_properties(Item(id="id-A", payload=None), digest)

        assert props == {}

    def test_item_id_does_not_affect_output(self) -> None:
        digest = _digest('{"summary_lines":["a"],"key_points":["x"],"type":null,"priority":null}')

        props = _make_success_properties(Item(id="id-B", payload="https://example.com"), digest)

        assert props == {}

    def test_unknown_type_string_is_treated_as_null(self) -> None:
        # ArticleSummary._coerce_type coerces unknown values to None; the callback must
        # omit the "Type" key in that case.
        digest = _digest(
            '{"summary_lines":["a"],"key_points":["x"],"type":"unknown-value","priority":null}'
        )
        item = _item()

        props = _make_success_properties(item, digest)

        assert "Type" not in props


# ---------- _build_summary_blocks ----------


class TestBuildSummaryBlocks:
    def test_starts_with_heading_block(self) -> None:
        digest = _digest('{"summary_lines":["a"],"key_points":["x"]}')
        item = _item()

        blocks = _build_summary_blocks(digest, item)

        assert blocks[0]["type"] == "heading_3"
        assert blocks[0]["heading_3"]["rich_text"][0]["text"]["content"] == "要約"

    def test_three_summary_lines_and_two_key_points_yield_seven_blocks(self) -> None:
        digest = _digest('{"summary_lines":["a","b","c"],"key_points":["x","y"]}')
        item = _item()

        blocks = _build_summary_blocks(digest, item)

        # heading(要約) + 3 paragraphs + heading(重要ポイント) + 2 bullets = 7
        assert len(blocks) == 7

    def test_summary_paragraph_contents_match_summary_lines(self) -> None:
        digest = _digest('{"summary_lines":["行1","行2","行3"],"key_points":["p1"]}')
        item = _item()

        blocks = _build_summary_blocks(digest, item)

        para_blocks = [b for b in blocks if b["type"] == "paragraph"]
        contents = [b["paragraph"]["rich_text"][0]["text"]["content"] for b in para_blocks]
        assert contents == ["行1", "行2", "行3"]

    def test_key_points_become_bulleted_list_items(self) -> None:
        digest = _digest('{"summary_lines":["a"],"key_points":["pt1","pt2"]}')
        item = _item()

        blocks = _build_summary_blocks(digest, item)

        bullet_blocks = [b for b in blocks if b["type"] == "bulleted_list_item"]
        contents = [
            b["bulleted_list_item"]["rich_text"][0]["text"]["content"] for b in bullet_blocks
        ]
        assert contents == ["pt1", "pt2"]

    def test_no_key_points_omits_important_heading_and_bullets(self) -> None:
        digest = _digest('{"summary_lines":["a","b","c"],"key_points":[]}')
        item = _item()

        blocks = _build_summary_blocks(digest, item)

        headings = [b for b in blocks if b["type"] == "heading_3"]
        bullets = [b for b in blocks if b["type"] == "bulleted_list_item"]
        assert len(headings) == 1  # only 要約
        assert len(bullets) == 0

    def test_key_points_heading_text_is_correct(self) -> None:
        digest = _digest('{"summary_lines":["a"],"key_points":["x"]}')
        item = _item()

        blocks = _build_summary_blocks(digest, item)

        headings = [b for b in blocks if b["type"] == "heading_3"]
        texts = [b["heading_3"]["rich_text"][0]["text"]["content"] for b in headings]
        assert "重要ポイント" in texts

    def test_single_summary_line_single_key_point(self) -> None:
        digest = _digest('{"summary_lines":["only"],"key_points":["kp1"]}')
        item = _item()

        blocks = _build_summary_blocks(digest, item)

        # heading + 1 paragraph + heading + 1 bullet = 4
        assert len(blocks) == 4

    def test_block_order_is_heading_paragraphs_heading_bullets(self) -> None:
        digest = _digest('{"summary_lines":["s1","s2"],"key_points":["k1"]}')
        item = _item()

        blocks = _build_summary_blocks(digest, item)

        # [0] heading_3(要約), [1] para(s1), [2] para(s2), [3] heading_3(重要ポイント), [4] bullet(k1)
        assert blocks[0]["type"] == "heading_3"
        assert blocks[1]["type"] == "paragraph"
        assert blocks[2]["type"] == "paragraph"
        assert blocks[3]["type"] == "heading_3"
        assert blocks[4]["type"] == "bulleted_list_item"


# ---------- build_digester ----------


class TestBuildDigester:
    def test_returns_read_later_digester_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = _minimal_config(monkeypatch)

        digester = build_digester(config)

        assert isinstance(digester, ReadLaterDigester)

    def test_custom_max_items_per_run_is_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_ITEMS_PER_RUN", "5")
        config = _minimal_config(monkeypatch)

        digester = build_digester(config)

        assert digester._max_items_per_run == 5

    def test_no_network_calls_during_construction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # build_digester must be a pure factory — no Notion/Slack/LLM calls.
        # If construction triggered a network call, it would raise since the
        # credentials are dummy values. Reaching this assertion means it did not.
        config = _minimal_config(monkeypatch)

        build_digester(config)  # must not raise
