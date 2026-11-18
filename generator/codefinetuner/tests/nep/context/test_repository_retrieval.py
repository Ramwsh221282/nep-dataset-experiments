"""End-to-end: retrieval must depend on editor state, not expected output."""
from dataclasses import replace
from importlib import import_module

from codefinetuner.nep.context.RepositorySymbolIndex import RepositorySymbolIndex
from codefinetuner.nep.context.ContextPreparation import ContextPreparation
from codefinetuner.nep.context.ContextRetriever import ContextRetriever
from codefinetuner.nep.engine import Scenario, Step
from codefinetuner.nep.protocol import render


def prepare(files, target, cursor, *, index=None, history=()):
    view = (index or RepositorySymbolIndex(files)).view(files)
    return ContextPreparation().prepare(view, target, cursor, history, cursor, cursor)


def test_unfinished_member_finds_one_external_method_without_history_or_global_name_lookup():
    files = {'app.ts': 'import { Repo as Real } from "./repo";\nfunction f(r: Real) { return r.getU',
             'repo.ts': 'export class Repo { getUser(id: number) { return id; } sibling() {} }\n'
                        'export class MockRepo { getUser() { return 99; } }'}
    retrieval, _, diagnostics = prepare(files, 'app.ts', len(files['app.ts'].encode()))
    assert [(f['owner'], f['symbol'], f['reason']) for f in retrieval] == [('Repo', 'getUser', 'receiver_type_prefix')]
    assert 'sibling' not in retrieval[0]['code_text']
    assert any(d['reason'] == 'partial_syntax_tree' for d in diagnostics)
    files['repo.ts'] = files['repo.ts'].replace('sibling()', 'getUsers()')
    retrieval, _, diagnostics = prepare(files, 'app.ts', len(files['app.ts'].encode()))
    assert not any(f['reason'] == 'receiver_type_prefix' for f in retrieval)
    assert any(d['reason'] == 'ambiguous_prefix' for d in diagnostics)


def test_disjoint_snapshots_and_cache_no_stale_ranges(monkeypatch):
    module = import_module('codefinetuner.nep.context.RepositorySymbolIndex')
    calls = []
    original = module.extract_code
    def counted(path, text):
        calls.append(path)
        return original(path, text)
    monkeypatch.setattr(module, 'extract_code', counted)
    base = {'lib.py': 'def get(): return 0\n', 'app.py': 'import lib\nlib.get()\n'}
    index = RepositorySymbolIndex(base)
    assert sorted(calls) == ['app.py', 'lib.py']
    modified = {**base, 'lib.py': '# 🌍\ndef get(x): return x\n'}
    for files in (modified, base, modified):
        external, _, _ = prepare(files, 'app.py', len(base['app.py']) - 2, index=index)
        fragment, = external
        assert ('get(x)' in fragment['code_text']) == (files is modified)
        assert files['lib.py'].encode()[fragment['start_byte']:fragment['end_byte']].startswith(b'def get')
    assert calls.count('lib.py') == 2 and calls.count('app.py') == 1


def test_history_tracks_rename_then_byte_shift_not_deleted_namesake():
    old = 'def legacy(x): return x\n'
    new = 'def modern(x): return x\n'
    shifted = '# 🌍\n' + new
    app = 'import api\na = api.legacy(1)\n'
    history = [Step('api.py', old, new), Step('api.py', new, shifted)]
    idx = RepositorySymbolIndex({'api.py': old, 'app.py': app})
    files = {'api.py': shifted, 'app.py': app}
    retrieval, _, _ = prepare(files, 'app.py', app.index('legacy'), index=idx, history=history)
    f, = retrieval
    assert f['symbol'] == 'modern' and f['reason'] == 'history_change'
    assert f['start_byte'] == len('# 🌍\n'.encode())
    replaced = 'def modern(other): return 444\n'
    history += [Step('api.py', shifted, ''), Step('api.py', '', replaced)]
    view = idx.view({'api.py': replaced, 'app.py': app})
    candidates, _ = ContextRetriever().candidates(view, 'app.py', app.index('legacy'), history)
    # New declaration is independently eligible as a history seed, but the old
    # use cannot have been bound to this unrelated replacement via the rename.
    assert not any(c.name == 'modern' and c.origin == 'cursor' for c in candidates)


def test_projection_of_code_document_link_and_snapshot_only():
    files = {'settings.ts': 'const help = "./guide.md#setup";\n',
             'guide.md': '# Guide\n\n## Setup\nUse Rust.\n\n## Other\nUnrelated.\n'}
    external, _, _ = prepare(files, 'settings.ts', 20)
    fragment, = external
    assert fragment['selector'] == '/Guide/Setup' and fragment['path'] == 'guide.md'
    assert 'Use Rust.' in fragment['code_text'] and 'Unrelated' not in fragment['code_text']
    files['settings.ts'] = 'const help = "https://example.com/guide.md#setup";'
    assert prepare(files, 'settings.ts', 20)[0] == []


def test_package_overlay_can_move_add_and_remove_members_without_mutating_base():
    base = {'src/V.java': 'package lib; public class V {}', 'src/X.java': 'package lib; class X {}'}
    index = RepositorySymbolIndex(base)
    view = index.view({'src/V.java': 'package newer; public class V {}', 'src/Y.java': 'package lib; class Y {}'})
    assert view.packages('lib', 'java') == ('src/Y.java',)
    assert view.packages('newer', 'java') == ('src/V.java',)
    assert index.view(base).packages('lib', 'java') == ('src/V.java', 'src/X.java')


def test_ambiguity_witnesses_kept_for_replay():
    source = 'import {A} from "./foo";\nimport {B} from "./foo.ts";\nA(); B();\n'
    files = {'app.ts': source, 'foo.ts': 'export function A() {}\nexport function B() {}',
             'foo.js': 'export function A() {}', 'unrelated.py': 'x = 1'}
    item = Scenario({**files, 'app.ts': '//before\n' + source},
                    [Step('app.ts', '//before\n' + source, source)], 'app.ts', source.replace('B();', 'B(1);'),
                    'insert', 'typescript', 'f')
    row = render(item, include_blocks=True)
    assert 'foo.js' in row['index_witness_paths'] and 'unrelated.py' not in row['index_witness_paths']
    reduced = replace(item, initial={p: item.initial[p] for p in row['index_witness_paths']})
    assert render(reduced)['input'] == row['input']
