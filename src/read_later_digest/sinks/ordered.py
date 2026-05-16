from __future__ import annotations

from digestkit.digester import ConfigurationError
from digestkit.protocols import Sink
from digestkit.types import Digest, Item


class OrderedSink:
    """Sequential fail-fast 版 CompositeSink.

    digestkit.sinks.composite.CompositeSink は失敗を蓄積するが、本 Sink は
    1 つ目の失敗で即座に例外を伝播し、後続 Sink をスキップする.

    read-later-digest の用途:
        OrderedSink([slack_block_kit_sink, notion_page_sink])
    Slack 失敗時に NotionPageSink を呼ばないことで「ページ本文追記済 / Status 未更新」
    の二重追記バグを防ぐ (項目 06 参照).
    """

    def __init__(self, sinks: list[Sink]) -> None:
        if not sinks:
            raise ConfigurationError("OrderedSink requires at least 1 sink")
        self._sinks = list(sinks)

    def write(self, digest: Digest, item: Item) -> None:
        for sink in self._sinks:
            sink.write(digest, item)  # raise → そのまま伝播、後続スキップ
