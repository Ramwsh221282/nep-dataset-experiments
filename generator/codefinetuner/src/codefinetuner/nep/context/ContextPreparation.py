"""Project only structurally resolved ranges from S_t; never read target_after."""
from .ContextRetriever import ContextRetriever
from .ContextStructureParserAlgorithm import ContextParseError
from .ContextStructureParserResolution import ContextStructureParserResolution


class ContextPreparation:
    def prepare(self, view, target_path, cursor_byte, history, start_byte, end_byte, *,
                required_paths=(), max_chars=6000):
        retrieval, current, seen = [], [], set()
        candidates, diagnostics = ContextRetriever().candidates(view, target_path, cursor_byte, history)
        remaining = max_chars
        required = set(required_paths) - {target_path}
        # Reserve priority for the declaration which just changed in another file.
        candidates.sort(key=lambda c: c.path not in required)
        for c in candidates:
            if c.path == target_path and c.start < end_byte and start_byte < c.end:
                continue
            if remaining <= 0 or len(current) + len(retrieval) >= 12:
                break
            if (c.path, c.start, c.end) in seen:
                continue
            seen.add((c.path, c.start, c.end))
            parser = ContextStructureParserResolution.resolve(c.path)
            try:
                fragments = parser.parse_range(c.path, view.files[c.path], c.start, c.end,
                                               max_chars=remaining, selector=c.selector)
            except ContextParseError:
                fragments = []
            if not fragments:
                diagnostics.append({'path': c.path, 'reason': 'range_unavailable',
                                    'start_byte': c.start, 'end_byte': c.end})
                continue
            for fragment in fragments:
                item = dict(fragment.to_dict(), reason=c.reason, origin=c.origin, symbol=c.name,
                            owner=c.owner, scope=list(c.scope))
                (current if c.path == target_path else retrieval).append(item)
                remaining -= len(fragment.code_text)
        if required - {f['path'] for f in retrieval}:
            raise ContextParseError('Required history context could not be resolved within budget')
        return retrieval, current, diagnostics
