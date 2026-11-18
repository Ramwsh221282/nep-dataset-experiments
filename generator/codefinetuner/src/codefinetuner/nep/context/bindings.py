"""Bounded, snapshot-local resolution of syntactic code facts.

This is deliberately not a compiler: unsupported or conflicting bindings fail closed.
Paths in the view must be root-relative POSIX paths.
"""
import posixpath
import re
from dataclasses import dataclass, replace
from pathlib import PurePosixPath


from .code_facts import CODE_LANGUAGES
from .index_models import Definition, Reference


@dataclass(frozen=True)
class Resolution:
    matches: tuple[Definition, ...] = ()
    reason: str = 'unresolved'
    ambiguous: bool = False
    blocked: bool = False


def _result(definitions, reason, blocked=True):
    matches = tuple(dict.fromkeys(definitions))
    if len(matches) > 1:
        return Resolution((), reason, True, blocked)
    return Resolution(matches, reason, False, blocked)


def _visible(scope, parent):
    return scope[:len(parent)] == parent


def _bindings(facts, name, scope):
    defs = [d for d in facts.definitions if d.name == name and not d.owner
            and _visible(scope, d.scope)]
    imports = [i for i in facts.imports if i.local == name and _visible(scope, i.scope)]
    depth = max((len(x.scope) for x in (*defs, *imports)), default=-1)
    return ([d for d in defs if len(d.scope) == depth],
            [i for i in imports if len(i.scope) == depth])


def _module_files(view, path, module, language):
    """Compute a small fixed set of candidate paths; never iterate view.files."""
    directory = posixpath.dirname(path)
    if language in ('typescript', 'tsx', 'javascript'):
        if not module.startswith('.'):
            return ()
        base = posixpath.join(directory, module)
        extensions = ('.ts', '.tsx', '.js', '.jsx', '.mts', '.cts', '.mjs', '.cjs')
        candidates = [base] + [base + ext for ext in extensions] + [posixpath.join(base, 'index' + ext) for ext in extensions]
    elif language == 'python':
        dots = len(module) - len(module.lstrip('.'))
        if dots:
            base_dir = directory
            for _ in range(dots - 1):
                base_dir = posixpath.join(base_dir, '..')
            base = posixpath.join(base_dir, module[dots:].replace('.', '/'))
        else:
            base = module.replace('.', '/')
        candidates = [base + '.py', base + '.pyi', posixpath.join(base, '__init__.py'), posixpath.join(base, '__init__.pyi')]
    elif language in ('zig', 'c', 'cpp'):
        if module.startswith('/') or (language == 'zig' and not module.endswith('.zig')):
            return ()
        candidates = [posixpath.join(directory, module)]
    elif language == 'rust':
        parts = module.split('::')
        if parts[0] == 'crate':
            # The crate root is the nearest conventional src directory, or the snapshot root.
            segments = PurePosixPath(path).parts
            src_indexes = [i for i, segment in enumerate(segments[:-1]) if segment == 'src']
            base_dir = '/'.join(segments[:src_indexes[-1] + 1]) if src_indexes else ''
            parts = parts[1:]
        elif parts[0] in ('self', 'super'):
            stem = PurePosixPath(path).stem
            base_dir = directory if stem in ('lib', 'main', 'mod') else posixpath.join(directory, stem)
            if parts[0] == 'self':
                parts = parts[1:]
            while parts and parts[0] == 'super':
                base_dir = posixpath.dirname(base_dir)
                parts = parts[1:]
        else:
            return ()
        base = posixpath.join(base_dir, *parts)
        candidates = [base + '.rs', posixpath.join(base, 'mod.rs')]
        if not parts:
            candidates += [posixpath.join(base_dir, 'lib.rs'), posixpath.join(base_dir, 'main.rs')]
    else:
        return ()
    result = []
    for candidate in candidates:
        candidate = posixpath.normpath(candidate)
        if candidate != '..' and not candidate.startswith('../') and not candidate.startswith('/') and candidate in view.files and candidate not in result:
            result.append(candidate)
    return tuple(result)


def _select(view, paths, name, prefix=False, owner=None):
    if len(paths) > 1:
        return []
    found = []
    for target in paths:
        for d in view.code(target).definitions:
            if (d.name.startswith(name) if prefix else d.name == name):
                if owner is None:
                    if not d.scope and not d.owner:
                        found.append(d)
                elif d.owner == owner and d.kind in ('method', 'field', 'variable'):
                    found.append(d)
    return found


def _direct_export(view, definition, language):
    return definition.direct_export


