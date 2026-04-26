from __future__ import annotations

from datetime import UTC, datetime

import pytest

from read_later_digest.domain.digest_builder import DigestBuilder
from read_later_digest.domain.models import (
    ArticleSummary,
    ArticleType,
    Digest,
    NotionArticle,
    Priority,
    ProcessedArticle,
    ProcessStatus,
)


def _article(
    *,
    page_id: str = "p1",
    title: str = "タイトル",
    url: str = "https://example.com/a",
    added_at: datetime | None = None,
) -> NotionArticle:
    return NotionArticle(
        page_id=page_id,
        title=title,
        url=url,
        added_at=added_at or datetime(2026, 4, 25, 9, 0, tzinfo=UTC),
        age_days=1,
    )


def _summary(
    *,
    type_: ArticleType | None = ArticleType.TECH,
    priority: Priority | None = Priority.MID,
    summary_lines: list[str] | None = None,
    key_points: list[str] | None = None,
) -> ArticleSummary:
    return ArticleSummary(
        summary_lines=summary_lines or ["要約A", "要約B", "要約C"],
        key_points=key_points or ["点1", "点2", "点3"],
        type_=type_,
        priority=priority,
    )


def _success(
    *,
    page_id: str = "p1",
    title: str = "タイトル",
    url: str = "https://example.com/a",
    added_at: datetime | None = None,
    summary: ArticleSummary | None = None,
) -> ProcessedArticle:
    return ProcessedArticle(
        article=_article(page_id=page_id, title=title, url=url, added_at=added_at),
        status=ProcessStatus.SUCCESS,
        summary=summary or _summary(),
        error_reason=None,
    )


def _failure(
    *,
    page_id: str = "f1",
    title: str = "失敗記事",
    url: str = "https://example.com/f",
    reason: str = "fetch_failed: timeout",
    added_at: datetime | None = None,
) -> ProcessedArticle:
    return ProcessedArticle(
        article=_article(page_id=page_id, title=title, url=url, added_at=added_at),
        status=ProcessStatus.FETCH_FAILED,
        summary=None,
        error_reason=reason,
    )


@pytest.fixture
def builder() -> DigestBuilder:
    return DigestBuilder()


# --- AC6: 0件 -----------------------------------------------------------------


def test_zero_articles_emits_silent_day_message_in_html_and_text(
    builder: DigestBuilder,
) -> None:
    digest = Digest(target_date="2026-04-25", succeeded=[], failed=[])

    rendered = builder.build(digest)

    assert "本日の未読 0 件。" in rendered.text
    assert "本日の未読 0 件。" in rendered.html
    assert "(0 件)" in rendered.subject


# --- AC1, AC3, AC5: 成功 1 件 -------------------------------------------------


def test_single_success_includes_title_url_summary_keypoints_and_tags(
    builder: DigestBuilder,
) -> None:
    item = _success(
        title="記事A",
        url="https://example.com/a",
        summary=_summary(
            type_=ArticleType.TECH,
            priority=Priority.HIGH,
            summary_lines=["概要1", "概要2", "概要3"],
            key_points=["KP1", "KP2"],
        ),
    )
    digest = Digest(target_date="2026-04-25", succeeded=[item], failed=[])

    rendered = builder.build(digest)

    for fragment in ["記事A", "https://example.com/a", "概要1", "概要2", "概要3", "KP1", "KP2"]:
        assert fragment in rendered.text
        assert fragment in rendered.html
    assert "技術" in rendered.text
    assert "高" in rendered.text
    assert "技術" in rendered.html
    assert "高" in rendered.html


def test_unset_type_and_priority_render_as_japanese_placeholders(
    builder: DigestBuilder,
) -> None:
    item = _success(summary=_summary(type_=None, priority=None))
    digest = Digest(target_date="2026-04-25", succeeded=[item], failed=[])

    rendered = builder.build(digest)

    assert "未分類" in rendered.text
    assert "未設定" in rendered.text
    assert "未分類" in rendered.html
    assert "未設定" in rendered.html


