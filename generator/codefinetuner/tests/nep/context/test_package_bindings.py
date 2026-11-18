from functools import lru_cache

from codefinetuner.nep.context.bindings import resolve_reference
from codefinetuner.nep.context.code_facts import extract_code
from codefinetuner.nep.context.packages import build_packages, package_keys


class View:
    def __init__(self, files):
        self.files = files
        self.index = build_packages(files, {p: self.code(p) for p in files})

    @lru_cache(None)
    def code(self, path):
        return extract_code(path, self.files[path])

    def packages(self, module, language):
        return self.index.get((language, module), ())


def resolve(view, path, name, receiver=''):
    ref = next(r for r in view.code(path).references if r.name == name and r.receiver == receiver)
    return resolve_reference(view, path, ref)


def test_package_extraction_and_overlay_keys():
    files = {'a.go': 'package old\n', 'b.java': 'package a.b; class B {}',
             'c.cs': 'namespace A.B { class C {} }'}
    facts = {p: extract_code(p, s) for p, s in files.items()}
    index = build_packages(files, facts)
    assert index == {('go', ''): ('a.go',), ('java', 'a.b'): ('b.java',), ('csharp', 'A.B'): ('c.cs',)}
    changed = extract_code('b.java', 'package x.y; class B {}')
    assert package_keys('b.java', facts['b.java'], files) == (('java', 'a.b'),)
    assert package_keys('b.java', changed, files) == (('java', 'x.y'),)
    assert not package_keys('b.java', changed, {})
    assert not extract_code('c.cs', 'namespace A { class C {} } namespace B { class D {} }').package
    assert not extract_code('c.cs', 'namespace A { namespace B { class C {} } }').package


def test_go_relative_and_external_imports():
    files = {'app.go': 'package main\nimport alias "./lib"\nfunc f() { alias.Run(); alias.hidden() }',
             'lib/a.go': 'package different\nfunc Run() {}\nfunc hidden() {}',
             'lib/b.go': 'package different\nfunc Other() {}'}
    view = View(files)
    assert resolve(view, 'app.go', 'Run', 'alias').matches[0].path == 'lib/a.go'
    assert not resolve(view, 'app.go', 'hidden', 'alias').matches
    files['app.go'] = 'package main\nimport "./lib"\nfunc f() { lib.Run() }'
    assert not resolve(View(files), 'app.go', 'Run', 'lib').matches
    files['app.go'] = 'package main\nimport "./lib"\nfunc f() { different.Run() }'
    assert resolve(View(files), 'app.go', 'Run', 'different').matches[0].path == 'lib/a.go'
    files['app.go'] = 'package main\nimport alias "example.com/lib"\nfunc f() { alias.Run() }'
    assert not resolve(View(files), 'app.go', 'Run', 'alias').matches


def test_java_import_receiver_field_visibility_and_duplicates():
    files = {'A.java': 'package app; import p.Util; class A { void f(Util x) { x.value++; x.secret(); } }',
             'Util.java': 'package p; public class Util { public int value; private void secret() {} }'}
    view = View(files)
    assert resolve(view, 'A.java', 'value', 'x').matches[0].name == 'value'
    assert not resolve(view, 'A.java', 'secret', 'x').matches
    files['Other.java'] = 'package p; public class Util {}'
    assert not resolve(View(files), 'A.java', 'value', 'x').matches


def test_csharp_namespace_and_ambiguity():
    files = {'A.cs': 'using P; namespace App { class A { void F(Util x) { x.Go(); } } }',
             'U.cs': 'namespace P { public class Util { public void Go() {} } }'}
    view = View(files)
    assert resolve(view, 'A.cs', 'Go', 'x').matches[0].path == 'U.cs'
    files['V.cs'] = 'namespace P { public class Util { public void Go() {} } }'
    assert not resolve(View(files), 'A.cs', 'Go', 'x').matches
