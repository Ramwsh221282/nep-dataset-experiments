"""Conservative Tree-sitter facts for the code languages supported by context.

Offsets are UTF-8 bytes. Scope IDs identify lexical ancestors, not symbols;
consumers must still apply language-specific visibility and import resolution.
"""
from functools import lru_cache
import re
from pathlib import Path, PurePosixPath

from tree_sitter_language_pack import get_language, get_parser

from .index_models import Definition, FileFacts, Import, Reference


CODE_LANGUAGES = {
    '.py': 'python', '.pyi': 'python', '.rs': 'rust',
    '.js': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript', '.jsx': 'javascript',
    '.ts': 'typescript', '.mts': 'typescript', '.cts': 'typescript', '.tsx': 'tsx',
    '.cs': 'csharp', '.zig': 'zig', '.c': 'c', '.h': 'c',
    '.cpp': 'cpp', '.cc': 'cpp', '.cxx': 'cpp', '.hpp': 'cpp', '.hh': 'cpp', '.hxx': 'cpp',
    '.java': 'java', '.go': 'go',
}

_DOCUMENT_EXTENSIONS = frozenset({'.md', '.markdown', '.html', '.htm', '.xml', '.svg',
                                  '.json', '.jsonc', '.json5', '.jsonl', '.ndjson', '.toml'})
_DOCUMENT_URI = re.compile(r'[\w./~%+@-]+(?:#[\w./~%+@:=,-]*)?\Z')
_MAX_DOCUMENT_URI_BYTES = 512
_QUERY_DIR = Path(__file__).resolve().parent.parent / 'scms'
_FUNCTIONS = {'function_definition', 'function_declaration', 'function_expression', 'generator_function',
              'generator_function_declaration', 'function_item', 'function_signature_item',
              'method_definition', 'method_declaration', 'constructor_declaration', 'FnProto'}
_ANONYMOUS_FUNCTIONS = {'arrow_function', 'lambda', 'lambda_expression', 'closure_expression', 'func_literal'}
_CLASSES = {'class_definition', 'class_declaration', 'class_specifier', 'struct_specifier', 'union_specifier',
            'struct_item', 'union_item', 'enum_item', 'trait_item', 'interface_declaration', 'record_declaration',
            'impl_item', 'type_spec', 'namespace_definition'}
_NAMESPACES = {'namespace_declaration', 'namespace_definition'}
_BLOCKS = {'block', 'statement_block', 'compound_statement', 'for_statement', 'for_in_statement',
           'for_expression', 'catch_clause', 'lambda', 'lambda_expression', 'arrow_function',
           'closure_expression', 'func_literal'}
_IDENTIFIERS = {'identifier', 'type_identifier', 'property_identifier', 'private_property_identifier', 'field_identifier', 'IDENTIFIER'}
_VARIABLES = {'variable_declarator', 'let_declaration', 'assignment', 'VarDecl', 'field_declaration'}
_PARAMETERS = {'parameter', 'parameter_declaration', 'formal_parameter', 'required_parameter',
               'optional_parameter', 'typed_parameter', 'typed_default_parameter', 'ParamDecl'}


@lru_cache(maxsize=None)
def _queries(language):
    grammar = get_language(language)
    return tuple(grammar.query((_QUERY_DIR / language / (name + '.scm')).read_text())
                 for name in ('definitions', 'references', 'imports'))


def _text(node, data):
    return data[node.start_byte:node.end_byte].decode('utf-8') if node else ''


def _field(node, *names):
    for name in names:
        child = node.child_by_field_name(name)
        if child is not None:
            return child
    return None


def _identifier(node):
    if node is None:
        return None
    if node.type in _IDENTIFIERS:
        return node
    child = _field(node, 'declarator', 'name', 'pattern', 'left', 'function', 'variable_type_function', 'parameter')
    if child is not None:
        return _identifier(child)
    return None


def _name(node):
    if node.type in _IDENTIFIERS:
        return node
    name = _field(node, 'name', 'declarator', 'pattern', 'left', 'function', 'variable_type_function', 'parameter', 'property')
    if name is None and node.type in {'typed_parameter', 'typed_default_parameter'}:
        name = next((c for c in node.named_children if c.type == 'identifier'), None)
    return _identifier(name)


