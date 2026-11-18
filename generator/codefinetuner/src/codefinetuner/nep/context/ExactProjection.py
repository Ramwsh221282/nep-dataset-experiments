"""Conservative, source-backed projection of one code declaration."""
from tree_sitter_language_pack import get_parser

from .ContextStructureParserAlgorithm import ContextFragment, ContextParseError


# Only declaration nodes with unambiguous source ownership are supported.
_DECLS = {
    'class_declaration', 'abstract_class_declaration', 'interface_declaration',
    'type_alias_declaration', 'enum_declaration', 'function_declaration',
    'generator_function_declaration', 'function_signature', 'method_definition',
    'abstract_method_signature', 'lexical_declaration', 'variable_declaration',
    'class_definition', 'function_definition', 'decorated_definition',
    'impl_item', 'trait_item', 'mod_item', 'struct_item', 'enum_item', 'union_item',
    'function_item', 'function_signature_item', 'type_item', 'const_item', 'static_item',
    'namespace_definition', 'namespace_declaration', 'method_declaration',
    'constructor_declaration', 'struct_declaration', 'record_declaration',
    'export_statement', 'internal_module', 'ambient_declaration', 'class_specifier',
    'struct_specifier', 'Decl', 'public_field_definition', 'field_definition',
    'field_declaration',
}
_LISTS = {'class_body', 'declaration_list', 'interface_body', 'statement_block',
          'namespace_body', 'field_declaration_list', 'enum_body'}
_PREFIXES = {'decorator', 'attribute_item'}
_OWNERS = {'class_declaration', 'abstract_class_declaration', 'interface_declaration',
           'class_definition', 'decorated_definition', 'impl_item', 'trait_item', 'mod_item',
           'namespace_definition', 'namespace_declaration', 'class_specifier', 'struct_specifier',
           'struct_declaration', 'record_declaration', 'export_statement', 'internal_module',
           'ambient_declaration'}
_BINDINGS = {'parameter', 'parameter_declaration', 'formal_parameter', 'required_parameter',
             'optional_parameter', 'typed_parameter', 'typed_default_parameter', 'ParamDecl',
             'assignment', 'variable_declarator', 'let_declaration', 'local_variable_declaration',
             'lexical_declaration', 'variable_declaration', 'VarDecl', 'declaration'}