def _external_select(view, paths, name, language, prefix=False, owner=None):
    result = _select(view, paths, name, prefix, owner)
    if len(result) > 1:
        return result
    if language == 'zig':
        result = [d for d in result if re.search(rb'\bpub\s*$', view.files[d.path].encode('utf-8')[max(0, d.start - 32):d.start])]
    if language in ('javascript', 'typescript', 'tsx'):
        if owner is not None:
            owners = _select(view, paths, owner)
            if len(owners) != 1 or not _direct_export(view, owners[0], language):
                return []
            result = [d for d in result if not re.match(
                rb'\s*(?:private|protected)\b',
                view.files[d.path].encode('utf-8')[d.start:d.end]) and not d.name.startswith('#')]
        else:
            result = [d for d in result if _direct_export(view, d, language)]
    return result


def _package_paths(view, module, language):
    lookup = getattr(view, 'packages', None)
    return tuple(lookup(module, language)) if lookup else ()


def _package_select(view, paths, name, language, source, prefix=False, owner=None):
    result = []
    source_package = view.code(source).package
    for path in paths:
        facts = view.code(path)
        if language == 'go' and posixpath.dirname(path) == posixpath.dirname(source) and facts.package != source_package:
            continue
        for d in facts.definitions:
            if not (d.name.startswith(name) if prefix else d.name == name):
                continue
            if owner is None:
                if d.owner or any(not s.startswith('namespace:') for s in d.scope):
                    continue
            elif d.owner != owner or d.kind not in ('method', 'field', 'variable'):
                continue
            if language == 'go' and (posixpath.dirname(path) != posixpath.dirname(source) or facts.package != source_package) and not d.name[:1].isupper():
                continue
            if language in ('java', 'csharp') and owner and d.visibility in ('private', 'protected'):
                continue
            if language in ('java', 'csharp') and path != source:
                if d.visibility in ('private', 'protected'):
                    continue
                if language == 'csharp' and owner and d.visibility != 'public':
                    continue
                if language == 'java' and facts.package != source_package and d.visibility != 'public':
                    continue
            result.append(d)
    return result


def _go_paths(view, path, imp, local=None):
    if not imp.module.startswith('./') and not imp.module.startswith('../'):
        return ()
    directory = posixpath.normpath(posixpath.join(posixpath.dirname(path), imp.module))
    if directory == '..' or directory.startswith('../') or directory.startswith('/'):
        return ()
    if directory == '.':
        directory = ''
    paths = _package_paths(view, directory, 'go')
    names = {view.code(p).package for p in paths}
    if len(names) != 1 or not names or (not imp.explicit_alias and (local or imp.local) not in names):
        return ()
    return paths


def _go_bindings(view, path, facts, name, scope):
    defs, imports = _bindings(facts, name, scope)
    extra = [i for i in facts.imports if not i.explicit_alias and i.local != name
             and _visible(scope, i.scope) and _go_paths(view, path, i, name)]
    if extra:
        depth = max([len(d.scope) for d in defs] + [len(i.scope) for i in imports + extra])
        return ([d for d in defs if len(d.scope) == depth],
                [i for i in imports + extra if len(i.scope) == depth])
    return defs, imports


def _import_target(view, path, imp, language, name=None, prefix=False):
    if language == 'java':
        return _package_select(view, _package_paths(view, imp.module, language), name if name is not None else imp.remote, language, path, prefix)
    paths = _module_files(view, path, imp.module, language)
    return _external_select(view, paths, name if name is not None else imp.remote, language, prefix)


def _type_target(view, path, facts, scope, type_name, language):
    if language == 'rust':
        type_name = re.sub(r'^&(?:\s*mut\s+|\s*)', '', type_name)
    if language == 'go' and re.fullmatch(r'\*?[A-Za-z_]\w*\.[A-Za-z_]\w*', type_name):
        qualifier, name = type_name.lstrip('*').split('.')
        defs, imports = _go_bindings(view, path, facts, qualifier, scope)
        if not defs and len(imports) == 1:
            types = _package_select(view, _go_paths(view, path, imports[0], qualifier), name, language, path)
            if len(types) == 1 and types[0].kind == 'type':
                return _go_paths(view, path, imports[0], qualifier), types[0].name
        return (), ''
    if not re.fullmatch(r'[A-Za-z_]\w*', type_name):
        return (), ''
    defs, imports = _bindings(facts, type_name, scope)
    if not defs and not imports and language in ('java', 'csharp', 'go'):
        paths = _type_candidates(view, path, facts, type_name, language)
        if len(paths) == 1:
            owner_paths = _package_paths(view, posixpath.dirname(paths[0].path), 'go') if language == 'go' else (paths[0].path,)
            return owner_paths, paths[0].name
        return (), ''
    if len(defs) + len(imports) != 1:
        return (), ''
    if defs:
        d = defs[0]
        if d.kind == 'type' and language in ('java', 'csharp', 'go') and not d.owner:
            candidates = _type_candidates(view, path, facts, type_name, language)
            if candidates and (len(candidates) != 1 or candidates[0] != d):
                return (), ''
        if d.kind == 'type':
            owner_paths = _package_paths(view, posixpath.dirname(path), 'go') if language == 'go' else (path,)
            return owner_paths or (path,), d.name
        return (), ''
    imp = imports[0]
    if not imp.remote or imp.remote == 'default':
        return (), ''
    paths = _module_files(view, path, imp.module, language)
    types = [d for d in _import_target(view, path, imp, language) if d.kind == 'type']
    return ((types[0].path,), types[0].name) if len(types) == 1 else ((), '')


