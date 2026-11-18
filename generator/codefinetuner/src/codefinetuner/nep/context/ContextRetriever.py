"""Bounded structural retrieval. No repository-wide name fallback, LSP or I/O."""
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import chain

from .bindings import can_reference, resolve_reference

from .document_links import resolve_link
from .RepositorySymbolIndex import is_code


@dataclass(frozen=True)
class Candidate:
    path: str
    start: int
    end: int
    reason: str
    origin: str
    name: str = ''
    owner: str = ''
    scope: tuple = ()
    selector: str | None = None

    @classmethod
    def symbol(cls, d, reason, origin):
        return cls(d.path, d.start, d.end, reason, origin, d.name, d.owner, d.scope)


def _change(before, after):
    """Smallest byte interval covering the change in each revision."""
    a, b = before.encode(), after.encode()
    left, right, new_right = 0, len(a), len(b)
    while left < min(right, new_right) and a[left] == b[left]:
        left += 1
    while right > left and new_right > left and a[right - 1] == b[new_right - 1]:
        right -= 1
        new_right -= 1
    return left, right, new_right


def _hunks(before, after):
    # Trim the common prefix/suffix first. Split separated line edits rather
    # than treating all declarations between two hunks as changed evidence.
    lo, end, new_end = _change(before, after)
    a = before.encode()[lo:end].splitlines(keepends=True)
    b = after.encode()[lo:new_end].splitlines(keepends=True)
    if len(a) + len(b) > 2048:
        return []  # mass rewrite: do not infer a rename from unrelated names
    def boundaries(lines):
        offsets = [lo]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        return offsets
    old, new = boundaries(a), boundaries(b)
    return [(old[i], old[j], new[k], new[l]) for tag, i, j, k, l in
            SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if tag != 'equal']


def _identity(d):
    return (d.name, d.owner, d.kind, tuple(s.rsplit('@', 1)[0] for s in d.scope))


def _touched(facts, left, right):
    # Use named declarations, not arbitrary enclosing namespaces/classes when a
    # method/parameter inside has changed. Avoid guessing across a multi-hunk diff.
    named = [d for d in facts.definitions if left < d.name_end and d.name_start < right]
    enclosing = [d for d in facts.definitions if d.start <= left and right <= d.end
                 and d.kind in ('function', 'method')]
    # Parameter changes also change the enclosing function's contract.
    return list(dict.fromkeys(named + sorted(enclosing, key=lambda d: d.end - d.start)[:1]))


def _history_seeds(view, history):
    """Read both sides of completed steps; all returned ranges are in S_t."""
    changes, refs, links = [], [], []
    # Anchor old seeds only when all later edits in their file are disjoint
    # from the declaration. Never attach a deleted API to a namesake that a
    # developer subsequently created in the same lexical scope.
    later = {}
    for step in reversed(history[-6:]):
        subsequent = later.get(step.path, [])
        later[step.path] = [step, *subsequent]
        hunks = _hunks(step.before, step.after)
        if not is_code(step.path):
            facts = view.document(step.path)
            if view.files[step.path] == step.after:
                links.extend((step.path, link.uri) for link in facts.links
                             if any(link.start_byte <= end and start <= link.end_byte
                                                                 for _, _, start, end in hunks))
            continue
        past = view.index.revision(step.path, step.before)
        then = view.index.revision(step.path, step.after)
        seeds = [(d, _touched(past, a, b)) for a, b, c, e in hunks
                 for d in _touched(then, c, e)]
        for d, old in seeds:
            # Later steps may have moved, renamed or removed this definition.
            start, end = d.start, d.end
            for following in subsequent:
                a, b, new_b = _change(following.before, following.after)
                if b <= start:
                    start, end = start + new_b - b, end + new_b - b
                elif a < end:
                    break
            else:
                current = [x for x in view.code(step.path).definitions
                           if _identity(x) == _identity(d) and (x.start, x.end) == (start, end)]
                if len(current) == 1:
                    related = [x for x in old if _identity(x)[1:] == _identity(d)[1:]]
                    old_name = related[0].name if len(related) == 1 else d.name
                    changes.append((old_name, current[0]))
        if view.files[step.path] == step.after:
            refs.extend((step.path, r) for r in then.references
                        if any(r.start <= end and start <= r.end for _, _, start, end in hunks))
    return changes, refs, links


