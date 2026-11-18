import pytest
from tree_sitter_language_pack import get_language, get_parser

from codefinetuner.nep.context.code_facts import CODE_LANGUAGES, _QUERY_DIR, extract_code


SAMPLES = {
    'python': ('x.py', 'class A:\n def f(self, x: T):\n  y: U = x\n  return y.foo()\n', 'f', 'foo'),
    'javascript': ('x.js', 'class A { f(x) { return x.foo(); } }', 'f', 'foo'),
    'typescript': ('x.ts', 'class A { f(x: T) { return x.foo(); } }', 'f', 'foo'),
    'tsx': ('x.tsx', 'function f(x: T) { return <Widget value={x}/>; }', 'f', 'Widget'),
    'rust': ('x.rs', 'struct A {} impl A { fn f(x: T) { x.foo(); } }', 'f', 'foo'),
    'c': ('x.c', 'int f(int x) { return foo(x); }', 'f', 'foo'),
    'cpp': ('x.cpp', 'class A { int f(int x) { return foo(x); } };', 'f', 'foo'),
    'csharp': ('x.cs', 'class A { void f(T x) { x.foo(); } }', 'f', 'foo'),
    'java': ('x.java', 'class A { void f(T x) { x.foo(); } }', 'f', 'foo'),
    'go': ('x.go', 'package p\nfunc f(x T) { foo(x) }', 'f', 'foo'),
    'zig': ('x.zig', 'fn f(x: T) void { foo(x); }', 'f', 'foo'),
}


@pytest.mark.parametrize('language', sorted(set(CODE_LANGUAGES.values())))
def test_queries_compile_and_extract_real_code(language):
    path, source, definition, reference = SAMPLES[language]
    root = get_parser(language).parse(source.encode()).root_node
    assert not root.has_error
    for name in ('definitions', 'references', 'imports'):
        query = get_language(language).query((_QUERY_DIR / language / (name + '.scm')).read_text())
        if name != 'imports':
            assert query.captures(root)
    facts = extract_code(path, source)
    assert definition in {d.name for d in facts.definitions}
    assert reference in {r.name for r in facts.references}
    assert not facts.partial


@pytest.mark.parametrize('language,source', [
    ('python', 'import os'), ('javascript', 'import x from "pkg";'),
    ('typescript', 'import x from "pkg";'), ('tsx', 'import x from "pkg";'),
    ('rust', 'use a::b;'), ('c', '#include <stdio.h>\n'),
    ('cpp', '#include <vector>\n'), ('csharp', 'using A.B;'),
    ('java', 'import a.B;'), ('go', 'package p\nimport "fmt"'),
    ('zig', 'const std = @import("std");'),
])
def test_import_queries_capture_real_imports(language, source):
    query = get_language(language).query((_QUERY_DIR / language / 'imports.scm').read_text())
    assert query.captures(get_parser(language).parse(source.encode()).root_node)


def test_typescript_imports_scopes_types_and_byte_offsets():
    source = 'import Default, { Foo as Bar, Baz } from "pkg";\nclass Café { run(x: Bar) { let y: Baz = x; y.go(); } }'
    facts = extract_code('a.ts', source)
    assert {(i.local, i.module, i.remote) for i in facts.imports} == {
        ('Default', 'pkg', 'default'), ('Bar', 'pkg', 'Foo'), ('Baz', 'pkg', 'Baz')}
    method = next(d for d in facts.definitions if d.name == 'run')
    assert method.kind == 'method' and method.owner == 'Café'
    x = next(d for d in facts.definitions if d.name == 'x')
    y = next(d for d in facts.definitions if d.name == 'y')
    assert x.type_name == 'Bar' and y.type_name == 'Baz'
    assert x.scope == y.scope
    ref = next(r for r in facts.references if r.name == 'go')
    assert ref.receiver == 'y' and ref.scope == y.scope
    data = source.encode()
    assert data[method.name_start:method.name_end] == b'run'
    assert data[method.start:method.end].startswith(b'run(')


def test_python_and_rust_imports_and_receivers():
    py = extract_code('x.py', 'from a.b import Thing as T, other\nimport os.path as op\ndef f(x: T):\n return x.go()\n')
    assert {(i.local, i.module, i.remote) for i in py.imports} == {('T', 'a.b', 'Thing'), ('other', 'a.b', 'other'), ('op', 'os.path', '')}
    assert next(d for d in py.definitions if d.name == 'x').type_name == 'T'
    assert next(r for r in py.references if r.name == 'go').receiver == 'x'
    rust = extract_code('x.rs', 'use a::b::{Thing as T, other};\nimpl T { fn f(x: T) { x.go(); } }')
    assert {(i.local, i.module, i.remote) for i in rust.imports} == {('T', 'a::b', 'Thing'), ('other', 'a::b', 'other')}
    assert next(d for d in rust.definitions if d.name == 'f').owner == 'T'
    assert next(r for r in rust.references if r.name == 'go').receiver == 'x'


