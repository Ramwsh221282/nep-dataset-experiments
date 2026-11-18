"""Markdown sections with heading ancestry and source-backed spans."""
import re

from .ContextStructureParserAlgorithm import ContextStructureParserAlgorithm, ContextParseError
from .DocumentStructureSupport import fragment, pointer


def _label(value):
    return ' '.join(value.split()).casefold()


def _used_references(body, definitions):
    """Recognize full, collapsed and shortcut references outside code blocks."""
    used = set()
    fence = None
    for line in body.splitlines():
        match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) and not match.group(2).strip():
                fence = None
            continue
        if match:
            fence = match.group(1)
            continue
        if re.match(r'^ {0,3}\[[^]]+\]:[ \t]*\S', line):
            continue
        line = re.sub(r'(`+).*?\1', '', line)
        pattern = r'(?<!\\)\[([^\]\n]+)\](?:\[([^\]\n]*)\]|(?![\[(]))'
        for match in re.finditer(pattern, line):
            label = _label(match.group(2) or match.group(1))
            if label in definitions:
                used.add(label)
    return used


class MarkdownContextStructureParser(ContextStructureParserAlgorithm):
    def parse(self, path, source_text):
        try:
            lines = source_text.splitlines(keepends=True)
            headings = []
            references = {}
            fence = None
            offset = 0
            front_end = 0
            if lines and lines[0].strip() == '---':
                for i in range(1, len(lines)):
                    if lines[i].strip() in ('---', '...'):
                        front_end = sum(map(len, lines[:i + 1]))
                        break
            previous = None
            for line in lines:
                stripped = line.rstrip('\r\n')
                if offset < front_end:
                    offset += len(line)
                    continue
                match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', stripped)
                if fence:
                    if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) and not match.group(2).strip():
                        fence = None
                    previous = None
                elif match:
                    fence = match.group(1)
                    previous = None
                else:
                    h = re.match(r'^ {0,3}(#{1,6})(?:[ \t]+|$)(.*)$', stripped)
                    setext = re.match(r'^ {0,3}(=+|-+)[ \t]*$', stripped)
                    if h:
                        title = re.sub(r'[ \t]+#+[ \t]*$', '', h.group(2)).strip()
                        headings.append((offset, len(h.group(1)), title, line))
                    elif setext and previous and previous[1].strip():
                        headings.append((previous[0], 1 if setext.group(1)[0] == '=' else 2,
                                         previous[1].strip(), previous[1] + line))
                    definition = re.match(r'^ {0,3}\[([^]]+)\]:[ \t]*\S', stripped)
                    if definition:
                        references.setdefault(_label(definition.group(1)), line)
                    previous = (offset, line) if stripped.strip() else None
                offset += len(line)
            result = []
            if front_end:
                result.append(fragment(path, 'markdown', 'front_matter', source_text[:front_end],
                                       source_text, 0, front_end, '/front-matter'))
            if not headings:
                if source_text[front_end:]:
                    result.append(fragment(path, 'markdown', 'section', source_text[front_end:],
                                           source_text, front_end, len(source_text), ''))
                return result
            if headings[0][0] > front_end and source_text[front_end:headings[0][0]].strip():
                result.append(fragment(path, 'markdown', 'section', source_text[front_end:headings[0][0]],
                                       source_text, front_end, headings[0][0], ''))
            ancestors = []
            for index, (start, level, title, heading) in enumerate(headings):
                while ancestors and ancestors[-1][0] >= level:
                    ancestors.pop()
                end = headings[index + 1][0] if index + 1 < len(headings) else len(source_text)
                body = source_text[start:end]
                prefix = ''.join(item[2] for item in ancestors)
                # Include only definitions actually used in this section.
                used = _used_references(body, references)
                missing = ''.join(line for label, line in references.items()
                                  if label in used and line not in body)
                text = prefix + body + ('\n' + missing if missing else '')
                names = [item[1] for item in ancestors] + [title]
                result.append(fragment(path, 'markdown', 'section', text, source_text, start, end, pointer(names)))
                ancestors.append((level, title, heading))
            return result
        except (UnicodeError, ValueError) as exc:
            raise ContextParseError(f'Invalid markdown: {exc}') from exc