def _scope_node(node):
    # The function body itself is not a separate scope: parameters and locals share it.
    if node.type == 'ERROR' and node.children and node.children[0].type in {'function', 'def', 'fn'}:
        return any(c.type in {'parameters', 'formal_parameters', 'parameter_list'} and not c.has_error
                   for c in node.named_children)
    if node.type in _FUNCTIONS or node.type in _CLASSES or node.type in _ANONYMOUS_FUNCTIONS or node.type in _NAMESPACES:
        return True
    if node.type in _BLOCKS:
        root = node
        while root.parent:
            root = root.parent
        if root.type == 'module':
            return node.type in {'lambda'}
        parent = node.parent
        return not (parent and (parent.type in _FUNCTIONS or parent.type in _CLASSES
                                or parent.type in _ANONYMOUS_FUNCTIONS or parent.type == 'impl_item'))
    return False


def _scope(node, data):
    ancestors = []
    node = node.parent
    while node:
        if _scope_node(node):
            error_name = next((c for c in node.named_children if c.type == 'identifier'), None) if node.type == 'ERROR' else None
            name = _text(_name(node) or _field(node, 'type') or error_name, data)
            category = ('function' if node.type in _FUNCTIONS or node.type in _ANONYMOUS_FUNCTIONS or node.type == 'ERROR'
                        else 'namespace' if node.type in _NAMESPACES else 'class' if node.type in _CLASSES else 'block')
            ancestors.append(f'{category}:{name}@{node.start_byte}')
        node = node.parent
    return tuple(reversed(ancestors))


def _owner(node, data):
    parent = node.parent
    while parent:
        if parent.type in _FUNCTIONS or parent.type in _ANONYMOUS_FUNCTIONS or (parent.type == 'ERROR' and _scope_node(parent)):
            return ''
        if parent.type in _CLASSES:
            owner = _text(_name(parent) or _field(parent, 'type'), data)
            if parent.type == 'namespace_definition':
                return ''
            # Qualified Rust impl types still own fields/methods by their simple name.
            if parent.type == 'impl_item':
                owner = owner.split('<', 1)[0].rsplit('::', 1)[-1]
            return owner
        parent = parent.parent
    return ''


def _type(node, data):
    typ = _field(node, 'type')
    if typ is None and node.parent and node.parent.type in {'variable_declaration', 'local_variable_declaration', 'declaration'}:
        typ = _field(node.parent, 'type')
    text = _text(typ, data).strip()
    return text.lstrip(':').strip()


def _document_reference(node, data):
    if node.parent and node.parent.type == 'concatenated_string':
        return None
    literal = _text(node, data)
    if (node.has_error or len(literal.encode('utf-8')) > _MAX_DOCUMENT_URI_BYTES + 2
            or len(literal) < 3 or literal[0] not in "'\"" or literal[-1] != literal[0]):
        return None
    uri = literal[1:-1]
    if not _DOCUMENT_URI.fullmatch(uri) or uri.startswith('/'):
        return None
    if PurePosixPath(uri.split('#', 1)[0]).suffix.lower() not in _DOCUMENT_EXTENSIONS:
        return None
    return Reference(name=uri, start=node.start_byte + 1, end=node.end_byte - 1,
                     scope=_scope(node, data), receiver='@document')


def _invalid_header(node):
    """Allow errors only inside a function's own body, not its signature."""
    if node.type not in _FUNCTIONS and node.type not in _CLASSES:
        return node.has_error
    body = _field(node, 'body')
    for child in node.children:
        if body is not None and child == body:
            continue
        if child.has_error or child.is_missing:
            return True
    return False


