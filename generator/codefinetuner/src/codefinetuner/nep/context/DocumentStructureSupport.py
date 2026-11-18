"""Source-backed helpers for document context strategies.

Fragments can contain synthetic ancestor headings/tags; their byte range identifies
only the original selected unit, not those added wrappers.
"""
import json
import re
import tomllib
from xml.parsers import expat

from .ContextStructureParserAlgorithm import ContextFragment, ContextParseError


def pointer(parts):
    return ''.join('/' + str(p).replace('~', '~0').replace('/', '~1') for p in parts)


def fragment(path, fmt, kind, text, source, start, end, selector='', representation='extracted'):
    return ContextFragment(path, fmt, kind, text, selector, representation,
                           len(source[:start].encode('utf-8')), len(source[:end].encode('utf-8')))


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate key: {key}')
        result[key] = value
    return result


class JSONScanner:
    def __init__(self, path, source, fmt):
        self.path, self.source, self.fmt = path, source, fmt
        self.i = 0
        self.fragments = []

    def skip(self):
        s = self.source
        while self.i < len(s):
            if s[self.i].isspace():
                self.i += 1
            elif s.startswith('//', self.i):
                end = self.i + 2
                while end < len(s) and s[end] not in '\r\n\u2028\u2029':
                    end += 1
                self.i = end
            elif s.startswith('/*', self.i):
                end = s.find('*/', self.i + 2)
                if end < 0:
                    raise ValueError('unterminated comment')
                self.i = end + 2
            else:
                break

    def string(self):
        s, start = self.source, self.i
        quote = s[self.i]
        self.i += 1
        while self.i < len(s):
            if s[self.i] == '\\':
                self.i += 2
            elif s[self.i] == quote:
                self.i += 1
                return s[start:self.i]
            else:
                self.i += 1
        raise ValueError('unterminated string')

    def value(self, parts, start=None):
        self.skip()
        s = self.source
        begin = self.i if start is None else start
        if self.i >= len(s):
            raise ValueError('missing value')
        c = s[self.i]
        if c == '{':
            self.i += 1
            entry = self.i
            self.skip()
            while s[self.i] != '}':
                if s[self.i] in ('"', "'"):
                    raw = self.string()
                    if self.fmt == 'json5':
                        import json5
                        key = json5.loads(raw)
                    else:
                        key = json.loads(raw)
                else:
                    k = self.i
                    while self.i < len(s) and not s[self.i].isspace() and s[self.i] not in ':/':
                        self.i += 1
                    key = s[k:self.i]
                    if self.fmt == 'json5':
                        import json5
                        key = next(iter(json5.loads('{' + key + ':0}')))
                self.skip()
                if s[self.i] != ':':
                    raise ValueError('expected colon')
                self.i += 1
                self.value(parts + [key], entry)
                self.skip()
                if s[self.i] != ',':
                    break
                self.i += 1
                # Comments following a comma belong to the next entry.
                entry = self.i
                self.skip()
                if s[self.i] == '}':
                    break
                # The next entry starts before its leading comments.
            if s[self.i] != '}':
                raise ValueError('expected closing brace')
            self.i += 1
        elif c == '[':
            self.i += 1
            entry = self.i
            self.skip()
            index = 0
            while s[self.i] != ']':
                leading = s[entry:self.i]
                self.value(parts + [index], entry if '//' in leading or '/*' in leading else None)
                index += 1
                self.skip()
                if s[self.i] != ',':
                    break
                self.i += 1
                entry = self.i
                self.skip()
                if s[self.i] == ']':
                    break
            if s[self.i] != ']':
                raise ValueError('expected closing bracket')
            self.i += 1
        elif c in ('"', "'"):
            self.string()
        else:
            token_start = self.i
            while self.i < len(s) and not s[self.i].isspace() and s[self.i] not in ',]}/':
                self.i += 1
            if self.i == token_start:
                raise ValueError('expected value')
        end = self.i
        # Retain comments between a value and its delimiter, including a line
        # terminator so a synthetic closing delimiter is not swallowed by //.
        self.skip()
        trivia = s[end:self.i]
        if '//' in trivia or '/*' in trivia:
            end = self.i
        self.fragments.append(fragment(self.path, self.fmt, 'branch', s[begin:end], s,
                                       begin, end, pointer(parts)))


