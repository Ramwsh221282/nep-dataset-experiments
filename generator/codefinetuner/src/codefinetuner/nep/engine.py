"""Deterministic causal next-edit examples from a repository snapshot.

Tree-sitter is a concrete syntax parser, not a general name resolver. Python
semantic examples use restricted AST checks. Cross-file binding is resolved only
for an unambiguous root-level module and a direct, unaliased module import.
Offsets into source are UTF-8 bytes, as in both tree-sitter and Python's AST.
"""

from __future__ import annotations

import ast
import hashlib
import io
import keyword
import sys
import tokenize
from dataclasses import dataclass
from typing import Iterator

from .languages import language_for_path


@dataclass
class Step:
    path: str
    before: str
    after: str


@dataclass
class Scenario:
    initial: dict[str, str]
    history: list[Step]
    target_path: str
    target_after: str
    kind: str
    language: str
    family_id: str

    @property
    def current(self) -> dict[str, str]:
        return replay(self.initial, self.history)


def replay(initial: dict[str, str], history: list[Step]) -> dict[str, str]:
    """Replay full-file steps, rejecting stale or non-editing history."""
    state = initial.copy()
    for step in history:
        if step.path not in state or state[step.path] != step.before:
            raise ValueError(f"History does not match current file: {step.path}")
        if step.before == step.after:
            raise ValueError(f"History step is not an edit: {step.path}")
        state[step.path] = step.after
    return state


def _family(path: str, kind: str, start: int, end: int, source: bytes) -> str:
    digest = hashlib.sha256(source).hexdigest()[:16]
    return f"{path}:{kind}:{start}:{end}:{digest}"


def _scenario(files: dict[str, str], path: str, language: str, kind: str,
              family: str, versions: list[str], target: str) -> Scenario:
    initial = files.copy()
    initial[path] = versions[0]
    history = [Step(path, a, b) for a, b in zip(versions, versions[1:])]
    return Scenario(initial, history, path, target, kind, language, family)


def _nodes(root):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.named_children))


# Select actual expression nodes, not declarations, identifiers or statements.
_EXPRESSIONS = {
    "binary_expression", "binary_operator", "boolean_operator", "comparison_operator",
    "call", "call_expression", "method_invocation", "invocation_expression",
    "parenthesized_expression", "list", "dictionary", "tuple", "array", "array_expression",
    "object", "object_creation_expression", "struct_expression", "composite_literal",
}


