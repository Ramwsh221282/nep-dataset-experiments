from functools import lru_cache

import pytest

from codefinetuner.nep.context.bindings import can_reference, resolve_reference
from codefinetuner.nep.context.code_facts import extract_code


class View:
    def __init__(self, files):
        self.files = files

    @lru_cache(None)
    def code(self, path):
        return extract_code(path, self.files[path])


def reference(view, path, name, receiver=None):
    return next(r for r in view.code(path).references
                if r.name == name and (receiver is None or r.receiver == receiver))


def definition(view, path, name):
    return next(d for d in view.code(path).definitions if d.name == name)


def test_typescript_import_alias_typed_receiver_and_prefix():
    view = View({'src/repo.ts': 'export class Repo { getUser() {} getGroup() {} }',
                 'src/app.ts': 'import { Repo as Store } from "./repo"; function f(repo: Store) { repo.getU(); }'})
    ref = reference(view, 'src/app.ts', 'getU', 'repo')
    result = resolve_reference(view, 'src/app.ts', ref, prefix=True)
    assert result.matches == (definition(view, 'src/repo.ts', 'getUser'),)
    assert result.reason == 'receiver_type' and result.blocked
    assert not resolve_reference(view, 'src/app.ts', ref).matches
    from dataclasses import replace
    ambiguous = resolve_reference(view, 'src/app.ts', replace(ref, name='get'), prefix=True)
    assert ambiguous.ambiguous and not ambiguous.matches


def test_python_imports_and_history():
    view = View({'pkg/service.py': 'def modern(): pass\nclass Service:\n def modern(self): pass\n',
                 'app.py': 'from pkg.service import old as alias\nimport pkg.service as svc\ndef f(x: int):\n alias()\n svc.old()\n'})
    target = definition(view, 'pkg/service.py', 'modern')
    alias = reference(view, 'app.py', 'alias')
    assert can_reference(view, 'app.py', alias, target, 'old')
    assert not can_reference(view, 'app.py', alias, target, 'alias')
    ref = reference(view, 'app.py', 'old', 'svc')
    assert can_reference(view, 'app.py', ref, target, 'old')
    assert resolve_reference(view, 'app.py', ref).blocked


def test_python_from_import_and_shadowing():
    view = View({'pkg/api.py': 'def run(): pass\n',
                 'app.py': 'from pkg.api import run as go\ndef f():\n go()\ndef g(go):\n go()\n'})
    refs = [r for r in view.code('app.py').references if r.name == 'go']
    assert resolve_reference(view, 'app.py', refs[0]).matches == (definition(view, 'pkg/api.py', 'run'),)
    result = resolve_reference(view, 'app.py', refs[1])
    assert result.matches[0].kind == 'parameter' and result.blocked
    assert not can_reference(view, 'app.py', refs[1], definition(view, 'pkg/api.py', 'run'), 'go')


def test_self_method_only_and_nested_owner():
    view = View({'x.py': 'class A:\n def go(self): pass\n def f(self):\n  self.go()\n'})
    result = resolve_reference(view, 'x.py', reference(view, 'x.py', 'go', 'self'))
    assert result.matches == (definition(view, 'x.py', 'go'),)


@pytest.mark.parametrize('source', [
    'use crate::util::run; fn f() { run(); }',
    'fn f() { crate::util::run(); }',
    'fn f() { super::util::run(); }',

])
def test_rust_paths(source):
    view = View({'src/lib.rs': '', 'src/util.rs': 'pub fn run() {}', 'src/sub.rs': source})
    result = resolve_reference(view, 'src/sub.rs', reference(view, 'src/sub.rs', 'run'))
    assert result.matches == (definition(view, 'src/util.rs', 'run'),)


def test_rust_external_paths_do_not_bind_local_modules():
    for expression in ('::util::run()', 'util::run()'):
        view = View({'src/sub.rs': 'fn f() { ' + expression + '; }',
                     'src/util.rs': 'pub fn run() {}', 'src/sub/util.rs': 'pub fn run() {}'})
        result = resolve_reference(view, 'src/sub.rs', reference(view, 'src/sub.rs', 'run'))
        assert result.blocked and not result.matches


def test_rust_self_module_path():
    view = View({'src/lib.rs': 'fn f() { self::util::run(); }',
                 'src/util.rs': 'pub fn run() {}'})
    result = resolve_reference(view, 'src/lib.rs', reference(view, 'src/lib.rs', 'run'))
    assert result.matches == (definition(view, 'src/util.rs', 'run'),)


