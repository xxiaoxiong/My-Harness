"""Lossy history summarization contracts for HARN-07 compaction."""

from __future__ import annotations

from abc import ABC, abstractmethod

from harness.model import ModelMessage

SUMMARY_TRUNCATION_MARKER = "[summary truncated]"


class HistorySummarizer(ABC):
    """Create a bounded representation of history omitted from context."""

    @abstractmethod
    def summarize(
        self,
        messages: tuple[ModelMessage, ...],
        *,
        max_chars: int,
    ) -> str:
        """Summarize messages into at most ``max_chars`` characters."""


class SimpleHistorySummarizer(HistorySummarizer):
    """Deterministically compact role-prefixed text without another LLM call."""

    def summarize(
        self,
        messages: tuple[ModelMessage, ...],
        *,
        max_chars: int,
    ) -> str:
        if max_chars <= 0 or not messages:
            return ""

        summary = " | ".join(
            f"{message.role.value}: {' '.join(message.content.split())}"
            for message in messages
        )
        if len(summary) <= max_chars:
            return summary
        if max_chars <= len(SUMMARY_TRUNCATION_MARKER):
            return SUMMARY_TRUNCATION_MARKER[:max_chars]
        return (
            summary[: max_chars - len(SUMMARY_TRUNCATION_MARKER)]
            + SUMMARY_TRUNCATION_MARKER
        )
