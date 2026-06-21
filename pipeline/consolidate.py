"""Consolidation logic — compress staging files into recent and archive memory.

This is the pipeline stage that runs overnight (or on demand) to merge
daily staging files into the two long-lived memory files:

- **recent.md** — active, high-relevance entries from the last few days.
- **archive.md** — older entries, compressed and deduplicated.

The flow is: shell collects file contents -> this module builds the prompt
and calls Haiku -> Haiku returns a structured response with ``===RECENT===``
and ``===ARCHIVE===`` delimiters -> this module parses and returns the
sections -> shell writes the output files.

Typical usage::

    from pipeline.consolidate import consolidate
    result = consolidate(staging_contents, recent_text, archive_text)
    # result.recent  -> new content for recent.md
    # result.archive -> new content for archive.md
"""

from __future__ import annotations

from .prompts import build_consolidation_prompt
from .llm import call_haiku
from .types import ConsolidationResult, TokenUsage


class ConsolidationSkipped(Exception):
    """Raised when the LLM returns a SKIP or non-conforming consolidation
    response. The caller MUST NOT write output or retire the staging files —
    staging is preserved untouched and retried on the next session start.

    Prevents the corruption class where a conversational reply, refusal, or
    SKIP was written verbatim into recent.md/archive.md while the source
    staging files were irreversibly renamed to .done.md (upstream #89).
    """


def consolidate(
    staging_contents: dict[str, str],
    recent: str,
    archive: str,
) -> ConsolidationResult:
    """Run full consolidation: build prompt, call Haiku, parse response.

    Args:
        staging_contents: Mapping of ``{filename: content}`` for each
            staging file to consolidate.
        recent: Current content of recent.md (may be empty).
        archive: Current content of archive.md (may be empty).

    Returns:
        ConsolidationResult with new recent/archive content and token usage.

    Raises:
        RuntimeError: If the Haiku call fails or times out.
    """
    prompt = build_consolidation_prompt(staging_contents, recent, archive)
    result = call_haiku(prompt, timeout=180, max_output_tokens=2000)

    recent_new, archive_new = parse_consolidation_response(result.text)

    return ConsolidationResult(
        recent=recent_new,
        archive=archive_new,
        tokens=result.tokens,
    )


def parse_consolidation_response(text: str) -> tuple[str, str]:
    """Parse the LLM's structured response into recent and archive sections.

    Splits on the ``===RECENT===`` and ``===ARCHIVE===`` delimiters. The
    ``===RECENT===`` delimiter is REQUIRED — a response that is empty, a
    ``SKIP``, or missing that delimiter is treated as non-conforming and
    raises :class:`ConsolidationSkipped`, so the caller leaves the staging
    files untouched rather than writing garbage into recent.md/archive.md
    and irreversibly retiring the source files (upstream #89). The archive
    delimiter is optional (early days have nothing to rotate yet).

    Expected format::

        ===RECENT===
        # Recent
        ...content...

        ===ARCHIVE===
        # Archive
        ...content...

    Args:
        text: Raw text response from the LLM.

    Returns:
        Tuple of ``(recent_content, archive_content)``, both stripped
        and with headers ensured. Archive may be empty if the model
        did not produce an archive section.

    Raises:
        ConsolidationSkipped: If the response is empty, a SKIP, or does not
            contain the required ``===RECENT===`` delimiter.
    """
    stripped = text.strip()
    if not stripped or stripped.upper().startswith("SKIP"):
        raise ConsolidationSkipped("LLM returned an empty or SKIP response")
    if "===RECENT===" not in text:
        raise ConsolidationSkipped(
            "LLM response missing the required ===RECENT=== delimiter"
        )

    recent = ""
    archive = ""

    if "===ARCHIVE===" in text:
        parts = text.split("===ARCHIVE===", 1)
        recent = parts[0].replace("===RECENT===", "").strip()
        archive = parts[1].strip()
    else:
        recent = text.replace("===RECENT===", "").strip()

    # Ensure headers are present
    if recent and not recent.startswith("# Recent"):
        recent = "# Recent\n\n" + recent
    if archive and not archive.startswith("# Archive"):
        archive = "# Archive\n\n" + archive

    return recent, archive
