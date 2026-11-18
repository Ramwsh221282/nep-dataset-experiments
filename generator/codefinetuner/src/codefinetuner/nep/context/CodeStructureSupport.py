"""Tree-sitter based, byte-preserving structural projections for code context."""
from .ContextStructureParserAlgorithm import (
    ContextFragment, ContextParseError, ContextStructureParserAlgorithm,
)


# Only these top-level constructs become candidates. In particular, arbitrary
# statements and preprocessor macro definitions are not safe context.
_TOP_LEVEL = {
    "python": {"import_statement", "import_from_statement", "future_import_statement", "decorated_definition", "class_definition", "function_definition", "expression_statement", "type_alias_statement"},
    "rust": {"attribute_item", "inner_attribute_item", "use_declaration", "extern_crate_declaration", "mod_item", "struct_item", "enum_item", "union_item", "trait_item", "impl_item", "function_item", "function_signature_item", "const_item", "static_item", "type_item", "foreign_mod_item"},
    "csharp": {"using_directive", "extern_alias_directive", "namespace_declaration", "file_scoped_namespace_declaration", "class_declaration", "struct_declaration", "interface_declaration", "enum_declaration", "record_declaration", "delegate_declaration"},
    "zig": {"Decl"},
    "c": {"preproc_include", "declaration", "type_definition", "struct_specifier", "union_specifier", "enum_specifier", "function_definition"},
    "cpp": {"preproc_include", "declaration", "type_definition", "struct_specifier", "union_specifier", "enum_specifier", "class_specifier", "function_definition", "namespace_definition", "template_declaration", "alias_declaration", "using_declaration", "linkage_specification", "concept_definition"},
    "java": {"package_declaration", "import_declaration", "class_declaration", "interface_declaration", "enum_declaration", "record_declaration", "annotation_type_declaration"},
    "go": {"package_clause", "import_declaration", "type_declaration", "const_declaration", "var_declaration", "function_declaration", "method_declaration"},
}
_FUNCTIONS = {
    "function_definition", "function_item", "function_declaration", "method_declaration",
    "constructor_declaration", "destructor_declaration", "operator_declaration", "conversion_operator_declaration",
    "local_function_statement", "accessor_declaration", "lambda", "lambda_expression",
    "closure_expression", "func_literal", "anonymous_method_expression", "compact_constructor_declaration",
}
# These nodes can contain arbitrary code not represented as function bodies.
_OMIT = {"macro_invocation", "macro_definition", "preproc_def", "preproc_function_def", "global_statement", "static_initializer", "test_declaration", "TestDecl", "ComptimeDecl"}


class CodeStructureSupport(ContextStructureParserAlgorithm):
    language = ""

    def project_range(self, path, source_text, start_byte, end_byte):
        from .ExactProjection import project
        return project(self, path, source_text, start_byte, end_byte)

    def parse(self, path: str, source_text: str) -> list[ContextFragment]:
        try:
            from tree_sitter_language_pack import get_parser
            source = source_text.encode("utf-8")
            root = get_parser(self.language).parse(source).root_node
        except Exception as exc:
            raise ContextParseError(f"Cannot parse {path} as {self.language}: {exc}") from exc
        if root.has_error:
            raise ContextParseError(f"Invalid {self.language} syntax in {path}")

        fragments = []
        pending = []
        for node in root.named_children:
            if self.language == "rust" and node.type == "attribute_item":
                pending.append(node)
                continue
            if node.type not in _TOP_LEVEL[self.language] or not self._allowed(node):
                pending.clear()
                continue
            start = pending[0].start_byte if pending else node.start_byte
            edits = []
            for attr in pending:
                self._collect(attr, source, edits)
            pending.clear()
            self._collect(node, source, edits)
            result = self._render(source, start, node.end_byte, edits).strip()
            if not result:
                continue
            name = node.child_by_field_name("name")
            selector = source[name.start_byte:name.end_byte].decode("utf-8") if name else ""
            fragments.append(ContextFragment(path=path, format=self.language, kind=node.type,
                                             code_text=result, selector=selector,
                                             start_byte=start, end_byte=node.end_byte))
        return fragments

    def _allowed(self, node):
        if self.language == "python" and node.type == "expression_statement":
            return bool(node.named_children and node.named_children[0].type in {"assignment", "augmented_assignment"})
        return True

    def _collect(self, node, source, edits):
        if node.type in _OMIT:
            edits.append((node.start_byte, node.end_byte, b""))
            return
        # Python class suites may contain arbitrary executable statements.
        if self.language == "python" and node.type == "block" and node.parent and node.parent.type == "class_definition":
            for child in node.named_children:
                if child.type not in {"decorated_definition", "class_definition", "function_definition", "expression_statement", "type_alias_statement"} or not self._allowed(child):
                    edits.append((child.start_byte, child.end_byte, b""))
                else:
                    self._collect(child, source, edits)
            return
        body = node.child_by_field_name("body") if node.type in _FUNCTIONS else None
        if self.language == "csharp" and node.type == "anonymous_method_expression" and body is None:
            body = next((c for c in node.named_children if c.type == "block"), None)
        if self.language == "cpp" and node.type == "function_definition" and body is None:
            body = next((c for c in node.named_children if c.type == "try_statement"), None)
        # Zig's FnProto and Block are siblings rather than body fields.
        if self.language == "zig" and node.type == "Decl" and any(c.type == "FnProto" for c in node.named_children):
            body = next((c for c in node.named_children if c.type == "Block"), None)
        # Expression-bodied C# properties/indexers do not have a function node.
        if self.language == "csharp" and node.type in {"property_declaration", "indexer_declaration"}:
            body = next((c for c in node.named_children if c.type == "arrow_expression_clause"), None)
        for child in node.named_children:
            if body is not None and child.id == body.id:
                replacement = b"..." if self.language == "python" else b";"
                if node.type in {"lambda", "lambda_expression", "closure_expression", "func_literal", "anonymous_method_expression"}:
                    replacement = b"..." if self.language == "python" else b"{ /* body omitted */ }"
                edits.append((child.start_byte, child.end_byte, replacement))
            else:
                # Comments between a Python function header and its suite belong to the body.
                if self.language == "python" and node.type == "function_definition" and child.type == "comment" and body is not None:
                    edits.append((child.start_byte, child.end_byte, b""))
                # Java instance initializer blocks are direct class-body children.
                elif self.language == "java" and node.type in {"class_body", "enum_body"} and child.type == "block":
                    edits.append((child.start_byte, child.end_byte, b""))
                else:
                    self._collect(child, source, edits)

    @staticmethod
    def _render(source, start, end, edits):
        result = bytearray()
        pos = start
        for left, right, replacement in sorted(edits):
            result.extend(source[pos:left])
            result.extend(replacement)
            pos = right
        result.extend(source[pos:end])
        return result.decode("utf-8")