def _insertions(files, path, language, source, root):
    for node in _nodes(root):
        if node.type not in _EXPRESSIONS:
            continue
        start, end = node.start_byte, node.end_byte
        expression = source[start:end]
        # Bound work and avoid splitting UTF-8 code points or source lines.
        if not 4 <= len(expression) <= 512 or b"\n" in expression:
            continue
        boundaries = [i for i in range(1, len(expression))
                      if expression[i] < 128 and expression[i - 1] < 128]
        if len(boundaries) < 2:
            continue
        # Multiple prefixes from the same trajectory share the family ID.
        cuts = sorted(set(boundaries[i * (len(boundaries) - 1) // 3] for i in (1, 2, 3)))
        versions = [(source[:start] + expression[:cut] + source[end:]).decode("utf-8")
                    for cut in cuts]
        full = source.decode("utf-8")
        family = _family(path, "insert", start, end, source)
        for i in range(1, len(versions)):
            yield _scenario(files, path, language, "insert", family, versions[:i + 1], full)
        if len(versions) >= 2:
            yield _scenario(files, path, language, "noop", family, versions + [full], full)


def _offsets(source: bytes):
    starts = [0]
    for i, byte in enumerate(source):
        if byte == 10:
            starts.append(i + 1)
    return starts


def _span(node, starts):
    return starts[node.lineno - 1] + node.col_offset, starts[node.end_lineno - 1] + node.end_col_offset


def _replace(source: bytes, edits: list[tuple[int, int, bytes]]) -> str:
    for start, end, value in sorted(edits, reverse=True):
        source = source[:start] + value + source[end:]
    return source.decode("utf-8")


def _python_renames(files, path, source, tree):
    starts = _offsets(source)
    # Only top-level synchronous functions without decorators or annotations.
    # Nested scopes, rebinding, globals, comprehensions and reflection are
    # rejected, not guessed at. Every occurrence is an ordinary Name load.
    forbidden = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
                 ast.Global, ast.Nonlocal, ast.Import, ast.ImportFrom,
                 ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
                 ast.NamedExpr, ast.Try, ast.TryStar, ast.Match, ast.Delete, ast.JoinedStr)
    for fn in tree.body:
        if (not isinstance(fn, ast.FunctionDef) or fn.decorator_list or fn.returns
                or fn.type_comment or getattr(fn, "type_params", ())):
            continue
        if any(isinstance(n, forbidden) for stmt in fn.body for n in ast.walk(stmt)):
            continue
        if any(isinstance(n, (ast.Call, ast.Attribute)) for stmt in fn.body for n in ast.walk(stmt)):
            continue
        if any(isinstance(n, ast.Attribute) and n.attr == fn.name for n in ast.walk(tree)):
            continue
        if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id in {"globals", "locals", "eval", "exec", "getattr", "__import__"}
               for n in ast.walk(tree)):
            continue
        if any(fn.name in text for other, text in files.items() if other != path):
            continue
        if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and fn.name in n.value
               for n in ast.walk(tree)):
            continue
        args = fn.args
        if args.vararg or args.kwarg or args.kwonlyargs or args.defaults or args.posonlyargs:
            continue
        if any(arg.annotation or arg.type_comment for arg in args.args):
            continue
        all_names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        all_names.update(n.arg for n in ast.walk(tree) if isinstance(n, ast.arg))
        for arg in args.args:
            name = arg.arg
            if name == fn.name:
                continue
            occurrences = [n for stmt in fn.body for n in ast.walk(stmt)
                           if isinstance(n, ast.Name) and n.id == name]
            if not 2 <= len(occurrences) <= 30 or any(not isinstance(n.ctx, ast.Load) for n in occurrences):
                continue
            new = name + "_nep"
            if new in all_names or new == fn.name:
                continue
            # Parameter renames can break keyword calls and external callers.
            # Limit to private functions with no use outside their definition.
            if not fn.name.startswith("_") or fn.name.startswith("__"):
                continue
            if any(isinstance(n, ast.Name) and n.id == fn.name for n in ast.walk(tree)):
                continue
            occurrences.sort(key=lambda n: _span(n, starts))
            argspan = _span(arg, starts)
            spans = [argspan] + [_span(n, starts) for n in occurrences]
            replacement = new.encode("utf-8")
            # Snapshot contains the completed rename; reconstruct its prior state.
            # Start with all old names except definition, then propagate one use.
            initial = _replace(source, [(a, b, replacement) for a, b in spans])
            versions = [initial]
            for count in range(1, len(spans) + 1):
                versions.append(_replace(source, [(a, b, replacement) for a, b in spans[count:]]))
            family = _family(path, "replace", *argspan, source)
            for i in range(1, len(versions) - 1):
                yield _scenario(files, path, "python", "replace", family, versions[:i + 1], versions[i + 1])
            yield _scenario(files, path, "python", "noop", family, versions, versions[-1])


def _tokens(source: str):
    try:
        return list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):
        return []