def _type_candidates(view, path, facts, name, language):
    modules = []
    if facts.package:
        modules.append(posixpath.dirname(path) if language == 'go' else facts.package)
    if language == 'csharp':
        modules.extend(i.module for i in facts.imports if i.local == '*' and i.remote == '*' and not i.scope)
    result = []
    for module in dict.fromkeys(modules):
        result.extend(d for d in _package_select(view, _package_paths(view, module, language), name, language, path) if d.kind == 'type')
    return list(dict.fromkeys(result))


def _rust_qualifier(view, path, ref):
    data = view.files[path].encode('utf-8')
    before = data[:ref.start].decode('utf-8', errors='ignore')
    match = re.search(r'((?:::)?(?:[A-Za-z_]\w*::)+)$', before)
    return match.group(1)[:-2] if match else ''


def resolve_reference(view, path: str, ref: Reference, *, prefix=False) -> Resolution:
    """Resolve a reference to at most one current definition.

    Ambiguity returns no matches and sets ambiguous. blocked means lexical or
    qualified evidence rules out an unqualified name-only history fallback.
    Prefix matching is subject to the same uniqueness rule.
    """
    if path not in view.files or not ref.name:
        return Resolution()
    facts = view.code(path)
    language = CODE_LANGUAGES.get(PurePosixPath(path).suffix.lower(), '')
    receiver = ref.receiver or (_rust_qualifier(view, path, ref) if language == 'rust' else '')
    if not receiver and language == 'zig':
        before = view.files[path].encode('utf-8')[:ref.start].decode('utf-8', errors='ignore')
        match = re.search(r'\b([A-Za-z_]\w*)\.$', before)
        receiver = match.group(1) if match else ''
    if receiver:
        rust_scoped = language == 'rust' and bool(_rust_qualifier(view, path, ref))
        if receiver in ('self', 'this') and not rust_scoped:
            owners = [s.split(':', 1)[1].rsplit('@', 1)[0] for s in ref.scope if s.startswith('class:')]
            if owners:
                class_scope = next(s for s in reversed(ref.scope) if s.startswith('class:'))
                methods = _select(view, (path,), ref.name, prefix, owners[-1])
                return _result([d for d in methods if d.scope and d.scope[-1] == class_scope], 'receiver_type')
            return Resolution(reason='receiver_type', blocked=True)
        if rust_scoped:
            parts = receiver.split('::')
            defs, imports = _bindings(facts, parts[0], ref.scope)
            if defs or len(imports) > 1:
                return Resolution(reason='module_path', ambiguous=len(defs) + len(imports) > 1, blocked=True)
            if imports:
                imp = imports[0]
                if imp.module.startswith('::'):
                    return Resolution(reason='module_path', blocked=True)
                receiver = '::'.join(filter(None, (imp.module, imp.remote, *parts[1:])))
            paths = _module_files(view, path, receiver, language)
            if len(paths) > 1:
                return Resolution(reason='module_path', ambiguous=True, blocked=True)
            return _result(_select(view, paths, ref.name, prefix), 'module_path')
        if re.fullmatch(r'[A-Za-z_]\w*', receiver):
            defs, imports = (_go_bindings(view, path, facts, receiver, ref.scope) if language == 'go'
                             else _bindings(facts, receiver, ref.scope))
            if language == 'zig' and len(defs) == len(imports) == 1:
                d, imp = defs[0], imports[0]
                declaration = view.files[path].encode('utf-8')[d.start:d.end].decode('utf-8')
                if d.kind == 'variable' and d.scope == imp.scope and re.search(r'@import\s*\(\s*"' + re.escape(imp.module) + r'"\s*\)', declaration):
                    defs = []
            if len(defs) + len(imports) > 1:
                return Resolution(reason='receiver_type', ambiguous=True, blocked=True)
            if defs:
                paths, owner = _type_target(view, path, facts, ref.scope, defs[0].type_name, language)
                if language in ('go', 'java', 'csharp') and paths:
                    select = _package_select(view, paths, ref.name, language, path, prefix, owner)
                else:
                    select = _select(view, paths, ref.name, prefix, owner) if paths == (path,) else _external_select(view, paths, ref.name, language, prefix, owner)
                return _result(select, 'receiver_type')
            if imports:
                imp = imports[0]
                if language == 'go':
                    return _result(_package_select(view, _go_paths(view, path, imp, receiver), ref.name, language, path, prefix), 'module_path')
                if imp.remote:
                    types = _import_target(view, path, imp, language)
                    if len(types) == 1 and types[0].kind == 'type':
                        if language == 'java':
                            return _result(_package_select(view, (types[0].path,), ref.name, language, path, prefix, types[0].name), 'receiver_type')
                        return _result(_external_select(view, (types[0].path,), ref.name, language, prefix, types[0].name), 'receiver_type')
                    if language == 'python':
                        module = imp.module + ('' if imp.module.endswith('.') else '.') + imp.remote
                        return _result(_select(view, _module_files(view, path, module, language), ref.name, prefix), 'module_path')
                    return Resolution(reason='explicit_import', ambiguous=len(types) > 1, blocked=True)
                return _result(_external_select(view, _module_files(view, path, imp.module, language), ref.name, language, prefix), 'module_path')

        if language in ('java', 'csharp', 'go') and re.fullmatch(r'[A-Za-z_]\w*', receiver):
            types = _type_candidates(view, path, facts, receiver, language)
            if len(types) > 1:
                return Resolution(reason='receiver_type', ambiguous=True, blocked=True)
            if types:
                return _result(_package_select(view, (types[0].path,), ref.name, language, path, prefix, types[0].name), 'receiver_type')
        return Resolution(reason='qualified', blocked=True)

    defs, imports = _bindings(facts, ref.name, ref.scope)
    if len(defs) + len(imports) > 1:
        return Resolution(reason='local_binding', ambiguous=True, blocked=True)
    if defs:
        return _result(defs, 'local_binding')
    if imports:
        imp = imports[0]
        if not imp.remote or imp.remote == 'default':
            return Resolution(reason='explicit_import', blocked=True)
        return _result(_import_target(view, path, imp, language), 'explicit_import')
    if language in ('go', 'java', 'csharp'):
        modules = []
        if facts.package:
            modules.append(posixpath.dirname(path) if language == 'go' else facts.package)
        if language == 'csharp':
            modules.extend(i.module for i in facts.imports if i.local == '*' and i.remote == '*' and _visible(ref.scope, i.scope))
        matches = []
        for module in dict.fromkeys(modules):
            matches.extend(_package_select(view, _package_paths(view, module, language), ref.name, language, path, prefix))
        if matches:
            return _result(matches, 'package')
    if language in ('c', 'cpp'):
        includes = [i for i in facts.imports if i.local == '*' and i.remote == '*' and _visible(ref.scope, i.scope)]
        matches = []
        for imp in includes:
            paths = _module_files(view, path, imp.module, language)
            if len(paths) != 1:
                return Resolution(reason='explicit_import', blocked=True)
            matches.extend(_select(view, paths, ref.name, prefix))
        if includes:
            return _result(matches, 'explicit_import')
    if prefix:
        candidates = [d for d in facts.definitions if not d.owner and _visible(ref.scope, d.scope) and d.name.startswith(ref.name)]
        return _result(candidates, 'local_binding', blocked=bool(candidates))
    return Resolution()