# --- AC4: 失敗のみ ------------------------------------------------------------


def test_only_failures_emits_failure_section_without_toc(builder: DigestBuilder) -> None:
    fail = _failure(url="https://example.com/x", reason="llm_failed: schema")
    digest = Digest(target_date="2026-04-25", succeeded=[], failed=[fail])

    rendered = builder.build(digest)

    assert "処理失敗一覧" in rendered.text
    assert "https://example.com/x" in rendered.text
    assert "llm_failed: schema" in rendered.text
    assert "処理失敗一覧" in rendered.html
    assert "目次" not in rendered.html  # no successes
    assert "目次" not in rendered.text


# --- AC1, AC4, AC8: 混在 ------------------------------------------------------


def test_mixed_success_and_failure_includes_both_sections_and_subject_failed_count(
    builder: DigestBuilder,
) -> None:
    digest = Digest(
        target_date="2026-04-25",
        succeeded=[_success(title="成功A")],
        failed=[_failure(title="失敗A"), _failure(page_id="f2", title="失敗B")],
    )

    rendered = builder.build(digest)

    assert "成功A" in rendered.html
    assert "失敗A" in rendered.html
    assert "失敗B" in rendered.html
    assert "(1 件)" in rendered.subject
    assert "失敗 2 件" in rendered.subject


# --- AC2: 並び順 --------------------------------------------------------------


def test_succeeded_sorted_by_priority_descending() -> None:
    builder = DigestBuilder()
    low = _success(page_id="a", title="LOW", summary=_summary(priority=Priority.LOW))
    high = _success(page_id="b", title="HIGH", summary=_summary(priority=Priority.HIGH))
    none_p = _success(page_id="c", title="NONE", summary=_summary(priority=None))
    mid = _success(page_id="d", title="MID", summary=_summary(priority=Priority.MID))
    digest = Digest(target_date="2026-04-25", succeeded=[low, high, none_p, mid], failed=[])

    rendered = builder.build(digest)

    order = [
        rendered.text.index("== 1. HIGH =="),
        rendered.text.index("== 2. MID =="),
        rendered.text.index("== 3. LOW =="),
        rendered.text.index("== 4. NONE =="),
    ]
    assert order == sorted(order)


def test_succeeded_same_priority_sorted_by_added_at_then_page_id() -> None:
    builder = DigestBuilder()
    base = datetime(2026, 4, 25, 9, 0, tzinfo=UTC)
    later = datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    p_late = _success(page_id="z", title="LATE", added_at=later)
    p_early_b = _success(page_id="b", title="EARLY_B", added_at=base)
    p_early_a = _success(page_id="a", title="EARLY_A", added_at=base)
    digest = Digest(
        target_date="2026-04-25",
        succeeded=[p_late, p_early_b, p_early_a],
        failed=[],
    )

    rendered = builder.build(digest)

    idx_a = rendered.text.index("EARLY_A")
    idx_b = rendered.text.index("EARLY_B")
    idx_late = rendered.text.index("LATE")
    assert idx_a < idx_b < idx_late


# --- AC7: HTML エスケープ ----------------------------------------------------


def test_html_escapes_title_and_summary_to_prevent_xss(builder: DigestBuilder) -> None:
    item = _success(
        title="<script>alert(1)</script>",
        summary=_summary(summary_lines=["</p><img onerror=x>", "y", "z"]),
    )
    digest = Digest(target_date="2026-04-25", succeeded=[item], failed=[])

    rendered = builder.build(digest)

    assert "<script>alert(1)</script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert "<img onerror=x>" not in rendered.html


def test_html_escapes_url_attribute_quotes(builder: DigestBuilder) -> None:
    item = _success(url='https://example.com/?q="bad"')
    digest = Digest(target_date="2026-04-25", succeeded=[item], failed=[])

    rendered = builder.build(digest)

    assert '"bad"' not in rendered.html
    assert "&quot;bad&quot;" in rendered.html


