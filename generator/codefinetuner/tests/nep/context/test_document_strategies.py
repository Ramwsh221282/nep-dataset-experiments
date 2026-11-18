import pytest

from codefinetuner.nep.context.ContextStructureParserAlgorithm import ContextParseError
from codefinetuner.nep.context.ContextStructureParser import ContextStructureParser
from codefinetuner.nep.context.JSONContextStructureParser import JSONContextStructureParser
from codefinetuner.nep.context.JSONCContextStructureParser import JSONCContextStructureParser
from codefinetuner.nep.context.JSON5ContextStructureParser import JSON5ContextStructureParser
from codefinetuner.nep.context.JSONLContextStructureParser import JSONLContextStructureParser
from codefinetuner.nep.context.XMLContextStructureParser import XMLContextStructureParser
from codefinetuner.nep.context.HTMLContextStructureParser import HTMLContextStructureParser
from codefinetuner.nep.context.MarkdownContextStructureParser import MarkdownContextStructureParser
from codefinetuner.nep.context.TOMLContextStructureParser import TOMLContextStructureParser


def selected(items, selector):
    return next(item for item in items if item.selector == selector)


def test_json_branches_and_unicode_spans():
    source = '{"é": {"a/b~c": [1, true]}}'
    items = JSONContextStructureParser().parse('x.json', source)
    item = selected(items, '/é/a~1b~0c/1')
    assert source.encode()[item.start_byte:item.end_byte] == b'true'
    assert '"a/b~c"' in selected(items, '/é/a~1b~0c').code_text


@pytest.mark.parametrize('parser,source', [
    (JSONContextStructureParser(), '{"a": 1, "a": 2}'),
    (JSONContextStructureParser(), '{"a": NaN}'),
    (JSONContextStructureParser(), '{"a": 1,}'),
    (JSONCContextStructureParser(), '{"a": 1,}'),
    (JSONCContextStructureParser(), '{"a": 1, /* x */ "a": 2}'),
    (JSON5ContextStructureParser(), '{a: 1, a: 2}'),
    (JSON5ContextStructureParser(), '{a: }'),
    (JSONLContextStructureParser(), '{"a":1}\n\n'),
    (TOMLContextStructureParser(), 'a=1\na=2'),
    (XMLContextStructureParser(), '<a><b></a>'),
])
def test_invalid(parser, source):
    with pytest.raises(ContextParseError):
        parser.parse('x', source)


def test_jsonc_comments_and_strings():
    source = '{\n // help\n "url": "https://x/*yes*/",\n /* other */ "é": 2\n}'
    items = JSONCContextStructureParser().parse('x.jsonc', source)
    assert '// help' in selected(items, '/url').code_text
    assert '/* other */' in selected(items, '/é').code_text
    with pytest.raises(ContextParseError):
        JSONContextStructureParser().parse('x.json', source)


def test_json5_features():
    items = JSON5ContextStructureParser().parse('x.json5', "{unquoted: 'hi', hex: 0x10, trailing: [1,],}")
    assert "'hi'" in selected(items, '/unquoted').code_text
    assert '0x10' in selected(items, '/hex').code_text


def test_jsonl_records():
    source = '{"é":1}\n{"a":2}\n'
    item = selected(JSONLContextStructureParser().parse('x.jsonl', source), '/1/a')
    assert source.encode()[item.start_byte:item.end_byte] == b'"a":2'


def test_xml_namespace_mixed_and_repeated():
    source = '<r xmlns="urn:x">é<a>one<b/>tail</a><a>two</a></r>'
    items = XMLContextStructureParser().parse('x.xml', source)
    item = selected(items, '/r[1]/a[2]')
    assert 'xmlns="urn:x"' in item.code_text and '<a>two</a>' in item.code_text
    assert source.encode()[item.start_byte:item.end_byte] == b'<a>two</a>'
    assert 'one<b/>tail' in selected(items, '/r[1]/a[1]').code_text


def test_xml_forbids_entities():
    with pytest.raises(ContextParseError):
        XMLContextStructureParser().parse('x.xml', '<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>')


def test_html_subtree_and_scripts():
    source = '<main><form id="é"><label for="x">Name</label><input id="x"></form><script>evil()</script></main>'
    items = HTMLContextStructureParser().parse('x.html', source)
    form = selected(items, '/main/form')
    assert '<main>' in form.code_text and 'for="x"' in form.code_text
    assert source.encode()[form.start_byte:form.end_byte].decode().startswith('<form')
    assert all('evil()' not in item.code_text for item in items)
    with pytest.raises(ContextParseError):
        HTMLContextStructureParser().parse('x.jsx', '<div/>')


def test_markdown_sections_fences_and_references():
    source = '# API\n\n## Auth\n```js\n# not heading\n```\nSee [link][id].\n\n## Other\ntext\n\n[id]: https://x\n'
    items = MarkdownContextStructureParser().parse('x.md', source)
    item = selected(items, '/API/Auth')
    assert '# API' in item.code_text and '# not heading' in item.code_text
    assert '[id]: https://x' in item.code_text
    assert len(items) == 3


def test_toml_tables_and_values():
    source = '[server]\nname = "é"\n[server.db]\nports = [1, 2]\n'
    items = TOMLContextStructureParser().parse('x.toml', source)
    item = selected(items, '/server/db/ports')
    assert 'ports = [1, 2]' in item.code_text
    assert item.code_text.startswith('[server.db]\n')
    assert source.encode()[item.start_byte:item.end_byte].decode() == 'ports = [1, 2]'