def _import_facts(node, language, data):
    scope = _scope(node, data)
    result = []
    def add(local, module, remote='', explicit_alias=False):
        if local and module:
            result.append(Import(local=local, module=module, remote=remote, scope=scope, explicit_alias=explicit_alias))
    def text(n): return _text(n, data)
    if language == 'python':
        module_node = _field(node, 'module_name')
        module = text(module_node)
        for child in node.named_children:
            if child == module_node:
                continue
            if child.type == 'aliased_import':
                original = text(_field(child, 'name'))
                alias = text(_field(child, 'alias'))
            elif child.type in {'dotted_name', 'identifier'}:
                original = text(child)
                alias = ''
            else:
                continue
            if module_node:
                add(alias or original, module, original)
            else:
                add(alias or original.split('.')[0], original if alias else original.split('.')[0])
    elif language in {'javascript', 'typescript', 'tsx'}:
        module = text(_field(node, 'source')).strip("'\"")
        def visit(n):
            if n.type == 'import_specifier':
                remote = text(_field(n, 'name'))
                add(text(_field(n, 'alias')) or remote, module, remote)
            elif n.type == 'namespace_import':
                ids = [c for c in n.named_children if c.type == 'identifier']
                if ids: add(text(ids[-1]), module)
            elif n.type == 'import_clause':
                for c in n.named_children:
                    if c.type == 'identifier': add(text(c), module, 'default')
                    else: visit(c)
            else:
                for c in n.named_children: visit(c)
        visit(node)
    elif language in {'c', 'cpp'}:
        target = _field(node, 'path')
        literal = text(target)
        if (target and target.type == 'string_literal' and not target.has_error
                and len(literal) > 2 and literal[0] == literal[-1] == '"'
                and '\\' not in literal and '\n' not in literal):
            add('*', literal[1:-1], '*')
    elif language == 'zig':
        if node.named_children and text(node.named_children[0]) == '@import':
            parent = node.parent
            while parent and parent.type != 'VarDecl':
                parent = parent.parent
            name = _name(parent) if parent else None
            argument = next((c for c in node.named_children if c.type == 'FnCallArguments'), None)
            if name and argument and not argument.has_error:
                raw = text(argument).strip()
                if re.fullmatch(r'\(\s*"[^"\\\n]+"\s*\)', raw):
                    # Require the entire initializer, not a nested call or member access.
                    declaration = text(parent)
                    _, separator, initializer = declaration.partition('=')
                    if (separator and re.fullmatch(r'@import\s*' + re.escape(raw), text(node))
                            and initializer.strip().removesuffix(';').strip() == text(node)):
                        add(text(name), raw[1:-1].strip()[1:-1])
    elif language == 'rust':
        def visit(n, prefix=''):
            if n.type == 'scoped_use_list':
                p = text(_field(n, 'path'))
                visit(_field(n, 'list'), '::'.join(filter(None, (prefix, p))))
            elif n.type == 'use_list':
                for c in n.named_children: visit(c, prefix)
            elif n.type == 'use_as_clause':
                path = text(_field(n, 'path'))
                parts = path.split('::')
                add(text(_field(n, 'alias')), '::'.join(filter(None, (prefix, *parts[:-1]))), parts[-1])
            elif n.type in {'identifier', 'scoped_identifier', 'self'}:
                parts = text(n).split('::')
                if parts[-1] == 'self':
                    add(prefix.split('::')[-1], prefix)
                else:
                    module = '::'.join(filter(None, (prefix, *parts[:-1])))
                    if module:
                        add(parts[-1], module, parts[-1])
                    else:
                        add(parts[-1], parts[-1])
        arg = _field(node, 'argument')
        if arg: visit(arg)
    elif language == 'go' and node.type == 'import_spec':
        literal = text(_field(node, 'path'))
        if len(literal) >= 2 and literal[0] in '"`' and literal[-1] == literal[0] and '\\' not in literal:
            module = literal[1:-1]
            local = text(_field(node, 'name')) or module.rsplit('/', 1)[-1]
            add('*' if local == '.' else local, module, '*' if local == '.' else '', bool(_field(node, 'name')))
    elif language == 'csharp':
        raw = text(node).strip()
        match = re.fullmatch(r'(?:global\s+)?using\s+(?:(static)\s+)?(?:(\w+)\s*=\s*)?([\w.]+)\s*;', raw)
        if match:
            static, alias, module = match.groups()
            if alias:
                add(alias, module)
            elif not static:
                add('*', module, '*')
    elif language == 'java':
        raw = text(node).strip().removeprefix('import ').removesuffix(';').strip()
        if '.' in raw and not raw.endswith('.*'):
            module, remote = raw.rsplit('.', 1)
            add(remote, module, remote)
    return result


