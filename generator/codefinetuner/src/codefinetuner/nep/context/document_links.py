"""Explicit, snapshot-local document links and source-backed structural targets.

All offsets are half-open UTF-8 byte offsets. Resolution returns five-tuples
(path, parser_selector, start_byte, end_byte, reason). A nonnegative range is
always supplied, and is required to disambiguate repeated parser selectors.
No root/whole-document fallback or lexical search is performed.
"""
from collections import Counter
from dataclasses import dataclass
from html import unescape
from pathlib import PurePosixPath
import json
import posixpath
import re
from urllib.parse import unquote, urlsplit
from xml.parsers import expat

from .ContextStructureParserAlgorithm import ContextParseError
from .ContextStructureParserResolution import ContextStructureParserResolution
from .DocumentStructureSupport import JSONScanner, tree


_EXTENSIONS = {'.md', '.markdown', '.html', '.htm', '.xml', '.svg',
               '.json', '.jsonc', '.json5', '.jsonl', '.ndjson', '.toml'}
_MAX_BYTES = 32_000
_MAX_ITEMS = 512


@dataclass(frozen=True)
class DocumentLink:
    start_byte: int
    end_byte: int
    uri: str
    kind: str


@dataclass(frozen=True)
class DocumentTarget:
    selector: str
    start_byte: int
    end_byte: int


@dataclass(frozen=True)
class DocumentFacts:
    links: tuple[DocumentLink, ...] = ()
    targets: tuple[tuple[str, DocumentTarget], ...] = ()
    units: tuple[DocumentTarget, ...] = ()


def _slug(title):
    title = re.sub(r'<[^>]*>', '', title).lower()
    title = re.sub(r'[^\w\-\s]', '', title, flags=re.UNICODE)
    return re.sub(r'\s', '-', title)


def _markdown(source, units):
    links, targets, definitions = [], [], {}
    offsets = [0]
    for char in source:
        offsets.append(offsets[-1] + len(char.encode('utf-8')))
    lines = source.splitlines(keepends=True)
    front_end = 0
    if lines and lines[0].strip() == '---':
        for index in range(1, len(lines)):
            if lines[index].strip() in ('---', '...'):
                front_end = sum(len(line) for line in lines[:index + 1])
                break
    usable = []
    fence = None
    offset = 0
    for line in lines:
        if offset < front_end:
            offset += len(line)
            continue
        fence_match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence) and not fence_match.group(2).strip():
                fence = None
        elif fence_match:
            fence = fence_match.group(1)
        else:
            # Mask inline code without changing positions.
            masked = list(line)
            for m in re.finditer(r'(`+).*?\1', line):
                masked[m.start():m.end()] = ' ' * (m.end() - m.start())
            visible = ''.join(masked)
            definition = re.match(r'^ {0,3}\[([^]\n]+)\]:[ \t]*(?:<([^>\n]+)>|([^\s]+))', visible)
            if definition:
                label = ' '.join(definition.group(1).split()).casefold()
                group = 2 if definition.group(2) is not None else 3
                uri = definition.group(group)
                definitions.setdefault(label, uri)
                links.append(DocumentLink(offsets[offset + definition.start(group)], offsets[offset + definition.end(group)], uri, 'markdown-reference-definition'))
            else:
                usable.append((offset, visible))
        offset += len(line)
    inline = re.compile(r'(?<!\\)!?\[([^]\n]+)\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+[^)]*)?\)')
    reference = re.compile(r'(?<!\\)!?\[([^]\n]+)\](?:\[([^]\n]*)\]|(?![\[(]))')
    for offset, line in usable:
        occupied = []
        for m in inline.finditer(line):
            group = 2 if m.group(2) is not None else 3
            links.append(DocumentLink(offsets[offset + m.start()], offsets[offset + m.end()], m.group(group), 'markdown-link'))
            occupied.append((m.start(), m.end()))
        for m in reference.finditer(line):
            if any(a <= m.start() < b for a, b in occupied):
                continue
            label = ' '.join((m.group(2) or m.group(1)).split()).casefold()
            if label in definitions:
                links.append(DocumentLink(offsets[offset + m.start()], offsets[offset + m.end()], definitions[label], 'markdown-reference'))
    counts = Counter()
    for unit in sorted(units, key=lambda x: x.start_byte):
        if not unit.selector or unit.selector == '/front-matter':
            continue
        title = unit.selector.rsplit('/', 1)[-1].replace('~1', '/').replace('~0', '~')
        base = _slug(title)
        number = counts[base]
        counts[base] += 1
        targets.append((base + ('-' + str(number) if number else ''), unit))
    return links, targets


