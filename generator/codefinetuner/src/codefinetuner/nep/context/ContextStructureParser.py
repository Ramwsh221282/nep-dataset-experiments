"""Strategy facade: extraction, selection, deduplication and atomic budgets."""
from functools import lru_cache
import re

from .ContextStructureParserAlgorithm import ContextParseError, ContextStructureParserAlgorithm


def identifiers(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[^\W\d]\w*", text, flags=re.UNICODE))


@lru_cache(maxsize=32)
def _extract(algorithm, path, source):
    return tuple(algorithm.parse(path, source))


class ContextStructureParser:
    def __init__(self, algorithm: ContextStructureParserAlgorithm):
        self.algorithm = algorithm

    def parse_range(self, path: str, source_text: str, start_byte: int, end_byte: int,
                    *, max_chars: int = 6000, selector=None):
        """Project exactly one original UTF-8 range, without a raw-source fallback."""
        from ..protocol import check_path, plain

        check_path(path)
        plain(source_text)
        if max_chars <= 0:
            raise ValueError("Context budget must be positive")
        source = source_text.encode('utf-8')
        if len(source) > 1_000_000:
            raise ContextParseError(f"Context source too large: {path}")
        if not isinstance(start_byte, int) or not isinstance(end_byte, int) or not 0 <= start_byte < end_byte <= len(source):
            raise ContextParseError(f"Invalid context range in {path}")
        try:
            source[:start_byte].decode('utf-8')
            source[:end_byte].decode('utf-8')
            if hasattr(self.algorithm, 'project_range'):
                candidates = self.algorithm.project_range(path, source_text, start_byte, end_byte)
            else:
                candidates = [f for f in _extract(self.algorithm, path, source_text)
                              if f.start_byte == start_byte and f.end_byte == end_byte]
            if selector is not None:
                candidates = [f for f in candidates if f.selector == selector]
            if len(candidates) != 1 or len(candidates[0].code_text) > max_chars:
                return []
            plain(candidates[0].code_text)
            return candidates
        except ContextParseError:
            raise
        except (LookupError, ImportError, TypeError, UnicodeError, RecursionError) as exc:
            raise ContextParseError(f"Cannot project context from {path}: {exc}") from exc

    def parse(self, path: str, source_text: str, *, selectors=(), query_terms=(),
              exclude_ranges=(), max_chars: int = 6000, max_fragments: int = 12):
        """Return selected candidates, never cutting a declaration or document unit.

        selectors address exact strategy-specific selectors (e.g. JSON Pointer).
        query_terms are a lexical ranking/filter, NOT semantic name resolution.
        exclude_ranges are original UTF-8 byte intervals (usually the edit window).
        An explicit selector that cannot be represented in budget fails closed.
        """
        from ..protocol import check_path, plain

        check_path(path)
        plain(source_text)
        if max_chars <= 0 or max_fragments <= 0:
            raise ValueError("Context budgets must be positive")
        if len(source_text.encode("utf-8")) > 1_000_000:
            raise ContextParseError(f"Context source too large: {path}")
        try:
            candidates = _extract(self.algorithm, path, source_text)
        except ContextParseError:
            raise
        except (LookupError, ImportError, TypeError, UnicodeError, RecursionError) as exc:
            raise ContextParseError(f"Cannot extract context from {path}: {exc}") from exc
        requested, terms = set(selectors), set(query_terms)
        eligible = []
        for fragment in candidates:
            if any(fragment.start_byte < end and start < fragment.end_byte
                   for start, end in exclude_ranges):
                continue
            if requested and fragment.selector not in requested:
                continue
            matches = terms & identifiers(fragment.selector + " " + fragment.code_text)
            if terms and not matches and not requested:
                continue
            plain(fragment.code_text)
            eligible.append((fragment, len(matches)))
        if requested - {fragment.selector for fragment, _ in eligible}:
            raise ContextParseError(f"Context selector not found in {path}: {sorted(requested)}")
        # Prefer specific, high-density matches to whole-document candidates.
        if terms:
            eligible.sort(key=lambda item: (-item[1] / max(1, len(identifiers(item[0].code_text))),
                                            -item[1], item[0].start_byte, item[0].end_byte))
        else:
            eligible.sort(key=lambda item: (item[0].end_byte - item[0].start_byte,
                                            item[0].start_byte))
        selected, seen, size = [], set(), 0
        for fragment, _ in eligible:
            key = (fragment.path, fragment.code_text)
            if key in seen:
                continue
            # Do not repeat a nested section already represented by an ancestor.
            if any(fragment.start_byte < f.end_byte and f.start_byte < fragment.end_byte for f in selected):
                continue
            if len(selected) >= max_fragments or size + len(fragment.code_text) > max_chars:
                continue
            selected.append(fragment)
            size += len(fragment.code_text)
            seen.add(key)
        if requested - {f.selector for f in selected}:
            raise ContextParseError(f"Selected context exceeds budget or overlaps in {path}")
        return sorted(selected, key=lambda f: (f.start_byte, f.end_byte, f.selector))
