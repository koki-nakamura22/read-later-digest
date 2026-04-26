from __future__ import annotations

import html

from read_later_digest.domain.models import (
    ArticleSummary,
    Digest,
    Priority,
    ProcessedArticle,
    RenderedDigest,
)

_PRIORITY_RANK: dict[Priority | None, int] = {
    Priority.HIGH: 3,
    Priority.MID: 2,
    Priority.LOW: 1,
    None: 0,
}

_UNCATEGORIZED_TYPE = "未分類"
_UNSET_PRIORITY = "未設定"


class DigestBuilder:
    """Render a `Digest` into HTML / plain text bodies plus a mail subject.

    Pure function: no I/O, no clock access. Output is deterministic given input.
    """

    def __init__(self, *, subject_prefix: str = "[read-later-digest]") -> None:
        self._subject_prefix = subject_prefix

    def build(self, digest: Digest) -> RenderedDigest:
        succeeded = self._sort_succeeded(digest.succeeded)
        failed = self._sort_failed(digest.failed)
        subject = self._build_subject(digest.target_date, len(succeeded), len(failed))
        text = self._render_text(digest.target_date, succeeded, failed)
        html_body = self._render_html(subject, digest.target_date, succeeded, failed)
        return RenderedDigest(subject=subject, html=html_body, text=text)

    def build_per_article(
        self,
        processed: ProcessedArticle,
        *,
        target_date: str,
        index: int,
        total: int,
    ) -> RenderedDigest:
        """Render one successfully summarized article as a standalone notification.

        Used by NOTIFY_GRANULARITY=per_article. The caller is responsible for
        passing only `ProcessStatus.SUCCESS` entries; failed articles share the
        aggregated failure summary built by `build_failure_summary`.
        """
        if processed.summary is None:
            raise ValueError("build_per_article requires a successful ProcessedArticle")
        subject = self._build_per_article_subject(
            target_date, index, total, processed.article.title
        )
        text = self._render_per_article_text(processed)
        html_body = self._render_per_article_html(subject, processed)
        return RenderedDigest(subject=subject, html=html_body, text=text)

    def build_failure_summary(
        self,
        failed: list[ProcessedArticle],
        *,
        target_date: str,
    ) -> RenderedDigest:
        """Render a single aggregated failure summary for per_article mode.

        Per the policy in docs/functional-design.md: failed articles are bundled
        into one summary message rather than one-per-failure, so the chat
        channel does not get spammed when many fetches fail in a row.
        Returns a RenderedDigest the caller can hand to mailer/notifier.
        """
        if not failed:
            raise ValueError("build_failure_summary requires at least one failed article")
        ordered = self._sort_failed(failed)
        subject = f"{self._subject_prefix} {target_date} 失敗 {len(ordered)} 件"
        text = self._render_failure_text(target_date, ordered)
        html_body = self._render_failure_html(subject, target_date, ordered)
        return RenderedDigest(subject=subject, html=html_body, text=text)

    @staticmethod
    def _sort_succeeded(items: list[ProcessedArticle]) -> list[ProcessedArticle]:
        def key(p: ProcessedArticle) -> tuple[int, object, str]:
            summary = p.summary
            priority = summary.priority if summary is not None else None
            rank = _PRIORITY_RANK[priority]
            # Negate rank for descending; tie-break by added_at ascending then page_id.
            return (-rank, p.article.added_at, p.article.page_id)

        return sorted(items, key=key)

    @staticmethod
    def _sort_failed(items: list[ProcessedArticle]) -> list[ProcessedArticle]:
        return sorted(items, key=lambda p: (p.article.added_at, p.article.page_id))

    def _build_subject(self, target_date: str, n_success: int, n_failed: int) -> str:
        base = f"{self._subject_prefix} {target_date} ({n_success} 件)"
        if n_failed > 0:
            return f"{base} 失敗 {n_failed} 件"
        return base

    @staticmethod
    def _tag_label(summary: ArticleSummary) -> tuple[str, str]:
        type_label = summary.type_.value if summary.type_ is not None else _UNCATEGORIZED_TYPE
        priority_label = summary.priority.value if summary.priority is not None else _UNSET_PRIORITY
        return type_label, priority_label

    def _render_text(
        self,
        target_date: str,
        succeeded: list[ProcessedArticle],
        failed: list[ProcessedArticle],
    ) -> str:
        lines: list[str] = []
        lines.append(f"{target_date} のダイジェスト")
        lines.append(f"成功 {len(succeeded)} 件 / 失敗 {len(failed)} 件")
        lines.append("")

        if not succeeded and not failed:
            lines.append("本日の未読 0 件。")
            return "\n".join(lines) + "\n"

        if succeeded:
            lines.append("== 目次 ==")
            for i, p in enumerate(succeeded, start=1):
                lines.append(f"{i}. {p.article.title}")
            lines.append("")

        for i, p in enumerate(succeeded, start=1):
            assert p.summary is not None  # guarded by Digest invariant
            type_label, priority_label = self._tag_label(p.summary)
            lines.append(f"== {i}. {p.article.title} ==")
            lines.append(f"URL: {p.article.url}")
            lines.append(f"タグ: {type_label} / 優先度: {priority_label}")
            lines.append("")
            lines.append("[3 行要約]")
            for s in p.summary.summary_lines:
                lines.append(f"- {s}")
            lines.append("")
            lines.append("[重要ポイント]")
            for k in p.summary.key_points:
                lines.append(f"- {k}")
            lines.append("")

        if failed:
            lines.append("== 処理失敗一覧 ==")
            for p in failed:
                title_or_url = p.article.title or p.article.url
                reason = p.error_reason or ""
                lines.append(f"- {title_or_url} ({p.article.url}) — {reason}")
            lines.append("")

        return "\n".join(lines)

    def _render_html(
        self,
        subject: str,
        target_date: str,
        succeeded: list[ProcessedArticle],
        failed: list[ProcessedArticle],
    ) -> str:
        def e(s: str) -> str:
            return html.escape(s)

        def attr(s: str) -> str:
            return html.escape(s, quote=True)

        parts: list[str] = []
        parts.append("<!doctype html>")
        parts.append('<html lang="ja"><head><meta charset="utf-8">')
        parts.append(f"<title>{e(subject)}</title></head><body>")
        parts.append(f"<h1>{e(target_date)} のダイジェスト</h1>")
        parts.append(f"<p>成功 {len(succeeded)} 件 / 失敗 {len(failed)} 件</p>")

        if not succeeded and not failed:
            parts.append("<p>本日の未読 0 件。</p>")
            parts.append("</body></html>")
            return "".join(parts)

        if succeeded:
            parts.append("<h2>目次</h2><ol>")
            for i, p in enumerate(succeeded, start=1):
                parts.append(f'<li><a href="#article-{i}">{e(p.article.title)}</a></li>')
            parts.append("</ol>")

        for i, p in enumerate(succeeded, start=1):
            assert p.summary is not None  # guarded by Digest invariant
            type_label, priority_label = self._tag_label(p.summary)
            parts.append(f'<section id="article-{i}">')
            parts.append(f'<h3><a href="{attr(p.article.url)}">{e(p.article.title)}</a></h3>')
            parts.append(f"<p>タグ: {e(type_label)} / 優先度: {e(priority_label)}</p>")
            parts.append("<h4>3 行要約</h4><ul>")
            for s in p.summary.summary_lines:
                parts.append(f"<li>{e(s)}</li>")
            parts.append("</ul>")
            parts.append("<h4>重要ポイント</h4><ul>")
            for k in p.summary.key_points:
                parts.append(f"<li>{e(k)}</li>")
            parts.append("</ul></section>")

        if failed:
            parts.append("<h2>処理失敗一覧</h2><ul>")
            for p in failed:
                title_or_url = p.article.title or p.article.url
                reason = p.error_reason or ""
                parts.append(
                    f'<li><a href="{attr(p.article.url)}">{e(title_or_url)}</a> — {e(reason)}</li>'
                )
            parts.append("</ul>")

        parts.append("</body></html>")
        return "".join(parts)

    def _build_per_article_subject(
        self, target_date: str, index: int, total: int, title: str
    ) -> str:
        return f"{self._subject_prefix} {target_date} ({index}/{total}) {title}"

    def _render_per_article_text(self, p: ProcessedArticle) -> str:
        assert p.summary is not None  # caller guarantee
        type_label, priority_label = self._tag_label(p.summary)
        lines: list[str] = []
        lines.append(p.article.title)
        lines.append(f"URL: {p.article.url}")
        lines.append(f"タグ: {type_label} / 優先度: {priority_label}")
        lines.append("")
        lines.append("[3 行要約]")
        for s in p.summary.summary_lines:
            lines.append(f"- {s}")
        lines.append("")
        lines.append("[重要ポイント]")
        for k in p.summary.key_points:
            lines.append(f"- {k}")
        return "\n".join(lines) + "\n"

    def _render_per_article_html(self, subject: str, p: ProcessedArticle) -> str:
        assert p.summary is not None  # caller guarantee

        def e(s: str) -> str:
            return html.escape(s)

        def attr(s: str) -> str:
            return html.escape(s, quote=True)

        type_label, priority_label = self._tag_label(p.summary)
        parts: list[str] = []
        parts.append("<!doctype html>")
        parts.append('<html lang="ja"><head><meta charset="utf-8">')
        parts.append(f"<title>{e(subject)}</title></head><body>")
        parts.append(f'<h1><a href="{attr(p.article.url)}">{e(p.article.title)}</a></h1>')
        parts.append(f"<p>タグ: {e(type_label)} / 優先度: {e(priority_label)}</p>")
        parts.append("<h2>3 行要約</h2><ul>")
        for s in p.summary.summary_lines:
            parts.append(f"<li>{e(s)}</li>")
        parts.append("</ul>")
        parts.append("<h2>重要ポイント</h2><ul>")
        for k in p.summary.key_points:
            parts.append(f"<li>{e(k)}</li>")
        parts.append("</ul></body></html>")
        return "".join(parts)

    def _render_failure_text(self, target_date: str, failed: list[ProcessedArticle]) -> str:
        lines: list[str] = []
        lines.append(f"{target_date} のダイジェスト — 処理失敗 {len(failed)} 件")
        lines.append("")
        for p in failed:
            title_or_url = p.article.title or p.article.url
            reason = p.error_reason or ""
            lines.append(f"- {title_or_url} ({p.article.url}) — {reason}")
        return "\n".join(lines) + "\n"

    def _render_failure_html(
        self, subject: str, target_date: str, failed: list[ProcessedArticle]
    ) -> str:
        def e(s: str) -> str:
            return html.escape(s)

        def attr(s: str) -> str:
            return html.escape(s, quote=True)

        parts: list[str] = []
        parts.append("<!doctype html>")
        parts.append('<html lang="ja"><head><meta charset="utf-8">')
        parts.append(f"<title>{e(subject)}</title></head><body>")
        parts.append(f"<h1>{e(target_date)} のダイジェスト — 処理失敗 {len(failed)} 件</h1>")
        parts.append("<ul>")
        for p in failed:
            title_or_url = p.article.title or p.article.url
            reason = p.error_reason or ""
            parts.append(
                f'<li><a href="{attr(p.article.url)}">{e(title_or_url)}</a> — {e(reason)}</li>'
            )
        parts.append("</ul></body></html>")
        return "".join(parts)