def test_partial_parse_preserves_facts_and_safe_header():
    source = 'function good() { ok(); }\nfunction bad(x: T) { broken( ; }\n'
    facts = extract_code('x.ts', source)
    assert facts.partial
    assert {'good', 'bad'} <= {d.name for d in facts.definitions}
    bad = next(d for d in facts.definitions if d.name == 'bad')
    assert source.encode()[bad.start:bad.end].strip() == b'function bad(x: T)'
    assert {'ok', 'broken'} <= {r.name for r in facts.references}


def test_unclosed_function_and_incomplete_expression():
    facts = extract_code('x.ts', 'function f() { thing.')
    assert facts.partial
    definition = next(d for d in facts.definitions if d.name == 'f')
    assert definition.end == len('function f()')
    assert 'thing' in {r.name for r in facts.references}


def test_zig_import_and_untyped_python_parameters():
    zig = extract_code('x.zig', 'const std = @import("std");')
    assert [(i.local, i.module, i.remote) for i in zig.imports] == [('std', 'std', '')]
    py = extract_code('x.py', 'def f(arg):\n return arg\n')
    arg = next(d for d in py.definitions if d.name == 'arg')
    assert arg.kind == 'parameter'
    assert next(r for r in py.references if r.name == 'arg').scope == arg.scope


@pytest.mark.parametrize('language,source', [
    ('python', 'x = "../docs/café.md#intro"'),
    ('javascript', 'const x = "../docs/café.md#intro";'),
    ('typescript', 'const x = "../docs/café.md#intro";'),
    ('tsx', 'const x = "../docs/café.md#intro";'),
    ('rust', 'fn f() { let x = "../docs/café.md#intro"; }'),
    ('c', 'char *x = "../docs/café.md#intro";'),
    ('cpp', 'auto x = "../docs/café.md#intro";'),
    ('csharp', 'class C { string x = "../docs/café.md#intro"; }'),
    ('java', 'class C { String x = "../docs/café.md#intro"; }'),
    ('go', 'package p\nvar x = "../docs/café.md#intro"'),
    ('zig', 'const x = "../docs/café.md#intro";'),
])
def test_document_references_all_languages(language, source):
    path = SAMPLES[language][0]
    root = get_parser(language).parse(source.encode()).root_node
    query = get_language(language).query((_QUERY_DIR / language / 'references.scm').read_text())
    assert any(capture == 'document' for _, capture in query.captures(root))
    refs = [r for r in extract_code(path, source).references if r.receiver == '@document']
    assert len(refs) == 1
    ref = refs[0]
    assert ref.name == '../docs/café.md#intro'
    assert source.encode()[ref.start:ref.end].decode() == ref.name


def test_document_reference_filtering_and_scope():
    source = '''// "ignored.md"
function f() {
  use("settings.JSON#/section/key");
  use("not.txt", "https://example.com/a.md", "/absolute.md", "a.md?x=1");
  use("hello world.md", "a\\\\x2emd", `dynamic-${id}.md`, `plain.md`);
  use("''' + 'x' * 513 + '''.md");
}'''
    refs = [r for r in extract_code('x.ts', source).references if r.receiver == '@document']
    assert [r.name for r in refs] == ['settings.JSON#/section/key']
    assert refs[0].scope == ('function:f@16',)


def test_interpolated_python_and_csharp_strings_are_not_paths():
    for path, source in [
        ('x.py', 'a = f"file.md"\nb = r"file.md"\nc = "file.md"\n'),
        ('x.cs', 'class C { string a = $"file.md"; string b = @"file.md"; string c = "file.md"; }'),
    ]:
        assert [r.name for r in extract_code(path, source).references if r.receiver == '@document'] == ['file.md']


@pytest.mark.parametrize('path,source', [
    ('x.ts', 'function broken(x: ) {}\nfunction valid() {}'),
    ('x.py', 'def broken(x: ):\n pass\ndef valid():\n pass\n'),
    ('x.rs', 'fn broken(x: ) {}\nfn valid() {}'),
])
def test_malformed_header_is_not_a_definition(path, source):
    facts = extract_code(path, source)
    assert facts.partial
    assert 'broken' not in {d.name for d in facts.definitions}
    assert 'valid' in {d.name for d in facts.definitions}


def test_broken_body_preserves_class_and_method_headers():
    facts = extract_code('x.ts', 'class Box { good() { broken( ; } }')
    assert facts.partial
    assert {'Box', 'good'} <= {d.name for d in facts.definitions}