def _markup(path, source, units):
    data = source.encode('utf-8')
    links, targets = [], []
    by_start = {unit.start_byte: unit for unit in units}
    attr_re = re.compile(rb'''([^\s=<>/]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))''')
    if path.lower().endswith(('.xml', '.svg')):
        parser = expat.ParserCreate('UTF-8')
        def start_element(name, attrs):
            pos = parser.CurrentByteIndex
            # Find the end of the opening tag outside attribute quotes.
            quote = None
            end = pos
            while end < len(data):
                c = data[end]
                if quote == c:
                    quote = None
                elif quote is None and c in (34, 39):
                    quote = c
                elif quote is None and c == 62:
                    break
                end += 1
            for match in attr_re.finditer(data[pos:end]):
                key = match.group(1).decode().lower()
                group = next(i for i in (2, 3, 4) if match.group(i) is not None)
                value = attrs.get(key)
                if value is None:
                    continue
                if key in ('href', 'src', 'xlink:href'):
                    links.append(DocumentLink(pos + match.start(group), pos + match.end(group), value, key))
                if key in ('id', 'xml:id') and pos in by_start:
                    targets.append((value, by_start[pos]))
        parser.StartElementHandler = start_element
        parser.Parse(data, True)
        return links, targets
    _, root = tree(path, source, 'html')
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in ('script_element', 'style_element'):
            continue
        stack.extend(reversed(node.named_children))
        if node.type not in ('element', 'STag', 'EmptyElemTag'):
            continue
        if node.type == 'element':
            opening = next((c for c in node.named_children if c.type in ('start_tag', 'self_closing_tag', 'STag', 'EmptyElemTag')), None)
            if opening is None:
                continue
            start = node.start_byte
        else:
            opening, start = node, node.start_byte
            # XML grammars may expose the opening tag under an element.
            if node.parent and node.parent.type == 'element':
                continue
        unit = by_start.get(start)
        raw = data[opening.start_byte:opening.end_byte]
        for match in attr_re.finditer(raw):
            name = match.group(1).decode('utf-8').lower()
            group = next(i for i in (2, 3, 4) if match.group(i) is not None)
            value = unescape(match.group(group).decode('utf-8'))
            if name in ('href', 'src', 'xlink:href'):
                links.append(DocumentLink(opening.start_byte + match.start(group), opening.start_byte + match.end(group), value, name))
            if name in ('id', 'xml:id') and unit:
                targets.append((value, unit))
    return links, targets


def _data_links(path, source, units):
    data = source.encode('utf-8')
    links = []
    fmt = PurePosixPath(path).suffix.lower()
    for unit in units:
        key = unit.selector.rsplit('/', 1)[-1].replace('~1', '/').replace('~0', '~')
        if key != '$ref':
            continue
        raw = data[unit.start_byte:unit.end_byte]
        # Inspect only the actual member, never a lookalike in leading comments.
        try:
            text = raw.decode('utf-8')
            if fmt == '.toml':
                match = re.match(r'''\s*["']\$ref["']\s*=\s*(["'])(.*)\1\s*$''', text)
                if not match:
                    continue
                import tomllib
                value = tomllib.loads('value = ' + match.group(1) + match.group(2) + match.group(1))['value']
                start, end = match.start(2), match.end(2)
            else:
                scanner = JSONScanner(path, text, 'json5' if fmt == '.json5' else 'json')
                scanner.skip()
                if text[scanner.i] in ('"', "'"):
                    key = scanner.string()
                    if fmt == '.json5':
                        import json5
                        key = json5.loads(key)
                    else:
                        key = json.loads(key)
                else:
                    match = re.match(r'\$ref\b', text[scanner.i:])
                    if not match:
                        continue
                    key = '$ref'
                    scanner.i += len(match.group())
                if key != '$ref':
                    continue
                scanner.skip()
                if text[scanner.i] != ':':
                    continue
                scanner.i += 1
                scanner.skip()
                if text[scanner.i] not in ('"', "'"):
                    continue
                start = scanner.i + 1
                literal = scanner.string()
                end = scanner.i - 1
                if fmt == '.json5':
                    import json5
                    value = json5.loads(literal)
                else:
                    value = json.loads(literal)
            if isinstance(value, str):
                start = unit.start_byte + len(text[:start].encode('utf-8'))
                end = unit.start_byte + len(text[:end].encode('utf-8'))
                links.append(DocumentLink(start, end, value, '$ref'))
        except (ValueError, UnicodeError, ImportError, IndexError):
            continue
    return links


