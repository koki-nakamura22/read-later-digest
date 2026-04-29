"""Unit tests for scripts/create-notion-db.py.

The script lives outside the package, so we load it via importlib from its
absolute path and exercise the pure payload-construction helpers plus
`main()` with `--dry-run` (no Notion API).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "create-notion-db.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("create_notion_db", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["create_notion_db"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load()


# ---------------------------------------------------------------------------
# build_properties_payload
# ---------------------------------------------------------------------------


class TestBuildPropertiesPayload:
    def test_contains_all_documented_properties(self, script: ModuleType) -> None:
        # Notion ignores property order from the create payload (columns are
        # rendered title-first then alphabetical), so we assert membership
        # only — the desired UI order is a one-time manual drag.
        props = script.build_properties_payload()
        assert set(props.keys()) == {
            "Name",
            "URL",
            "Status",
            "Type",
            "Priority",
            "AddedAt",
            "Age",
        }

    def test_name_is_title(self, script: ModuleType) -> None:
        assert script.build_properties_payload()["Name"] == {"title": {}}

    def test_url_is_url_type(self, script: ModuleType) -> None:
        assert script.build_properties_payload()["URL"] == {"url": {}}

    def test_added_at_is_created_time(self, script: ModuleType) -> None:
        # AddedAt must be created_time (auto-populated by Notion on insert) so
        # the formula `Age` can compute days-since-creation deterministically.
        assert script.build_properties_payload()["AddedAt"] == {"created_time": {}}

    def test_status_options_match_schema(self, script: ModuleType) -> None:
        status = script.build_properties_payload()["Status"]
        names = [opt["name"] for opt in status["select"]["options"]]
        assert names == ["未読", "処理済み", "不要"]

    def test_type_options_match_schema(self, script: ModuleType) -> None:
        type_prop = script.build_properties_payload()["Type"]
        names = [opt["name"] for opt in type_prop["select"]["options"]]
        assert names == ["記事", "技術", "ネタ", "仕事"]

    def test_priority_options_match_schema(self, script: ModuleType) -> None:
        prio = script.build_properties_payload()["Priority"]
        names = [opt["name"] for opt in prio["select"]["options"]]
        assert names == ["高", "中", "低"]

    def test_age_is_formula_with_expected_expression(self, script: ModuleType) -> None:
        age = script.build_properties_payload()["Age"]
        # Days between now and AddedAt — depended on by docs/functional-design.md
        # ("Age = AddedAt からの経過日数"). Hard-asserting the literal string
        # protects against accidental edits to the formula source.
        assert age == {"formula": {"expression": 'dateBetween(now(), prop("AddedAt"), "days")'}}


# ---------------------------------------------------------------------------
# build_create_payload
# ---------------------------------------------------------------------------


class TestBuildCreatePayload:
    def test_includes_parent_page_id(self, script: ModuleType) -> None:
        body = script.build_create_payload("page-xyz", "Read Later")
        assert body["parent"] == {"type": "page_id", "page_id": "page-xyz"}

    def test_title_is_rich_text_array(self, script: ModuleType) -> None:
        body = script.build_create_payload("page-xyz", "Custom Title")
        assert body["title"] == [{"type": "text", "text": {"content": "Custom Title"}}]

    def test_properties_nested_under_initial_data_source(self, script: ModuleType) -> None:
        # Notion API 2025-09-03+ requires properties under
        # `initial_data_source`. Top-level `properties` is silently ignored
        # and the DB ends up with only the default `Name` title property.
        body = script.build_create_payload("page-xyz", "Read Later")
        assert "properties" not in body
        assert body["initial_data_source"]["properties"] == script.build_properties_payload()


# ---------------------------------------------------------------------------
# _resolve_token
# ---------------------------------------------------------------------------


class TestResolveToken:
    def test_cli_token_takes_precedence(
        self, script: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOTION_TOKEN", "from-env")
        assert script._resolve_token("from-cli") == "from-cli"

    def test_falls_back_to_env(self, script: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTION_TOKEN", "from-env")
        assert script._resolve_token(None) == "from-env"

    def test_returns_none_when_neither_set(
        self, script: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        assert script._resolve_token(None) is None

    def test_empty_env_treated_as_unset(
        self, script: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOTION_TOKEN", "")
        assert script._resolve_token(None) is None


# ---------------------------------------------------------------------------
# _extract_data_source_id
# ---------------------------------------------------------------------------


class TestExtractDataSourceId:
    def test_returns_first_data_source_id(self, script: ModuleType) -> None:
        retrieved = {"data_sources": [{"id": "ds-1"}, {"id": "ds-2"}]}
        assert script._extract_data_source_id(retrieved) == "ds-1"

    def test_returns_none_when_missing(self, script: ModuleType) -> None:
        assert script._extract_data_source_id({}) is None

    def test_returns_none_when_empty_list(self, script: ModuleType) -> None:
        assert script._extract_data_source_id({"data_sources": []}) is None

    def test_returns_none_when_id_missing(self, script: ModuleType) -> None:
        assert script._extract_data_source_id({"data_sources": [{}]}) is None


# ---------------------------------------------------------------------------
# main(--dry-run)
# ---------------------------------------------------------------------------


class TestMainDryRun:
    def test_prints_valid_json_payload(
        self, script: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = script.main(["--parent-page-id", "page-xyz", "--dry-run"])
        assert rc == 0
        captured = capsys.readouterr()
        body = json.loads(captured.out)
        assert body["parent"]["page_id"] == "page-xyz"
        properties = body["initial_data_source"]["properties"]
        assert "Status" in properties
        assert "Age" in properties

    def test_dry_run_does_not_import_notion_client(
        self, script: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Sentinel: if main() reached the network branch it would import
        # notion_client. Force the import to blow up; --dry-run must short-
        # circuit before that point.
        import builtins

        original_import = builtins.__import__

        def _fail_on_notion(name: str, *args: object, **kwargs: object) -> object:
            if name.startswith("notion_client"):
                raise AssertionError(
                    f"notion_client must not be imported in --dry-run; got import of {name}"
                )
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fail_on_notion)
        rc = script.main(["--parent-page-id", "p", "--dry-run"])
        assert rc == 0

    def test_uses_custom_title(
        self, script: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = script.main(["--parent-page-id", "p", "--title", "My Reading Queue", "--dry-run"])
        assert rc == 0
        body = json.loads(capsys.readouterr().out)
        assert body["title"][0]["text"]["content"] == "My Reading Queue"


# ---------------------------------------------------------------------------
# main() — token resolution error path
# ---------------------------------------------------------------------------


class TestMainTokenError:
    def test_missing_token_returns_nonzero(
        self,
        script: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        rc = script.main(["--parent-page-id", "p"])
        assert rc == 1
        assert "Notion token not provided" in capsys.readouterr().err