def _package(root, language, data):
    kinds = {'go': {'package_clause'}, 'java': {'package_declaration'},
             'csharp': {'namespace_declaration', 'file_scoped_namespace_declaration'}}
    nodes = [n for n in root.named_children if n.type in kinds.get(language, set())]
    if len(nodes) != 1 or nodes[0].has_error:
        return ''
    node = nodes[0]
    if language == 'csharp':
        # Multiple or nested namespaces cannot be represented by a file-wide key.
        def nested(n):
            return any(c.type in kinds['csharp'] or nested(c) for c in n.named_children)
        if nested(node):
            return ''
    text = _text(node, data)
    match = re.match(r'(?:package|namespace)\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*(?:;|\{|$)', text)
    return match.group(1) if match else ''


def _metadata(node, declaration, name, language, data):
    direct = False
    if language in {'javascript', 'typescript', 'tsx'}:
        parent = declaration.parent
        if parent and parent.type == 'export_statement':
            direct = not re.match(rb'export\s+default\b', data[parent.start_byte:parent.end_byte].lstrip())
    visibility = ''
    if language in {'java', 'csharp'}:
        current = declaration
        while current.parent and current.type in {'variable_declarator', 'variable_declaration'}:
            current = current.parent
        header = data[current.start_byte:name.start_byte].decode('utf-8')
        match = re.search(r'\b(public|private|protected|internal)\b', header)
        visibility = match.group(1) if match else ''
    return direct, visibility