def _cross_file_renames(files, consumer_path, consumer_tree):
    """Resolve only root-level ``import module`` and direct module.function calls.

    The provider must consist of one undecorated function with no calls, imports,
    attributes or nested scopes. The consumer has one direct import and only
    top-level expression/assignment statements; all calls are to that function.
    Token checks reject hidden/bare references and strings mentioning either name.
    Other snapshot files must not mention the module or function at all. This
    intentionally rejects most real programs rather than guessing about binding.
    """
    if "/" in consumer_path or not consumer_path.endswith(".py"):
        return
    imports = [node for node in ast.walk(consumer_tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    if len(imports) != 1 or imports[0] not in consumer_tree.body:
        return
    imp = imports[0]
    if (not isinstance(imp, ast.Import) or consumer_tree.body[0] is not imp
            or len(imp.names) != 1 or imp.names[0].asname):
        return
    module = imp.names[0].name
    if (not module.isidentifier() or keyword.iskeyword(module)
            or module in sys.stdlib_module_names):
        return
    provider_path = module + ".py"
    if provider_path == consumer_path or provider_path not in files:
        return
    if any(path.startswith(module + "/") for path in files):
        return
    if any(not isinstance(node, (ast.Import, ast.Expr, ast.Assign, ast.AnnAssign))
           for node in consumer_tree.body):
        return
    consumer_nodes = list(ast.walk(consumer_tree))
    if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
                             ast.GeneratorExp, ast.NamedExpr, ast.Global, ast.Nonlocal))
           for node in consumer_nodes):
        return
    provider_text = files[provider_path]
    if len(provider_text.encode("utf-8")) > 1_000_000:
        return
    try:
        provider_tree = ast.parse(provider_text)
    except (SyntaxError, ValueError):
        return
    from tree_sitter_language_pack import get_parser
    if get_parser("python").parse(provider_text.encode("utf-8")).root_node.has_error:
        return
    if len(provider_tree.body) != 1 or not isinstance(provider_tree.body[0], ast.FunctionDef):
        return
    fn = provider_tree.body[0]
    if fn.decorator_list or getattr(fn, "type_params", ()):
        return
    name, old = fn.name, fn.name + "_nep"
    if name == module:
        return
    provider_nodes = list(ast.walk(provider_tree))
    if any(isinstance(node, (ast.Call, ast.Attribute, ast.Import, ast.ImportFrom,
                             ast.ClassDef, ast.Lambda, ast.Global, ast.Nonlocal))
           or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fn)
           for node in provider_nodes):
        return
    if any(module in text or name in text or old in text
           for path, text in files.items() if path not in (provider_path, consumer_path)):
        return
    provider_tokens, consumer_tokens = _tokens(provider_text), _tokens(files[consumer_path])
    if not provider_tokens or not consumer_tokens:
        return
    # Tokens catch imports, bindings, bare uses and f-strings that AST name-only
    # checks would miss. Comments are ignored; suspicious strings are rejected.
    for tokens in (provider_tokens, consumer_tokens):
        if any(t.type == tokenize.STRING and (name in t.string or module in t.string)
               for t in tokens):
            return
        if any(t.type == tokenize.NAME and t.string == old for t in tokens):
            return
    if sum(t.type == tokenize.NAME and t.string == name for t in provider_tokens) != 1:
        return
    uses = [node for node in consumer_nodes if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name) and node.value.id == module
            and isinstance(node.value.ctx, ast.Load) and isinstance(node.ctx, ast.Load)
            and node.attr == name]
    calls = [node for node in consumer_nodes if isinstance(node, ast.Call)]
    if not uses or len(uses) > 30 or any(not any(call.func is use for call in calls) for use in uses):
        return
    if any(not any(call.func is use for use in uses) for call in calls):
        return
    if sum(isinstance(node, ast.Attribute) for node in consumer_nodes) != len(uses):
        return
    module_names = [node for node in consumer_nodes if isinstance(node, ast.Name) and node.id == module]
    if len(module_names) != len(uses) or any(not any(use.value is node for use in uses) for node in module_names):
        return
    if sum(t.type == tokenize.NAME and t.string == module for t in consumer_tokens) != len(uses) + 1:
        return
    if sum(t.type == tokenize.NAME and t.string == name for t in consumer_tokens) != len(uses):
        return
    provider = provider_text.encode("utf-8")
    consumer = files[consumer_path].encode("utf-8")
    fn_start, _ = _span(fn, _offsets(provider))
    # AST normalizes some Unicode identifiers. Only edit exact source spelling.
    header = provider[fn_start:provider.find(b"(", fn_start)]
    identifier = name.encode("utf-8")
    pos = header.rfind(identifier)
    if pos < 0 or header[pos + len(identifier):].strip():
        return
    def_span = (fn_start + pos, fn_start + pos + len(identifier))
    starts = _offsets(consumer)
    spans = []
    for use in uses:
        _, end = _span(use, starts)
        start = end - len(identifier)
        if consumer[start:end] != identifier:
            return
        spans.append((start, end))
    spans.sort()
    initial = files.copy()
    initial[provider_path] = _replace(provider, [(*def_span, old.encode("utf-8"))])
    initial[consumer_path] = _replace(consumer, [(a, b, old.encode("utf-8")) for a, b in spans])
    history = [Step(provider_path, initial[provider_path], provider_text)]
    family = _family(provider_path, "cross-file-replace", *def_span, provider + b"\0" + consumer)
    current = initial[consumer_path]
    for index in range(len(spans)):
        target = _replace(consumer, [(a, b, old.encode("utf-8")) for a, b in spans[index + 1:]])
        yield Scenario(initial.copy(), history.copy(), consumer_path, target, "replace", "python", family)
        history.append(Step(consumer_path, current, target))
        current = target
    yield Scenario(initial.copy(), history, consumer_path, current, "noop", "python", family)