def project(algorithm, path, text, start, end, javascript=False):
    from .CodeStructureSupport import _FUNCTIONS as code_functions, _OMIT
    from .JavascriptStructureSupport import _FUNCTIONS as js_functions, _Renderer

    source = text.encode('utf-8')
    try:
        root = get_parser(algorithm.language).parse(source).root_node
    except (ValueError, TypeError, LookupError, ImportError) as exc:
        raise ContextParseError(f'Cannot parse {path}: {exc}') from exc
    functions = js_functions if javascript else code_functions
    renderer = _Renderer(source, algorithm.jsx or path.lower().endswith('.js')) if javascript else None

    def body_of(node):
        if algorithm.language == 'zig' and node.type == 'Decl':
            if any(c.type == 'FnProto' for c in node.named_children):
                return next((c for c in node.named_children if c.type == 'Block'), None)
        if node.type not in functions:
            return None
        body = node.child_by_field_name('body')
        if body is None and node.type == 'anonymous_method_expression':
            body = next((c for c in node.named_children if c.type == 'block'), None)
        if body is None and node.type == 'function_definition' and algorithm.language == 'cpp':
            body = next((c for c in node.named_children if c.type == 'try_statement'), None)
        return body

    def safe(node, skip=None, header=False):
        if skip is not None and node.id == skip.id:
            return True
        if node.type == 'ERROR' or node.is_missing:
            return False
        if node.type in _OMIT or (javascript and node.type == 'class_static_block'):
            return False if header else True
        if node.type in functions or (node.type == 'Decl' and any(c.type == 'FnProto' for c in node.named_children)):
            if header:
                return False
            body = body_of(node)
            if body is None or body.is_missing or body.start_byte == body.end_byte:
                return False
            return all(safe(c, body) for c in node.children)
        return all(safe(c, skip, header) for c in node.children)

    def previous_prefixes(node):
        result = []
        prev = node.prev_named_sibling
        while prev is not None and prev.type in _PREFIXES:
            if source[prev.end_byte:(result[0].start_byte if result else node.start_byte)].strip():
                break
            result.insert(0, prev)
            prev = prev.prev_named_sibling
        return result

    # Normalize only whitespace, never widen an arbitrary interior range.
    while start < end and source[start:start + 1] in b' \t\r\n':
        start += 1
    while end > start and source[end - 1:end] in b' \t\r\n':
        end -= 1
    candidates = []
    bindings = []
    def visit(node, in_function=False):
        if node.end_byte < start or node.start_byte > end:
            return
        is_function = node.type in functions or (node.type == 'Decl' and body_of(node) is not None)
        if node.type in _DECLS and not in_function:
            prefixes = previous_prefixes(node)
            body = body_of(node)
            header_end = body.start_byte if body is not None else -1
            while header_end > node.start_byte and source[header_end - 1:header_end] in b' \t\r\n':
                header_end -= 1
            if end in {node.end_byte, header_end} and start in {node.start_byte, prefixes[0].start_byte if prefixes else node.start_byte}:
                candidates.append((node, prefixes))
        if in_function and node.type in _BINDINGS and node.start_byte == start and node.end_byte == end:
            bindings.append(node)
        for child in node.named_children:
            visit(child, in_function or is_function)
    visit(root)
    if not candidates and len(bindings) == 1:
        node = bindings[0]
        # Binding projections retain only an explicitly typed name, never an
        # initializer, default expression, or surrounding executable scope.
        typ = node.child_by_field_name('type')
        name = (node.child_by_field_name('name') or node.child_by_field_name('pattern')
                or node.child_by_field_name('left') or node.child_by_field_name('declarator'))
        if node.type in {'typed_parameter', 'typed_default_parameter'}:
            name = next((c for c in node.named_children if c.type == 'identifier'), None)
        if node.type in {'lexical_declaration', 'variable_declaration', 'local_variable_declaration'}:
            children = [c for c in node.named_children if c.type == 'variable_declarator']
            if len(children) == 1:
                child = children[0]
                name = child.child_by_field_name('name')
                typ = child.child_by_field_name('type') or node.child_by_field_name('type')
        if not name or name.type not in {'identifier', 'field_identifier', 'IDENTIFIER'} or not typ:
            return []
        def safe_type(n):
            if n.type in {'call_expression', 'call', 'function_call_expression', 'assignment',
                          'lambda', 'lambda_expression', 'arrow_function', 'ERROR'} or n.is_missing:
                return False
            return all(safe_type(c) for c in n.children)
        if not safe_type(typ):
            return []
        value = source[name.start_byte:name.end_byte].decode() + ': ' + source[typ.start_byte:typ.end_byte].decode().lstrip(':').strip()
        return [ContextFragment(path, getattr(algorithm, 'format', algorithm.language), node.type,
                                value, source[name.start_byte:name.end_byte].decode(), 'extracted', start, end)]
    if len(candidates) != 1:
        return []
    node, prefixes = candidates[0]
    if not safe(node) or any(not safe(p, header=True) for p in prefixes):
        return []
    if javascript:
        value = renderer.render(node)
    else:
        edits = []
        algorithm._collect(node, source, edits)
        value = algorithm._render(source, node.start_byte, node.end_byte, edits)
    if prefixes:
        value = source[prefixes[0].start_byte:node.start_byte].decode() + value

    current = node
    parent = node.parent
    while parent is not None and parent.id != root.id:
        if parent.type in functions:
            return []
        if parent.type in _LISTS or (algorithm.language == 'python' and parent.type == 'block'):
            if algorithm.language == 'python' and parent.type == 'block':
                if parent.parent is None or parent.parent.type != 'class_definition':
                    return []
            else:
                if source[parent.start_byte:parent.start_byte + 1] != b'{' or source[parent.end_byte - 1:parent.end_byte] != b'}':
                    return []
                value = '{\n' + value + '\n}'
        else:
            # Preserve owner headers and transparent wrappers, but never other
            # declarations or unvalidated syntax around the selected child.
            if parent.type not in _OWNERS or not safe(parent, current, header=True):
                return []
            value = (source[parent.start_byte:current.start_byte].decode() + value +
                     source[current.end_byte:parent.end_byte].decode())
        current, parent = parent, parent.parent
    name = node.child_by_field_name('name')
    selector = source[name.start_byte:name.end_byte].decode() if name else ''
    return [ContextFragment(path, getattr(algorithm, 'format', algorithm.language), node.type,
                            value.strip(), selector, 'extracted', start, end)]