def test_concatenated_strings_are_not_independent_document_paths():
    facts = extract_code('x.py', 'x = "file.md" "suffix"\ny = "good.md"\n')
    assert [r.name for r in facts.references if r.receiver == '@document'] == ['good.md']


def test_rust_scoped_refs_have_qualifiers_and_unqualified_refs_do_not():
    facts = extract_code('x.rs', 'fn f() { a::b::run(); c::run(); run(); }')
    assert [r.receiver for r in facts.references if r.name == 'run'] == ['a::b', 'c', '']


@pytest.mark.parametrize('path', ['x.c', 'x.cpp'])
def test_quoted_includes_only(path):
    source = '#include "local/a.h"\n#include <system.h>\n#include HEADER\n#include "other.hpp"\n'
    facts = extract_code(path, source)
    assert [(i.local, i.module, i.remote, i.scope) for i in facts.imports] == [
        ('*', 'local/a.h', '*', ()), ('*', 'other.hpp', '*', ())]


def test_csharp_namespace_and_alias_imports():
    source = '''using System.Text;
namespace N { using A = Other.Space; using Local.Namespace; class C {} }
global using Global.Space;
using static System.Console;'''
    imports = extract_code('x.cs', source).imports
    assert {(i.local, i.module, i.remote) for i in imports} == {
        ('*', 'System.Text', '*'), ('A', 'Other.Space', ''),
        ('*', 'Local.Namespace', '*'), ('*', 'Global.Space', '*')}
    assert next(i for i in imports if i.module == 'Local.Namespace').scope


def test_go_and_zig_import_forms():
    go = extract_code('x.go', 'package p\nimport ("fmt"; named "a/b"; . "c/d"; _ "e/f"; `g/h`)')
    assert {(i.local, i.module, i.remote) for i in go.imports} == {
        ('fmt', 'fmt', ''), ('named', 'a/b', ''), ('*', 'c/d', '*'),
        ('_', 'e/f', ''), ('h', 'g/h', '')}
    zig = extract_code('x.zig', 'const a = @import("pkg"); const b = @import(x); const c = @import("pkg").Thing;')
    assert [(i.local, i.module, i.remote) for i in zig.imports] == [('a', 'pkg', '')]


@pytest.mark.parametrize('path,source,owner,fields', [
    ('x.ts', 'class Box { count: number; label = "hi"; method() { let local = 1; } }', 'Box', {'count': 'number', 'label': ''}),
    ('x.js', 'class Box { count = 1; #label = "hi"; }', 'Box', {'count': '', '#label': ''}),
    ('x.go', 'package p\ntype Box struct { count, size int }\n', 'Box', {'count': 'int', 'size': 'int'}),
    ('x.rs', 'struct Box { count: u32, pub label: String }', 'Box', {'count': 'u32', 'label': 'String'}),
    ('x.cpp', 'class Box { int first, second; int *ptr; };', 'Box', {'first': 'int', 'second': 'int', 'ptr': 'int'}),
])
def test_fields_have_owner_names_types_and_spans(path, source, owner, fields):
    facts = extract_code(path, source)
    for name, typ in fields.items():
        field = next(d for d in facts.definitions if d.name == name)
        assert field.owner == owner and field.kind == 'variable' and field.type_name == typ
        assert source.encode()[field.name_start:field.name_end].decode() == name
        assert field.start <= field.name_start < field.name_end <= field.end
    assert all(d.owner == '' for d in facts.definitions if d.name == 'local')


def test_incomplete_function_parameter_does_not_leak():
    source = 'import { UsersRepository } from "./repo"; function list(repo: UsersRepository){return repo.getU'
    facts = extract_code('x.ts', source)
    assert facts.partial
    param = next(d for d in facts.definitions if d.name == 'repo')
    ref = next(r for r in facts.references if r.name == 'getU')
    assert param.type_name == 'UsersRepository'
    assert param.scope == ref.scope == ('function:list@42',)
    assert ref.receiver == 'repo'
    function = next(d for d in facts.definitions if d.name == 'list')
    assert source.encode()[function.start:function.end] == b'function list(repo: UsersRepository)'


def test_function_expression_and_arrow_parameters_have_function_scopes():
    facts = extract_code('x.ts', 'const a = function inner(x: T) { return x; }; const b = (y: T) => y;')
    for name in ('x', 'y'):
        param = next(d for d in facts.definitions if d.name == name)
        ref = next(r for r in facts.references if r.name == name)
        assert param.scope == ref.scope and param.scope[-1].startswith('function:')


def test_incomplete_parameter_list_never_creates_global_parameter():
    facts = extract_code('x.ts', 'function list(repo: UsersRepository')
    assert all(d.name != 'repo' or d.scope for d in facts.definitions)


def test_unsupported_extension():
    with pytest.raises(ValueError):
        extract_code('x.md', 'text')