def _python_deletions(files, path, source, tree):
    starts = _offsets(source)
    # Only a sole top-level definition with exactly one reference, a top-level
    # standalone call. No imports, decorators, multiline/semicolon statements,
    # or dynamic lookup: these can hide dependencies or change removal safety.
    if any(isinstance(n, (ast.Import, ast.ImportFrom, ast.Attribute, ast.Global,
                          ast.Nonlocal)) for n in ast.walk(tree)):
        return
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
           and n.func.id in {"globals", "locals", "eval", "exec", "getattr", "__import__"}
           for n in ast.walk(tree)):
        return
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.decorator_list:
            continue
        args = fn.args
        if (fn.returns or args.defaults or args.kw_defaults or args.posonlyargs or args.args
                or args.kwonlyargs or args.vararg or args.kwarg or fn.type_comment
                or getattr(fn, "type_params", ())):
            continue
        if not fn.name.startswith("_") or fn.name.startswith("__"):
            continue
        if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and fn.name in n.value
               for n in ast.walk(tree)):
            continue
        if any(fn.name in text for other, text in files.items() if other != path):
            continue
        if sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == fn.name
               for n in ast.walk(tree)) != 1:
            continue
        names = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == fn.name]
        if len(names) != 1 or not isinstance(names[0].ctx, ast.Load):
            continue
        calls = [stmt for stmt in tree.body if isinstance(stmt, ast.Expr)
                 and isinstance(stmt.value, ast.Call) and stmt.value.func is names[0]]
        if len(calls) != 1:
            continue
        call = calls[0]
        # Arguments may have side effects even if the function no longer exists.
        if call.value.args or call.value.keywords or call.lineno != call.end_lineno:
            continue
        def line_span(node):
            a, b = _span(node, starts)
            line_start = starts[node.lineno - 1]
            line_end = source.find(b"\n", b)
            line_end = len(source) if line_end == -1 else line_end + 1
            if source[line_start:a].strip() or source[b:line_end].strip():
                return None
            return line_start, line_end
        def_span, call_span = line_span(fn), line_span(call)
        if def_span is None or call_span is None:
            continue
        without_def = _replace(source, [(*def_span, b"")])
        without_both = _replace(source, [(*def_span, b""), (*call_span, b"")])
        try:
            ast.parse(without_both)
        except SyntaxError:
            continue
        family = _family(path, "delete", *def_span, source)
        yield _scenario(files, path, "python", "delete", family,
                        [source.decode("utf-8"), without_def], without_both)
        yield _scenario(files, path, "python", "noop", family,
                        [source.decode("utf-8"), without_def, without_both], without_both)


def generate_scenarios(files: dict[str, str], *, max_scenarios_per_file: int = 30,
                       allow_semantic: bool = True) -> Iterator[Scenario]:
    """Generate bounded, deterministic scenarios; unsupported/invalid files are skipped.

    Input snapshots are not mutated. Every target is a complete parseable file;
    intermediate construction states may be syntactically invalid. A noop is a
    completed trajectory whose target is identical to the current file.
    """
    if max_scenarios_per_file <= 0:
        return
    from tree_sitter_language_pack import get_parser

    for path in sorted(files):
        language = language_for_path(path)
        if language is None:
            continue
        source = files[path].encode("utf-8")
        if len(source) > 1_000_000:
            continue
        parser = get_parser(language)
        root = parser.parse(source).root_node
        if root.has_error:
            continue
        candidates = []
        if language == "python" and allow_semantic:
            try:
                tree = ast.parse(files[path])
            except (SyntaxError, ValueError):
                continue
            candidates.extend((_cross_file_renames(files, path, tree),
                               _python_renames(files, path, source, tree),
                               _python_deletions(files, path, source, tree)))
        candidates.append(_insertions(files, path, language, source, root))
        count = 0
        for generator in candidates:
            for scenario in generator:
                # Intermediate rename targets must also parse as complete files.
                if parser.parse(scenario.target_after.encode("utf-8")).root_node.has_error:
                    continue
                yield scenario
                count += 1
                if count >= max_scenarios_per_file:
                    break
            if count >= max_scenarios_per_file:
                break