def mask_jsonc(source):
    chars = list(source)
    i = 0
    while i < len(source):
        if source[i] == '"':
            i += 1
            while i < len(source):
                if source[i] == '\\':
                    i += 2
                elif source[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
        elif source.startswith('//', i) or source.startswith('/*', i):
            line = source.startswith('//', i)
            if line:
                end = i + 2
                while end < len(source) and source[end] not in '\r\n':
                    end += 1
            else:
                end = source.find('*/', i + 2)
            if end < 0:
                if not line:
                    raise ValueError('unterminated comment')
                end = len(source)
            elif not line:
                end += 2
            for j in range(i, end):
                if chars[j] not in '\r\n':
                    chars[j] = ' '
            i = end
        else:
            i += 1
    return ''.join(chars)


def annotate_json(fragment, selector=None, fmt=None):
    selector = fragment.selector if selector is None else selector
    return ContextFragment(fragment.path, fmt or fragment.format, fragment.kind,
                           'JSON Pointer: ' + (json.dumps(selector, ensure_ascii=False)[1:-1] if selector else '"" (document root)') + '\n' + fragment.code_text,
                           selector, 'fragment_with_pointer', fragment.start_byte, fragment.end_byte)


def parse_json(path, source, fmt, *, annotate=True):
    try:
        if fmt == 'json5':
            import json5
            json5.loads(source, allow_duplicate_keys=False)
        else:
            json.loads(mask_jsonc(source) if fmt == 'jsonc' else source,
                       object_pairs_hook=unique_pairs,
                       parse_constant=lambda x: (_ for _ in ()).throw(ValueError('invalid constant ' + x)))
        scanner = JSONScanner(path, source, fmt)
        scanner.value([])
        scanner.skip()
        if scanner.i != len(source):
            raise ValueError('extra input')
        return [annotate_json(item) for item in scanner.fragments] if annotate else scanner.fragments
    except (ValueError, IndexError, RecursionError, ImportError) as exc:
        raise ContextParseError(f'Invalid or unsupported {fmt}: {exc}') from exc


def parse_jsonl(path, source):
    result = []
    offset = 0
    for number, line in enumerate(source.splitlines(keepends=True), 1):
        text = line.rstrip('\r\n')
        if not text.strip():
            raise ContextParseError(f'Invalid jsonl: blank line {number}')
        for item in parse_json(path, text, 'json', annotate=False):
            item = ContextFragment(path, 'jsonl', item.kind, item.code_text,
                                   '/' + str(number - 1) + item.selector,
                                   item.representation, len(source[:offset].encode()) + item.start_byte,
                                   len(source[:offset].encode()) + item.end_byte)
            result.append(annotate_json(item))
        offset += len(line)
    if not result:
        raise ContextParseError('Invalid jsonl: no records')
    return result


def parse_xml(path, source):
    try:
        data = source.encode('utf-8')
    except UnicodeError as exc:
        raise ContextParseError(f'Invalid xml encoding: {exc}') from exc
    parser = expat.ParserCreate('UTF-8')
    stack, nodes = [], []

    def tag_end(start):
        quote = None
        for i in range(start, len(data)):
            c = data[i]
            if quote:
                if c == quote:
                    quote = None
            elif c in (34, 39):
                quote = c
            elif c == 62:
                return i + 1
        raise ValueError('unterminated tag')

    def start(name, attrs):
        pos = parser.CurrentByteIndex
        end = tag_end(pos)
        siblings = stack[-1]['counts'] if stack else {}
        siblings[name] = siblings.get(name, 0) + 1
        node = {'name': name, 'start': pos, 'open_end': end, 'counts': {},
                'selector': (stack[-1]['selector'] if stack else '') + '/' + name + '[' + str(siblings[name]) + ']',
                'ancestors': list(stack)}
        stack.append(node)

    def end(name):
        node = stack.pop()
        pos = parser.CurrentByteIndex
        node['end'] = pos if data[node['start']:node['open_end']].rstrip().endswith(b'/>') else tag_end(pos)
        nodes.append(node)

    def unsafe(*args):
        raise ValueError('DTD and entity declarations are forbidden')

    def declaration(version, encoding, standalone):
        if encoding and encoding.lower().replace('_', '-') not in ('utf-8', 'utf8'):
            raise ValueError('XML source text must declare UTF-8 encoding')

    parser.XmlDeclHandler = declaration
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = unsafe
    parser.EntityDeclHandler = unsafe
    parser.ExternalEntityRefHandler = unsafe
    try:
        parser.Parse(data, True)
        result = []
        for node in nodes:
            ancestors = node['ancestors']
            prefix = b''.join(data[a['start']:a['open_end']] for a in ancestors)
            suffix = ''.join('</' + a['name'] + '>' for a in reversed(ancestors)).encode()
            text = (prefix + data[node['start']:node['end']] + suffix).decode('utf-8')
            result.append(ContextFragment(path, 'xml', 'element', text, node['selector'],
                                          'extracted', node['start'], node['end']))
        return result
    except (expat.ExpatError, ValueError, UnicodeError) as exc:
        raise ContextParseError(f'Invalid or unsafe xml: {exc}') from exc


def tree(path, source, language):
    try:
        from tree_sitter_language_pack import get_parser
        data = source.encode('utf-8')
        root = get_parser(language).parse(data).root_node
        if root.has_error:
            raise ValueError('syntax error')
        return data, root
    except (ValueError, LookupError, ImportError, UnicodeError) as exc:
        raise ContextParseError(f'Invalid or unsupported {language}: {exc}') from exc


def parse_html(path, source):
    if path.lower().endswith(('.jsx', '.tsx')):
        raise ContextParseError('JSX/TSX is not HTML')
    data, root = tree(path, source, 'html')
    result = []
    def visit(node, ancestors, names):
        if node.type in ('script_element', 'style_element'):
            return True
        if node.type != 'element':
            return any([visit(c, ancestors, names) for c in node.named_children])
        opening = next((c for c in node.named_children if c.type in ('start_tag', 'self_closing_tag')), None)
        if opening is None:
            raise ContextParseError('Invalid HTML element')
        tag = next((c for c in opening.named_children if c.type == 'tag_name'), None)
        name = data[tag.start_byte:tag.end_byte].decode() if tag else ''
        current = names + [name]
        unsafe = any([visit(c, ancestors + [(opening, name)], current)
                      for c in node.named_children if c.type not in ('start_tag', 'end_tag', 'self_closing_tag')])
        if not unsafe:
            prefix = b''.join(data[a.start_byte:a.end_byte] for a, _ in ancestors)
            suffix = ''.join('</' + n + '>' for _, n in reversed(ancestors)).encode()
            result.append(ContextFragment(path, 'html', 'element',
                (prefix + data[node.start_byte:node.end_byte] + suffix).decode(), pointer(current),
                'extracted', node.start_byte, node.end_byte))
        return unsafe
    visit(root, [], [])
    return result


def parse_toml(path, source):
    try:
        tomllib.loads(source)
        data, root = tree(path, source, 'toml')
        result = []
        array_indices = {}
        def key_parts(node):
            raw = data[node.start_byte:node.end_byte].decode()
            obj = tomllib.loads(raw + ' = 0')
            parts = []
            while isinstance(obj, dict):
                key = next(iter(obj))
                parts.append(key)
                obj = obj[key]
            return parts
        def visit(node, base, header=''):
            if node.type in ('table', 'table_array_element'):
                key = next(c for c in node.named_children if c.type in ('bare_key', 'quoted_key', 'dotted_key'))
                raw_parts = key_parts(key)
                line_end = data.find(b'\n', node.start_byte, node.end_byte)
                header = data[node.start_byte:line_end if line_end >= 0 else node.end_byte].decode().rstrip('\r') + '\n'
                base = []
                for index, part in enumerate(raw_parts):
                    base.append(part)
                    prefix = tuple(raw_parts[:index + 1])
                    if node.type == 'table_array_element' and index == len(raw_parts) - 1:
                        parent = tuple(base[:-1])
                        counter_key = (parent, prefix)
                        array_indices[counter_key] = array_indices.get(counter_key, -1) + 1
                    counter_key = (tuple(base[:-1]), prefix)
                    if counter_key in array_indices:
                        base.append(array_indices[counter_key])
                result.append(ContextFragment(path, 'toml', 'table', data[node.start_byte:node.end_byte].decode(),
                                              pointer(base), 'extracted', node.start_byte, node.end_byte))
            elif node.type == 'pair':
                key = node.named_children[0]
                result.append(ContextFragment(path, 'toml', 'value', header + data[node.start_byte:node.end_byte].decode(),
                                              pointer(base + key_parts(key)), 'extracted', node.start_byte, node.end_byte))
                return
            for child in node.named_children:
                visit(child, base, header)
        visit(root, [])
        return result
    except (ValueError, StopIteration, UnicodeError) as exc:
        raise ContextParseError(f'Invalid or unsupported toml: {exc}') from exc