def test_unknown_receiver_and_ambiguous_local_fail_closed():
    view = View({'x.ts': 'function foo() {} function foo() {} function f(x: Unknown) { x.foo(); foo(); }'})
    qualified = resolve_reference(view, 'x.ts', reference(view, 'x.ts', 'foo', 'x'))
    assert qualified.blocked and not qualified.matches
    bare = resolve_reference(view, 'x.ts', reference(view, 'x.ts', 'foo', ''))
    assert bare.ambiguous and not bare.matches


@pytest.mark.parametrize('annotation', ['&Repo', '&mut Repo'])
def test_rust_borrowed_receiver(annotation):
    view = View({'src/lib.rs': 'struct Repo {} impl Repo { fn go(&self) {} } '
                 + 'fn f(repo: ' + annotation + ') { repo.go(); }'})
    result = resolve_reference(view, 'src/lib.rs', reference(view, 'src/lib.rs', 'go', 'repo'))
    assert result.matches == (definition(view, 'src/lib.rs', 'go'),)


def test_typescript_requires_direct_export_for_imports_and_receiver_types():
    view = View({'repo.ts': 'class Hidden { go() {} } export class Public { go() {} } '
                 'function secret() {} export function open() {} export default class Default {}',
                 'app.ts': 'import { Hidden, Public, secret, open } from "./repo"; '
                 'import Default from "./repo"; function f(a: Hidden, b: Public) '
                 '{ a.go(); b.go(); secret(); open(); Default(); }'})
    for name, receiver in [('go', 'a'), ('secret', ''), ('Default', '')]:
        result = resolve_reference(view, 'app.ts', reference(view, 'app.ts', name, receiver))
        assert result.blocked and not result.matches
    assert resolve_reference(view, 'app.ts', reference(view, 'app.ts', 'open')).matches == (definition(view, 'repo.ts', 'open'),)
    result = resolve_reference(view, 'app.ts', reference(view, 'app.ts', 'go', 'b'))
    assert result.matches[0].owner == 'Public'


def test_typescript_duplicate_import_alias_fails_closed():
    view = View({'a.ts': 'export class Repo { go() {} }',
                 'b.ts': 'export class Repo { go() {} }',
                 'main.ts': 'import { Repo as R } from "./a"; import { Repo as R } from "./b"; '
                 'function f(x: R) { x.go(); }'})
    result = resolve_reference(view, 'main.ts', reference(view, 'main.ts', 'go', 'x'))
    assert result.blocked and not result.matches


def test_zig_relative_import_and_c_quoted_include():
    view = View({'lib.zig': 'pub fn run() void {} fn hidden() void {}',
                 'src/main.zig': 'const lib = @import("../lib.zig"); fn f() void { lib.run(); lib.hidden(); }',
                 'src/api.h': 'int run(void);',
                 'src/main.c': '#include "api.h"\nint f() { return run(); }'})
    result = resolve_reference(view, 'src/main.zig', reference(view, 'src/main.zig', 'run'))
    assert result.matches == (definition(view, 'lib.zig', 'run'),)
    assert not resolve_reference(view, 'src/main.zig', reference(view, 'src/main.zig', 'hidden')).matches
    result = resolve_reference(view, 'src/main.c', reference(view, 'src/main.c', 'run'))
    assert result.matches == (definition(view, 'src/api.h', 'run'),)


def test_unprovable_go_and_java_imports_are_blocked():
    view = View({'go.mod': 'module example.org/project\n',
                 'main.go': 'package main\nimport "example.org/project/util"\nfunc f() { util.Run() }',
                 'util/util.go': 'package util\nfunc Run() {}',
                 'Main.java': 'import p.Util; class Main { void f() { Util.run(); } }',
                 'p/Util.java': 'package p; class Util { static void run() {} }'})
    for path, name, receiver in [('main.go', 'Run', 'util'), ('Main.java', 'run', 'Util')]:
        result = resolve_reference(view, path, reference(view, path, name, receiver))
        assert result.blocked and not result.matches


def test_history_binds_old_import_provider_without_name_fallback():
    view = View({'p.py': 'def new(): pass\n', 'q.py': 'def new(): pass\n',
                 'a.py': 'from p import old\nold()\n'})
    ref = reference(view, 'a.py', 'old')
    assert can_reference(view, 'a.py', ref, definition(view, 'p.py', 'new'), 'old')
    assert not can_reference(view, 'a.py', ref, definition(view, 'q.py', 'new'), 'old')
