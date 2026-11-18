"""Tree-sitter based structural extraction shared by JS, TS, JSX and TSX."""
from tree_sitter_language_pack import get_parser

from .ContextStructureParserAlgorithm import (
    ContextFragment, ContextParseError, ContextStructureParserAlgorithm,
)


_FUNCTIONS = {
    "function_declaration", "generator_function_declaration", "function_expression",
    "generator_function", "arrow_function", "method_definition",
}
_JSX = {"jsx_element", "jsx_self_closing_element"}
_DECLARATIONS = {
    "import_statement", "export_statement", "lexical_declaration", "variable_declaration",
    "function_declaration", "generator_function_declaration", "class_declaration",
    "abstract_class_declaration", "interface_declaration", "type_alias_declaration",
    "enum_declaration", "internal_module", "module", "ambient_declaration",
    "function_signature", "abstract_method_signature",
}
_VARIABLES = {"lexical_declaration", "variable_declaration"}
_PLACEHOLDER = "{/* тело функции опущено */}"


class JavascriptStructureSupport(ContextStructureParserAlgorithm):
    language = "javascript"
    format = "javascript"
    jsx = False

    def project_range(self, path, source_text, start_byte, end_byte):
        from .ExactProjection import project
        return project(self, path, source_text, start_byte, end_byte, javascript=True)

    def parse(self, path: str, source_text: str) -> list[ContextFragment]:
        try:
            source = source_text.encode("utf-8")
            tree = get_parser(self.language).parse(source)
        except (UnicodeError, ValueError, TypeError, LookupError, ImportError) as exc:
            raise ContextParseError(f"Cannot parse {path}: {exc}") from exc
        root = tree.root_node
        if root.has_error:
            raise ContextParseError(f"Malformed {self.format} source: {path}")
        renderer = _Renderer(source, self.jsx)
        # JavaScript's grammar accepts JSX even in files named .js.
        if self.language == "javascript" and path.lower().endswith(".js"):
            renderer.jsx = renderer.contains_jsx(root)
        fragments = []
        for node in root.named_children:
            if node.type not in _DECLARATIONS:
                continue
            text = renderer.render(node)
            name = node.child_by_field_name("name")
            declaration = node.child_by_field_name("declaration")
            if name is None and declaration is not None:
                name = declaration.child_by_field_name("name")
            fragments.append(ContextFragment(
                path=path, format=self.format, kind=node.type, code_text=text,
                selector=renderer.text(name) if name else "",
                start_byte=node.start_byte, end_byte=node.end_byte,
            ))
        return fragments


class _Renderer:
    def __init__(self, source: bytes, jsx: bool):
        self.source = source
        self.jsx = jsx

    def text(self, node):
        return self.source[node.start_byte:node.end_byte].decode("utf-8")

    def replace_children(self, node, replacements):
        result = bytearray()
        pos = node.start_byte
        for child, replacement in replacements:
            result.extend(self.source[pos:child.start_byte])
            result.extend(replacement.encode("utf-8"))
            pos = child.end_byte
        result.extend(self.source[pos:node.end_byte])
        return result.decode("utf-8")

    def contains_jsx(self, node):
        return node.type in _JSX or any(self.contains_jsx(c) for c in node.named_children)

    def contains_returned_jsx(self, node):
        if node.type in _JSX:
            return True
        if node.type in _FUNCTIONS:
            return False
        if node.type == "call_expression":
            function = node.child_by_field_name("function")
            prop = function.child_by_field_name("property") if function else None
            if prop is not None and self.text(prop) in {"map", "flatMap"}:
                args = node.child_by_field_name("arguments")
                if args is not None and any(self.returns_jsx(c) for c in args.named_children if c.type in _FUNCTIONS):
                    return True
        return any(self.contains_returned_jsx(c) for c in node.named_children)

    def returns_jsx(self, node):
        """Find JSX in this function's returns, not in nested function bodies."""
        body = node.child_by_field_name("body")
        if body is None:
            return False
        if body.type != "statement_block":
            return self.contains_returned_jsx(body)

        def visit(child):
            if child.type in _FUNCTIONS:
                return False
            if child.type == "return_statement":
                return self.contains_returned_jsx(child)
            return any(visit(c) for c in child.named_children)
        return visit(body)

    def in_attribute(self, node):
        parent = node.parent
        while parent is not None:
            if parent.type == "jsx_attribute":
                return True
            if parent.type in _JSX or parent.type in _FUNCTIONS:
                return False
            parent = parent.parent
        return False

    def is_callback(self, node):
        parent = node.parent
        while parent is not None and parent.type in {"parenthesized_expression", "as_expression", "satisfies_expression"}:
            parent = parent.parent
        return parent is not None and parent.type == "arguments"

    def is_render_method(self, node):
        parent = node.parent
        if node.type == "method_definition":
            name = node.child_by_field_name("name")
            return (parent is not None and parent.type == "class_body"
                    and name is not None and self.text(name) == "render"
                    and not any(c.type in {"get", "set"} for c in node.children))
        while parent is not None and parent.type in {"parenthesized_expression", "as_expression", "satisfies_expression"}:
            parent = parent.parent
        if parent and parent.type in {"public_field_definition", "field_definition"}:
            name = parent.child_by_field_name("name") or parent.child_by_field_name("property")
            return name is not None and self.text(name) == "render"
        return True

    def render(self, node):
        if node.type == "class_static_block":
            return ""
        if node.type in _FUNCTIONS:
            body = node.child_by_field_name("body")
            if body is not None:
                is_component = (self.jsx and not self.in_attribute(node)
                                and self.is_render_method(node) and self.returns_jsx(node))
                if is_component:
                    replacement = self.component_body(body, callback=self.is_callback(node))
                elif node.type in {"arrow_function", "function_expression", "generator_function"}:
                    replacement = _PLACEHOLDER
                else:
                    replacement = ";"
                return self.replace_children(node, [
                    (c, replacement if c == body else self.render(c))
                    for c in node.named_children
                ])

        return self.replace_children(node, [(c, self.render(c)) for c in node.named_children])

    def component_body(self, body, callback=False):
        if body.type != "statement_block":
            return self.render(body)
        replacements = []
        for child in body.named_children:
            if not callback and (child.type in _VARIABLES or child.type in _FUNCTIONS or child.type in {
                "class_declaration", "interface_declaration", "type_alias_declaration",
            }):
                value = self.render(child)
            elif child.type == "return_statement":
                value = self.render(child) if self.contains_jsx(child) else ""
            elif child.type in {"if_statement", "switch_statement", "switch_body", "switch_case", "switch_default", "try_statement", "catch_clause", "finally_clause", "statement_block"} and self.contains_jsx(child):
                value = self.component_control(child, callback)
            else:
                value = ""
            replacements.append((child, value))
        return self.replace_children(body, replacements)

    def component_control(self, node, callback=False):
        if node.type == "statement_block":
            return self.component_body(node, callback)
        replacements = []
        for child in node.named_children:
            if child.type == "return_statement":
                value = self.render(child) if self.contains_jsx(child) else ""
            elif child.type in {"statement_block", "if_statement", "switch_body", "switch_case", "switch_default", "try_statement", "catch_clause", "finally_clause"}:
                value = self.component_control(child, callback)
            elif node.type in {"switch_case", "switch_default"} and child != node.child_by_field_name("value"):
                value = self.render(child) if not callback and child.type in _VARIABLES else ""
            elif child.type.endswith("statement"):
                value = ""
            else:
                value = self.render(child)
            replacements.append((child, value))
        return self.replace_children(node, replacements)