def _nearby(refs, source, cursor):
    data = source.encode()
    line_start = data.rfind(b'\n', 0, cursor) + 1
    line_end = data.find(b'\n', cursor)
    if line_end < 0:
        line_end = len(data)
    # Include the cursor line and its immediate neighbours for whitespace cursors.
    lo = data.rfind(b'\n', 0, max(0, line_start - 1)) + 1
    hi = data.find(b'\n', line_end + 1)
    if hi < 0:
        hi = len(data)
    return sorted((r for r in refs if lo <= r.start <= hi),
                  key=lambda r: (max(r.start - cursor, cursor - r.end, 0),
                                 not bool(r.receiver), r.start))[:16]


class ContextRetriever:
    def candidates(self, view, path, cursor_byte, history):
        found, diagnostics = [], []
        changes, historical_refs, links = _history_seeds(view, history)
        if is_code(path):
            facts = view.code(path)
            nearby = _nearby(facts.references, view.files[path], cursor_byte)
            if facts.partial:
                diagnostics.append({'path': path, 'reason': 'partial_syntax_tree'})
        else:
            nearby = []
            links.extend((path, link.uri) for link in view.document(path).links
                         if link.start_byte <= cursor_byte <= link.end_byte)

        for origin, (ref_path, ref) in chain(
                (('cursor', (path, r)) for r in nearby),
                (('history', pair) for pair in historical_refs[:16])):
            if ref.receiver == '@document':
                links.append((ref_path, ref.name))
                continue
            resolution = resolve_reference(view, ref_path, ref)
            if resolution.ambiguous:
                diagnostics.append({'path': ref_path, 'symbol': ref.name, 'reason': 'ambiguous_binding'})
                continue
            if resolution.matches:
                found.extend(Candidate.symbol(d, resolution.reason, origin) for d in resolution.matches)
                continue
            # Prefix expansion is allowed only at the actual cursor and only
            # when a receiver binding yields one member (repo.getU, not all get*).
            if origin == 'cursor' and ref.receiver and ref.start <= cursor_byte <= ref.end:
                partial = resolve_reference(view, ref_path, ref, prefix=True)
                if partial.matches:
                    found.extend(Candidate.symbol(d, partial.reason + '_prefix', origin) for d in partial.matches)
                    continue
                if partial.ambiguous:
                    diagnostics.append({'path': path, 'symbol': ref.name, 'reason': 'ambiguous_prefix'})
                    continue
            matches = {d for old_name, d in changes if can_reference(view, ref_path, ref, d, old_name)}
            # Only a specifically changed declaration can be evidence in the
            # absence of a resolvable binding. Never search all files by name.
            if not matches and not resolution.blocked and not ref.receiver:
                matches = {d for old_name, d in changes if not d.scope and not d.owner
                           and old_name == ref.name and d.kind in ('function', 'type')}
            if len(matches) == 1:
                found.append(Candidate.symbol(next(iter(matches)), 'history_change', origin))
        # A concrete recent API edit is a second independent seed, even if
        # the cursor moved away. No lookup by its unqualified name in other files.
        for _, d in changes:
            if d.kind in ('function', 'method', 'type', 'field'):
                found.append(Candidate.symbol(d, 'history_change', 'history'))
        for source_path, uri in dict.fromkeys(links):
            for target, selector, start, end, reason in resolve_link(source_path, uri, view.files, view.document):
                found.append(Candidate(target, start, end, reason, 'explicit_link', selector=selector))
        # If both the owner class and its precise member were found, the member
        # projection already includes the owner header. Don't send every sibling.
        found = [c for c in found if not any(other.path == c.path and other.owner
                                             and c.start <= other.start and other.end <= c.end
                                             and (other.start, other.end) != (c.start, c.end)
                                             for other in found)]
        rank = {'local_binding': 0, 'explicit_import': 1, 'module_path': 1,
                'receiver_type': 1, 'receiver_type_prefix': 2, 'document-link': 1, 'history_change': 3}
        return sorted(found, key=lambda c: (rank.get(c.reason, 2), c.origin == 'history')), diagnostics