def analyze_document(path, source):
    """Analyze supported text; invalid, oversized and unsupported documents yield empty facts."""
    if PurePosixPath(path).suffix.lower() not in _EXTENSIONS or not isinstance(source, str):
        return DocumentFacts()
    try:
        data = source.encode('utf-8')
        if len(data) > _MAX_BYTES or sum(data.count(bytes([c])) for c in b'{[<\n,') > _MAX_ITEMS:
            return DocumentFacts()
        parser = ContextStructureParserResolution.resolve(path).algorithm
        fragments = parser.parse(path, source)
        if len(fragments) > _MAX_ITEMS:
            return DocumentFacts()
        units = tuple(DocumentTarget(f.selector, f.start_byte, f.end_byte) for f in fragments)
        ext = PurePosixPath(path).suffix.lower()
        if ext in ('.md', '.markdown'):
            links, targets = _markdown(source, units)
        elif ext in ('.html', '.htm', '.xml', '.svg'):
            links, targets = _markup(path, source, units)
        else:
            links = _data_links(path, source, units)
            targets = [(unit.selector, unit) for unit in units if unit.selector]
        if len(links) + len(targets) > _MAX_ITEMS:
            return DocumentFacts()
        return DocumentFacts(tuple(links), tuple(targets), units)
    except (ContextParseError, ValueError, UnicodeError, RecursionError, IndexError, TypeError, LookupError, ImportError):
        return DocumentFacts()


def _resolve_path(source_path, raw_uri, known_paths):
    if not isinstance(raw_uri, str) or not raw_uri or any(ord(c) < 32 for c in raw_uri):
        return None
    try:
        uri = urlsplit(raw_uri)
        if uri.scheme or uri.netloc or uri.query:
            return None
        relative = unquote(uri.path, errors='strict')
        fragment = unquote(uri.fragment, errors='strict')
    except (ValueError, UnicodeError):
        return None
    if '\\' in relative or relative.startswith('/') or '\x00' in relative or '\x00' in fragment:
        return None
    if relative:
        parts = posixpath.dirname(source_path).split('/') if posixpath.dirname(source_path) else []
        for part in relative.split('/'):
            if part == '..':
                if not parts:
                    return None
                parts.pop()
            elif part and part != '.':
                parts.append(part)
        path = '/'.join(parts)
        # Code often spells snapshot-root-relative paths rather than relative URLs.
        if path not in known_paths and not relative.startswith(('.', '/')) and relative in known_paths:
            path = relative
    else:
        path = source_path
    if path in ('', '.', '..') or path.startswith(('../', '/')):
        return None
    if path not in known_paths or PurePosixPath(path).suffix.lower() not in _EXTENSIONS:
        return None
    return path, fragment


def resolve_link(source_path, raw_uri, known_paths, get_facts):
    """Resolve an explicit URI against a snapshot. Never accesses the filesystem.

    Relative paths are relative to source_path; empty fragments do not select a
    document root. A bare file is usable only if it has one non-root unit.
    """
    resolved = _resolve_path(source_path, raw_uri, known_paths)
    if resolved is None:
        return []
    path, fragment = resolved
    facts = get_facts(path)
    if not isinstance(facts, DocumentFacts):
        return []
    if fragment:
        matches = [unit for key, unit in facts.targets if key == fragment]
    else:
        # A file-only reference must identify one structural unit, not a root.
        matches = [unit for unit in facts.units if unit.selector]
        if len(matches) != 1 or matches[0].start_byte == 0:
            return []
    if len(matches) != 1:
        return []
    unit = matches[0]
    return [(path, unit.selector, unit.start_byte, unit.end_byte, 'document-link')]


def resolve_document_links(path, facts, cursor_byte, known_paths, get_facts):
    """Resolve links containing cursor_byte (half-open, in UTF-8 bytes)."""
    if not isinstance(facts, DocumentFacts) or not isinstance(cursor_byte, int):
        return []
    result = []
    for link in facts.links:
        if link.start_byte <= cursor_byte < link.end_byte:
            for target in resolve_link(path, link.uri, known_paths, get_facts):
                item = (*target[:4], link.kind)
                if item not in result:
                    result.append(item)
    return result