def can_reference(view, path: str, ref: Reference, definition: Definition, old_name: str) -> bool:
    """Whether an old reference has unique binding evidence for a current definition.

    For an unchanged import spelling, check its direct provider using the new
    name. No unqualified name-only or whole-project fallback is performed.
    """
    if definition.path not in view.files or path not in view.files:
        return False
    facts = view.code(path)
    defs, imports = _bindings(facts, ref.name, ref.scope) if not ref.receiver else ([], [])
    alias_of_old = len(imports) == 1 and imports[0].remote == old_name
    if ref.name != old_name and not alias_of_old:
        return False
    original = resolve_reference(view, path, ref)
    if original.ambiguous or (original.matches and original.matches != (definition,)):
        return False
    if original.matches == (definition,):
        return True
    language = CODE_LANGUAGES.get(PurePosixPath(path).suffix.lower(), '')
    zig_qualified = language == 'zig' and bool(re.search(
        r'\b[A-Za-z_]\w*\.$', view.files[path].encode('utf-8')[:ref.start].decode('utf-8', errors='ignore')))
    if not ref.receiver and not zig_qualified and not (language == 'rust' and _rust_qualifier(view, path, ref)):
        if not defs and len(imports) == 1 and imports[0].remote == old_name:
            result = _result(_import_target(view, path, imports[0], language, definition.name), 'explicit_import')
            return result.matches == (definition,)
        if original.blocked:
            return False
    modern = resolve_reference(view, path, replace(ref, name=definition.name))
    return not modern.ambiguous and modern.matches == (definition,)
