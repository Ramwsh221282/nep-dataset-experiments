import pytest

from codefinetuner.nep.context.ContextStructureParserAlgorithm import ContextParseError
from codefinetuner.nep.context.ContextStructureParserResolution import ContextStructureParserResolution


def project(path, source, selected, **kwargs):
    start = len(source[:source.index(selected)].encode())
    end = start + len(selected.encode())
    return ContextStructureParserResolution.resolve(path).parse_range(path, source, start, end, **kwargs)


def test_typescript_method_exact_owner_and_decorator_unicode():
    method = '@log\n  same(x: string): string { return secret(x); }'
    source = '// café\nclass First { same() { otherSecret(); } }\nclass Second {\n  ' + method + '\n  sibling() { siblingSecret(); }\n}'
    fragment, = project('x.ts', source, method)
    assert 'class Second' in fragment.code_text and '@log' in fragment.code_text
    assert 'same(x: string): string' in fragment.code_text
    assert all(s not in fragment.code_text for s in ('secret(', 'sibling', 'First'))
    assert source.encode()[fragment.start_byte:fragment.end_byte].decode() == method


def test_rust_impl_owner_and_attribute():
    method = '#[inline]\n fn get(&self) -> i32 { secret(); 1 }'
    source = 'impl Boxed {\n ' + method + '\n fn other(&self) { sibling(); }\n}'
    fragment, = project('x.rs', source, method)
    assert 'impl Boxed' in fragment.code_text and '#[inline]' in fragment.code_text
    assert 'fn get(&self) -> i32' in fragment.code_text
    assert 'secret' not in fragment.code_text and 'other' not in fragment.code_text


def test_errors_in_body_and_other_definitions():
    method = 'good(x: number) { return ???; }'
    source = 'function broken() { return ???; }\nfunction valid() { secret(); }\nconst = ;'
    # Check an independent declaration despite errors elsewhere.
    fragment, = project('x.ts', source, 'function valid() { secret(); }')
    assert 'function valid()' in fragment.code_text and 'secret' not in fragment.code_text
    source = 'class A { ' + method + ' sibling() { secret(); } }'
    fragment, = project('x.ts', source, method)
    assert 'good(x: number)' in fragment.code_text and '???' not in fragment.code_text
    assert 'sibling' not in fragment.code_text


def test_unsafe_header_and_partial_ranges_fail_closed():
    source = 'class A { bad(x: ???) { secret(); } ok(x: number) { let local = 1; } }'
    assert not project('x.ts', source, 'bad(x: ???) { secret(); }')
    fragment, = project('x.ts', source, 'x: number')
    assert fragment.code_text == 'x: number'
    assert not project('x.ts', source, 'let local = 1;')


def test_entire_class_projects_nested_functions_but_rejects_bad_headers():
    source = 'class A { one() { secret(); } field = (x: number) => hidden(x); }'
    fragment, = project('x.ts', source, source)
    assert 'one()' in fragment.code_text and 'field' in fragment.code_text
    assert 'secret' not in fragment.code_text and 'hidden' not in fragment.code_text
    source = 'class A { bad(x: ???) { secret(); } }'
    assert not project('x.ts', source, source)


def test_exact_projection_preserves_jsx_but_not_nested_callback_effects():
    source = 'export function View() { return <div>{() => secret()}</div>; }'
    fragment, = project('x.tsx', source, source)
    assert 'View()' in fragment.code_text and '<div>' in fragment.code_text
    assert 'secret' not in fragment.code_text


def test_document_exact_range_and_budget():
    source = '<root><item>café</item><other/></root>'
    fragment, = project('x.xml', source, '<item>café</item>')
    assert fragment.code_text == '<root><item>café</item></root>'
    assert not project('x.xml', source, '<item>café</item>', max_chars=3)
    assert not project('x.xml', source, '<item>café</item>', selector='/wrong')
    assert not project('x.xml', source, 'café')


@pytest.mark.parametrize('path,source', [
    ('x.py', 'class A:\n def f(self, x: T):\n  y: U = x\n  return y.foo()\n'),
    ('x.js', 'class A { f(x) { return x.foo(); } }'),
    ('x.ts', 'class A { f(x: T) { return x.foo(); } }'),
    ('x.tsx', 'function f(x: T) { return <Widget value={x}/>; }'),
    ('x.rs', 'struct A {} impl A { fn f(x: T) { x.foo(); } }'),
    ('x.c', 'int f(int x) { return foo(x); }'),
    ('x.cpp', 'class A { int f(int x) { return foo(x); } };'),
    ('x.cs', 'class A { void f(T x) { x.foo(); } }'),
    ('x.java', 'class A { void f(T x) { x.foo(); } }'),
    ('x.go', 'package p\nfunc f(x T) { foo(x) }'),
    ('x.zig', 'fn f(x: T) void { foo(x); }'),
])
def test_extract_code_fixture_function_spans(path, source):
    from codefinetuner.nep.context.code_facts import extract_code
    facts = extract_code(path, source)
    definition = next(d for d in facts.definitions if d.name == 'f')
    fragment, = ContextStructureParserResolution.resolve(path).parse_range(path, source, definition.start, definition.end)
    assert 'f(' in fragment.code_text
    assert 'foo(' not in fragment.code_text
    if path.endswith('.tsx'):
        assert '<Widget' in fragment.code_text


def test_extract_code_header_only_and_decorators():
    from codefinetuner.nep.context.code_facts import extract_code
    source = 'class A { @log f(x: T) { broken( ; } sibling() { secret(); } }'
    definition = next(d for d in extract_code('x.ts', source).definitions if d.name == 'f')
    fragment, = ContextStructureParserResolution.resolve('x.ts').parse_range('x.ts', source, definition.start, definition.end)
    assert '@log' in fragment.code_text and 'f(x: T)' in fragment.code_text
    assert 'broken' not in fragment.code_text and 'sibling' not in fragment.code_text


def test_python_method_decorators_and_siblings():
    from codefinetuner.nep.context.code_facts import extract_code
    source = '@outer\nclass A:\n @dec\n def f(self):\n  secret()\n def sibling(self):\n  other()\n'
    definition = next(d for d in extract_code('x.py', source).definitions if d.name == 'f')
    fragment, = ContextStructureParserResolution.resolve('x.py').parse_range('x.py', source, definition.start, definition.end)
    assert '@outer' in fragment.code_text and '@dec' in fragment.code_text
    assert 'sibling' not in fragment.code_text and 'secret' not in fragment.code_text


def test_extract_code_typed_local_never_includes_initializer():
    from codefinetuner.nep.context.code_facts import extract_code
    for path, source in [('x.ts', 'function f(x: T) { let y: U = secret(); }'),
                         ('x.py', 'def f(x: T):\n y: U = secret()\n')]:
        definition = next(d for d in extract_code(path, source).definitions if d.name == 'y')
        fragment, = ContextStructureParserResolution.resolve(path).parse_range(path, source, definition.start, definition.end)
        assert fragment.code_text == 'y: U'


def test_invalid_byte_ranges():
    parser = ContextStructureParserResolution.resolve('x.ts')
    with pytest.raises(ContextParseError):
        parser.parse_range('x.ts', 'é', 1, 2)
    with pytest.raises(ContextParseError):
        parser.parse_range('x.ts', 'abc', 0, 5)