def test_toml_array_tables_have_distinct_paths():
    source = '[[products]]\nname="a"\n[[products]]\nname="b"\n'
    items = TOMLContextStructureParser().parse('x.toml', source)
    assert '"a"' in selected(items, '/products/0/name').code_text
    assert '"b"' in selected(items, '/products/1/name').code_text


def test_json5_comment_after_unquoted_key():
    items = JSON5ContextStructureParser().parse('x.json5', '{name /* key comment */: 1}')
    assert 'key comment' in selected(items, '/name').code_text


def test_json5_special_values_unicode_keys_and_line_endings():
    source = '{\\u0061: Infinity\u00a0, café: -Infinity, z: NaN // note\u2028, next: 2}'
    items = JSON5ContextStructureParser().parse('x.json5', source)
    assert 'Infinity' in selected(items, '/a').code_text
    assert '-Infinity' in selected(items, '/café').code_text
    assert '// note\u2028' in selected(items, '/z').code_text
    assert selected(items, '/next').code_text.endswith('2')


def test_jsonc_selected_comments_are_retained_and_valid():
    source = '{"a": 1 // explanation\n, "b": [/* first */ 2 /* trailing */, 3]}'
    parser = ContextStructureParser(JSONCContextStructureParser())
    a = parser.parse('x.jsonc', source, selectors=['/a'])[0]
    assert '// explanation\n' in a.code_text
    JSONCContextStructureParser().parse('fragment.jsonc', '{' + a.code_text.split('\n', 1)[1] + '}')
    element = parser.parse('x.jsonc', source, selectors=['/b/0'])[0]
    assert '/* first */' in element.code_text and '/* trailing */' in element.code_text
    JSONCContextStructureParser().parse('fragment.jsonc', '[' + element.code_text.split('\n', 1)[1] + ']')


def test_xml_encoding_and_entity_safety():
    parser = XMLContextStructureParser()
    for source in ['<?xml version="1.0" encoding="ISO-8859-1"?><r>é</r>',
                   '<!DOCTYPE r [<!ENTITY a "secret">]><r>&a;</r>', '<r>\ud800</r>']:
        with pytest.raises(ContextParseError):
            parser.parse('x.xml', source)
    source = '<?xml version="1.0" encoding="UTF-8"?><r>é<a/></r>'
    item = selected(parser.parse('x.xml', source), '/r[1]/a[1]')
    assert source.encode()[item.start_byte:item.end_byte] == b'<a/>'


def test_html_root_script_sibling_and_wrapper_nested_selection():
    source = '<script>bad()</script><main><p>one</p><p>two</p></main>'
    parser = ContextStructureParser(HTMLContextStructureParser())
    items = parser.parse('x.html', source)
    assert len(items) == 2
    assert all('bad()' not in item.code_text for item in items)
    assert [item.code_text for item in items] == ['<main><p>one</p></main>', '<main><p>two</p></main>']


def test_wrapper_markdown_budget_skips_large_section_atomically():
    source = '# Large\n' + 'x' * 500 + '\n# Small\nshort\n'
    parser = ContextStructureParser(MarkdownContextStructureParser())
    items = parser.parse('x.md', source, max_chars=30)
    assert [item.selector for item in items] == ['/Small']
    with pytest.raises(ContextParseError):
        parser.parse('x.md', source, selectors=['/Large'], max_chars=30)


def test_wrapper_nested_json_selectors_and_order():
    parser = ContextStructureParser(JSONContextStructureParser())
    source = '{"outer":{"a":1,"b":2}}'
    items = parser.parse('x.json', source, selectors=['/outer/b', '/outer/a'])
    assert [item.selector for item in items] == ['/outer/a', '/outer/b']
    with pytest.raises(ContextParseError):
        parser.parse('x.json', source, selectors=['/outer', '/outer/a'])


@pytest.mark.parametrize('strategy,path,source,selector', [
    (JSONContextStructureParser, 'x.json', '{"scripts":{"build":"tsc"}}', '/scripts/build'),
    (JSONCContextStructureParser, 'x.jsonc', '{"scripts":{/* c */"build":"tsc"}}', '/scripts/build'),
    (JSON5ContextStructureParser, 'x.json5', '{scripts:{build:"tsc"}}', '/scripts/build'),
    (JSONContextStructureParser, 'x.json', '{"arr":[10,20]}', '/arr/1'),
    (JSONLContextStructureParser, 'x.jsonl', '{"a":1}\n{"a":2}\n', '/1/a'),
])
def test_json_pointer_is_in_prompt_text(strategy, path, source, selector):
    item = ContextStructureParser(strategy()).parse(path, source, selectors=[selector])[0]
    assert item.code_text.startswith('JSON Pointer: ' + selector + '\n')
    assert item.representation == 'fragment_with_pointer'
    assert item.code_text.split('\n', 1)[1] == source.encode()[item.start_byte:item.end_byte].decode()


def test_json_pointer_escapes_newlines():
    item = selected(JSONContextStructureParser().parse('x.json', '{"a\\nb":1}'), '/a\nb')
    assert item.code_text.startswith('JSON Pointer: /a\\nb\n')


def test_markdown_only_used_reference_definitions():
    source = ('# Section\n[full][Target] [collapsed][] [shortcut] ![image][pic]\n'
              '`[unused]`\n```\n[unused]\n```\n# Definitions\n'
              '[target]: /one\n[collapsed]: /two\n[shortcut]: /three\n[pic]: /img\n[unused]: /no\n')
    item = selected(MarkdownContextStructureParser().parse('x.md', source), '/Section')
    for label in ('target', 'collapsed', 'shortcut', 'pic'):
        assert '[' + label + ']: ' in item.code_text
    assert '[unused]: /no' not in item.code_text
