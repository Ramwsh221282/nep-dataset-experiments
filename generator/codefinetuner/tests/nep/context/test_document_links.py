import pytest

from codefinetuner.nep.context.document_links import (
    DocumentFacts, analyze_document, resolve_document_links, resolve_link,
)


def snapshot(files):
    facts = {path: analyze_document(path, source) for path, source in files.items()}
    return facts, facts.get


def test_markdown_links_unicode_and_references():
    files = {'docs/a.md': '# Start\n é [go][Ref] and [direct](b.md#hello-world)\n\n[ref]: b.md#hello-world\n',
             'docs/b.md': '# Hello World\nContent\n## Next\nMore\n'}
    facts, get = snapshot(files)
    source = files['docs/a.md'].encode()
    link = next(link for link in facts['docs/a.md'].links if link.kind == 'markdown-reference')
    assert source[link.start_byte:link.end_byte] == b'[go][Ref]'
    result = resolve_document_links('docs/a.md', facts['docs/a.md'], link.start_byte, files, get)
    assert result[0][:2] == ('docs/b.md', '/Hello World')
    assert files['docs/b.md'].encode()[result[0][2]:result[0][3]] == b'# Hello World\nContent\n'
    assert resolve_document_links('docs/a.md', facts['docs/a.md'], link.end_byte, files, get) == []


def test_html_duplicate_selectors_return_exact_range():
    files = {'x.html': '<main><p id="one">é</p><p id="two"><a href="#one">x</a></p></main>'}
    facts, get = snapshot(files)
    link = facts['x.html'].links[0]
    result = resolve_document_links('x.html', facts['x.html'], link.start_byte, files, get)[0]
    assert result[:2] == ('x.html', '/main/p')
    assert files['x.html'].encode()[result[2]:result[3]].decode() == '<p id="one">é</p>'


def test_xml_ids_and_links():
    files = {'x.xml': '<r><a id="first">é</a><a href="#first"/></r>'}
    facts, get = snapshot(files)
    link = facts['x.xml'].links[0]
    result = resolve_document_links('x.xml', facts['x.xml'], link.start_byte, files, get)[0]
    assert result[1] == '/r[1]/a[1]'
    assert files['x.xml'].encode()[result[2]:result[3]].decode() == '<a id="first">é</a>'


@pytest.mark.parametrize('path,source,selector', [
    ('x.json', '{"$ref":"#/a~1b/~0key", "a/b":{"~key":1}}', '/a~1b/~0key'),
    ('x.jsonc', '{/* hi */ "$ref":"#/a", "a":1}', '/a'),
    ('x.json5', "{$ref:'#/a', a:1}", '/a'),
    ('x.jsonl', '{"$ref":"#/1/a"}\n{"a":2}\n', '/1/a'),
    ('x.toml', '"$ref" = "#/a"\na=1\n', '/a'),
])
def test_data_references(path, source, selector):
    files = {path: source}
    facts, get = snapshot(files)
    link = facts[path].links[0]
    assert resolve_document_links(path, facts[path], link.start_byte, files, get)[0][1] == selector


def test_ref_lookalike_in_comment_is_not_a_link():
    source = '{/* "$ref":"#/wrong" */ "$ref":"#/right", "right":1, "wrong":2}'
    facts = analyze_document('x.jsonc', source)
    assert [link.uri for link in facts.links] == ['#/right']


def test_duplicate_ids_and_missing_targets_fail_closed():
    files = {'x.html': '<main><p id="x">a</p><p id="x">b</p></main>'}
    facts, get = snapshot(files)
    assert resolve_link('x.html', '#x', files, get) == []
    assert resolve_link('x.html', '#missing', files, get) == []


def test_snapshot_safety_and_root_relative_code_link():
    files = {'docs/a.json': '{"a":1, "b":2}'}
    facts, get = snapshot(files)
    assert resolve_link('src/main.py', 'docs/a.json#/a', files, get)[0][1] == '/a'
    for uri in ('https://example.com/docs/a.json#/a', '//host/docs/a.json#/a',
                '/docs/a.json#/a', '../../docs/a.json#/a', '%2e%2e/%2e%2e/docs/a.json#/a',
                'docs/a.json?x=1#/a', 'docs/a.json', 'docs/a.json#/missing'):
        assert resolve_link('src/main.py', uri, files, get) == []


def test_code_blocks_are_not_links():
    source = '# Title\n`[a](x.md#x)`\n```\n[b](x.md#x)\n```\n'
    assert not analyze_document('x.md', source).links


@pytest.mark.parametrize('path,source', [('x.json', '{bad'), ('x.toml', 'a='),
                                        ('x.xml', '<a>'), ('x.html', '<a'),
                                        ('x.json', 'x' * 32001)])
def test_invalid_and_oversized_are_empty(path, source):
    assert analyze_document(path, source) == DocumentFacts()