# --- AC8: 件名フォーマット ----------------------------------------------------


def test_subject_includes_prefix_date_and_success_count(builder: DigestBuilder) -> None:
    digest = Digest(
        target_date="2026-04-25",
        succeeded=[_success(), _success(page_id="p2", title="t2")],
        failed=[],
    )

    rendered = builder.build(digest)

    assert rendered.subject == "[read-later-digest] 2026-04-25 (2 件)"


def test_subject_omits_failed_suffix_when_all_succeed(builder: DigestBuilder) -> None:
    digest = Digest(target_date="2026-04-25", succeeded=[_success()], failed=[])

    rendered = builder.build(digest)

    assert "失敗" not in rendered.subject


def test_build_does_not_mutate_input_lists(builder: DigestBuilder) -> None:
    a = _success(page_id="b", title="B", summary=_summary(priority=Priority.LOW))
    b = _success(page_id="a", title="A", summary=_summary(priority=Priority.HIGH))
    succ = [a, b]
    fail: list[ProcessedArticle] = []
    digest = Digest(target_date="2026-04-25", succeeded=succ, failed=fail)

    builder.build(digest)

    assert succ == [a, b]  # original order preserved despite internal sort


def test_custom_subject_prefix_is_used() -> None:
    builder = DigestBuilder(subject_prefix="[custom]")
    digest = Digest(target_date="2026-04-25", succeeded=[], failed=[])

    rendered = builder.build(digest)

    assert rendered.subject.startswith("[custom] 2026-04-25")


# --- per_article rendering ---------------------------------------------------


def test_build_per_article_subject_includes_index_total_and_title(
    builder: DigestBuilder,
) -> None:
    p = _success(title="Hello")

    rendered = builder.build_per_article(p, target_date="2026-04-25", index=2, total=5)

    assert rendered.subject == "[read-later-digest] 2026-04-25 (2/5) Hello"


def test_build_per_article_text_contains_summary_keypoints_and_url(
    builder: DigestBuilder,
) -> None:
    p = _success(
        title="Hello",
        url="https://example.com/h",
        summary=_summary(summary_lines=["S1", "S2", "S3"], key_points=["K1", "K2"]),
    )

    rendered = builder.build_per_article(p, target_date="2026-04-25", index=1, total=1)

    assert "https://example.com/h" in rendered.text
    assert "S1" in rendered.text and "K1" in rendered.text


def test_build_per_article_html_escapes_title_to_prevent_xss(builder: DigestBuilder) -> None:
    p = _success(title="<script>alert(1)</script>")

    rendered = builder.build_per_article(p, target_date="2026-04-25", index=1, total=1)

    # Raw tag must not survive into the HTML output.
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html


def test_build_per_article_rejects_failed_article(builder: DigestBuilder) -> None:
    failed = _failure()

    with pytest.raises(ValueError, match="successful ProcessedArticle"):
        builder.build_per_article(failed, target_date="2026-04-25", index=1, total=1)


def test_build_failure_summary_subject_and_listing(builder: DigestBuilder) -> None:
    f1 = _failure(page_id="f1", title="失敗A", reason="fetch_failed: timeout")
    f2 = _failure(page_id="f2", title="失敗B", reason="llm_failed: schema")

    rendered = builder.build_failure_summary([f1, f2], target_date="2026-04-25")

    assert rendered.subject == "[read-later-digest] 2026-04-25 失敗 2 件"
    assert "失敗A" in rendered.text and "失敗B" in rendered.text
    assert "fetch_failed: timeout" in rendered.text
    assert "llm_failed: schema" in rendered.text


def test_build_failure_summary_rejects_empty_list(builder: DigestBuilder) -> None:
    with pytest.raises(ValueError, match="at least one failed article"):
        builder.build_failure_summary([], target_date="2026-04-25")