def extract_code(path: str, source: str) -> FileFacts:
    """Extract syntactic facts; unsupported extensions raise ValueError."""
    language = CODE_LANGUAGES.get(PurePosixPath(path).suffix.lower())
    if language is None:
        raise ValueError(f'Unsupported code file: {path}')
    data = source.encode('utf-8')
    root = get_parser(language).parse(data).root_node
    definitions_query, references_query, imports_query = _queries(language)
    import_nodes = [n for n, capture in imports_query.captures(root) if capture == 'import']
    # Go's import declaration wraps import specs; only process the specs.
    imports = []
    for node in import_nodes:
        imports.extend(_import_facts(node, language, data))
    def inside_import(node):
        parent = node
        while parent:
            if parent.type in {'import_statement', 'import_from_statement', 'use_declaration',
                               'import_declaration', 'using_directive', 'preproc_include'}:
                return True
            parent = parent.parent
        return False
    definitions = []
    names = set()
    for node, capture in definitions_query.captures(root):
        field_name = None
        if capture == 'field':
            if node.type == 'function_declarator':
                continue
            field_name = _identifier(node)
            node = node.parent
        if inside_import(node):
            continue
        if node.type == 'ERROR':
            children = node.children
            if not children or children[0].type not in {'function', 'def', 'fn'}:
                continue
            name = next((c for c in node.named_children if c.type == 'identifier'), None)
            params = _field(node, 'parameters') or next((c for c in node.named_children
                                                         if c.type in {'parameters', 'formal_parameters', 'parameter_list'}), None)
            if not name or not params or params.has_error:
                continue
            header = []
            for child in children:
                if child.type in {'{', ':'} and child.start_byte >= params.end_byte:
                    break
                header.append(child)
            if (len(header) < 3 or header[1] != name or params not in header
                    or any(c.has_error or c.is_missing for c in header if c != params)
                    or any(c.is_named for c in header[header.index(params) + 1:])):
                continue
            key = (name.start_byte, name.end_byte)
            if key not in names:
                names.add(key)
                owner = _owner(node, data)
                definitions.append(Definition(path=path, name=_text(name, data),
                                              kind='method' if owner else 'function',
                                              start=node.start_byte, end=params.end_byte,
                                              scope=_scope(node, data), owner=owner,
                                              name_start=name.start_byte, name_end=name.end_byte))
            continue
        name = field_name or _name(node)
        if not name or name.is_missing or _invalid_header(node):
            continue
        # A valid-looking parameter inside a malformed function signature is not a binding.
        ancestor = node.parent
        invalid_ancestor = False
        while ancestor and ancestor.type not in _FUNCTIONS and ancestor.type not in _ANONYMOUS_FUNCTIONS:
            if ancestor.type == 'ERROR' and ancestor.children and ancestor.children[0].type in {'function', 'def', 'fn'} and not _scope_node(ancestor):
                invalid_ancestor = True
                break
            ancestor = ancestor.parent
        if ancestor and _invalid_header(ancestor):
            body = _field(ancestor, 'body')
            invalid_ancestor = body is None or node.start_byte < body.start_byte
        if invalid_ancestor:
            continue
        # C/C++ declarations often wrap the actual declarator; avoid duplicate parameters.
        if node.type == 'field_declaration' and any(c.type == 'function_declarator' for c in node.named_children):
            continue
        key = (name.start_byte, name.end_byte)
        if key in names:
            continue
        kind = ('function' if node.type in _FUNCTIONS else 'parameter' if node.type in _PARAMETERS or node.type in _IDENTIFIERS
                else 'variable' if node.type in _VARIABLES or node.type in {'declaration', 'var_spec', 'const_spec', 'const_item', 'static_item', 'field_definition', 'public_field_definition'} else 'type')
        owner = _owner(node, data) if kind in {'function', 'variable'} else ''
        if node.type == 'method_declaration' and not owner:
            receiver = _field(node, 'receiver')
            if receiver:
                parameter = next((c for c in receiver.named_children if c.type == 'parameter_declaration'), None)
                owner = _type(parameter, data).lstrip('*') if parameter else ''
        if owner and kind == 'function': kind = 'method'
        declaration = node
        if node.type == 'FnProto' and node.parent and node.parent.type == 'Decl':
            declaration = node.parent
        elif node.type == 'variable_declarator' and node.parent and node.parent.type in {'lexical_declaration', 'variable_declaration', 'local_variable_declaration'}:
            if sum(c.type == 'variable_declarator' for c in node.parent.named_children) == 1:
                declaration = node.parent
        if declaration != node and declaration.has_error and node.type not in _FUNCTIONS:
            continue
        names.add(key)
        end = declaration.end_byte
        body = _field(node, 'body')
        if kind in {'function', 'method'} and body and body.has_error:
            end = body.start_byte
        direct_export, visibility = _metadata(node, declaration, name, language, data)
        definitions.append(Definition(path=path, name=_text(name, data), kind=kind,
                                      start=declaration.start_byte, end=end, scope=_scope(node, data),
                                      owner=owner, type_name=_type(node, data),
                                      name_start=name.start_byte, name_end=name.end_byte,
                                      direct_export=direct_export, visibility=visibility))
    references = []
    seen = set()
    for node, capture in references_query.captures(root):
        if capture == 'document':
            reference = _document_reference(node, data)
            if reference and (reference.start, reference.end) not in seen:
                seen.add((reference.start, reference.end))
                references.append(reference)
            continue
        key = (node.start_byte, node.end_byte)
        if key in names or key in seen or inside_import(node) or node.is_missing:
            continue
        seen.add(key)
        receiver = ''
        parent = node.parent
        if language == 'rust' and parent and parent.type in {'scoped_identifier', 'scoped_type_identifier'}:
            if _field(parent, 'name') == node:
                receiver = _text(_field(parent, 'path'), data)
        if parent and parent.type in {'member_expression', 'attribute', 'field_expression', 'selector_expression',
                                      'member_access_expression', 'method_invocation', 'field_access'}:
            member = _field(parent, 'property', 'attribute', 'field', 'name')
            if member == node:
                receiver = _text(_field(parent, 'object', 'value', 'operand', 'expression'), data)
        references.append(Reference(name=_text(node, data), start=node.start_byte, end=node.end_byte,
                                    scope=_scope(node, data), receiver=receiver))
    return FileFacts(definitions=tuple(sorted(definitions, key=lambda d: (d.start, d.name_start))),
                     references=tuple(sorted(references, key=lambda r: r.start)), imports=tuple(imports),
                     partial=root.has_error, package=_package(root, language, data))
