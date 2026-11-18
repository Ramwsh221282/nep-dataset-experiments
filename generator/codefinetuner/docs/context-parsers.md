# Structured context parsers

The NEP pipeline now prepares `retrieval_context` and `current_file_context`
**before** serializing the final SPM prompt. This implements the building-block
separation described in the workspace's `structure.md`; it does not add model
training, model inference or semantic refactoring.

## Components

Under `src/codefinetuner/nep/context/`:

- `ContextStructureParserAlgorithm.py`: strategy interface and `ContextFragment`.
- `ContextStructureParser.py`: facade; candidate selection, deduplication, budgets.
- `ContextStructureParserResolution.py`: extension-based factory.
- `ContextPreparation.py`: prepare both context groups from replayed current state.
- Each language/format has a separate `<Language>ContextStructureParser.py` file.

`src/codefinetuner/nep/InputPromptBuilder.py` only serializes already prepared
blocks. `protocol.render()` preserves its existing `input/output` API, with
`include_blocks=True` optionally exposing the intermediate representation.

```python
from codefinetuner.nep.context import ContextStructureParserResolution

parser = ContextStructureParserResolution.resolve("src/users.rs")
fragments = parser.parse(
    "src/users.rs",
    "pub fn get_user(id: u64) -> bool { id > 0 }",
    query_terms={"get_user"},
    max_chars=6000,
)
for fragment in fragments:
    print(fragment.code_text)
```

The facade accepts exact `selectors` (e.g. JSON Pointer), `query_terms`, original
UTF-8 `exclude_ranges`, `max_chars`, and `max_fragments`. Query matching is lexical,
not a compiler symbol resolver. Explicit selectors fail if unavailable or outside
budget. Candidates are never truncated mid-declaration; overlapping candidates
are not duplicated. Cache is bounded and keyed by strategy instance, path and
source, not merely path, so different trajectory states do not share stale text.

## Strategies

| Family | Strategies / extensions |
|---|---|
| Code | Rust, Python, C#, Zig, C, C++, Java, Go |
| JavaScript | JavaScript (`.js/.mjs/.cjs`), JSX, TypeScript (`.ts/.mts/.cts`), TSX |
| Documents | Markdown, HTML, XML (including `.svg`) |
| Data/config | JSON, JSONC, JSON5, JSONL/NDJSON, TOML |

Source code strategies use Tree-sitter to retain declarations, attributes,
decorators, signatures, generics, fields and initializer values while removing
recognized function implementations recursively. The output is a structural
projection, not necessarily compilable source. Unrecognized macros and some
executable constructs are intentionally omitted. `.h` resolves to C by default.

JSX/TSX preserve component-level variable declarations, nested function signatures
and JSX markup. Function bodies in handlers, hooks, variable initializers and
attributes are removed; static initializer blocks are omitted. JSX-producing
callbacks may retain markup. `.js` containing JSX is also handled. Component
recognition is syntactic, not React/type-aware, and unusual control flow may be
incompletely represented. Initializer calls are retained as text, never executed.

Markdown sections preserve heading ancestry, fences, front matter and relevant
reference definitions. This is not a complete CommonMark implementation.
HTML candidates preserve element ancestry, attributes and text; candidates
containing script/style elements are omitted rather than returning those bodies.
XML uses Expat, preserves namespaces/source order and rejects DTD/entity declarations;
no external entity or network resolution is performed.

JSON-family fragments include an explicit `JSON Pointer:` annotation in their
prompt text: the key/array path must survive serialization, not remain audit-only.
Their representation is `fragment_with_pointer`, not a standalone JSON document.
JSONL pointers include the record index. Duplicate object keys are rejected.
JSONC supports comments but **rejects trailing commas**; JSON5 uses the separate
`json5==0.12.1` dependency and accepts JSON5 syntax. TOML uses `tomllib` validation
and CST source ranges; inline structures remain part of their containing value.

## Integration and artifacts

CLI:

```sh
uv run codefinetuner-nep SOURCE NEW_OUTPUT \
  --repository-id org/project --split train --max-context-chars 6000
```

- Dataset JSONL remains `id/input/output`.
- `audit.jsonl` additionally contains `input_blocks`, `context_diagnostics` and
  the configured context budget.
- Blocks contain schema version, both processed context groups, chronological
  history, exact `edit_target` with UTF-8 cursor/marker offsets, and `input_result`.
- The builder validates offsets, marker ordering, reserved tokens and history
  continuity; supports empty insertion ranges and repeated target-file headers.
- `InputPromptBuilder().build(record["input_blocks"])` reproduces a stored prompt
  without reparsing source. The generator still verifies exact application of
  each completion to its expected target.

The CLI creates one `RepositorySymbolIndex` for the repository snapshot. A lookup
always goes through the scenario's replayed editor state: unchanged file facts
are shared and changed revisions are indexed on demand (bounded cache). Package
membership is adjusted for moved, removed and added files. Historical before/after
versions are used for change evidence, never as a source of stale projected ranges.
`target_after` is not used to discover symbols. The actual cursor byte offset,
not the entire editable window, seeds the lookup; same-file declarations that
overlap the window are excluded. Partial trees produce `partial_syntax_tree`
diagnostics but need not discard valid declarations elsewhere.
A failed/unsupported **external** context or a context that cannot fit is not
replaced by raw source: the CLI skips that sample with
`retrieval_context_unavailable`. The Python API raises `ContextParseError`.

The context budget counts characters in fragment text, not tokenizer tokens or
headers. The existing `--max-example-chars` additionally limits the final full
input+output. No truncation of history or target code is introduced.

## Boundaries of this implementation

- The runtime uses `nep/scms/<grammar>/{definitions,references,imports}.scm`
  for 11 code grammars (JSX uses JavaScript). Queries capture syntax, not semantic
  identities: the index stores path, scope chain, owner, symbol, kind, type when
  explicit, and UTF-8 byte ranges. There is no language server or build step.
- Cursor lookup considers at most 16 nearby references on the cursor line and
  adjacent lines; history lookup uses the last six completed actions, comparing
  changed declarations and their prior names. Later overlapping edits invalidate
  old anchors. Ambiguous matches are not expanded into global name search.
- Relative TS/JS imports, typed receivers, Python aliases, direct Rust module
  paths, local includes and simple package/namespace bindings are supported.
  Dynamic receivers, re-exports, complex inference, trait dispatch, build flags
  and non-local Go modules are not resolved in general.
- Local Markdown links, headings, HTML/XML IDs and attributes, data `$ref`/JSON
  Pointers and explicit quoted document paths resolve only inside the snapshot.
  Fragment selection uses exact ranges (including for duplicate selectors).
  External URLs are never fetched. Ambiguous IDs, unselective file-only links,
  unsupported or oversized documents can produce no context. Document indexing
  currently caps each source at 32 KB and 512 structural punctuation/newline items.
- Each included block records binding `reason`, `origin`, `symbol`, `scope`,
  `owner` and its current byte span in audit metadata, not in the prompt.
  `index_witness_paths` also records consulted files which ruled out a match: these
  must be present when rerunning retrieval from an audit snapshot.
- Reading those formats into a snapshot does not enable edit-scenario generation
  for them. Existing NEP language/scenario capabilities remain unchanged.
- The exact target **still contains the full current file** in existing scenarios.
  Prefix and suffix are not compressed. Therefore total prompt size can remain
  large even though external contexts now omit implementations.
- The renderer projects specific indexed ranges, including a method's owner
  header without its siblings; unsupported ranges or unreliable headers are
  skipped, never replaced by the complete file. Syntax-based bindings are
  conservative candidates, not compiler-level guarantees.
- Required data protection/licensing/secret scanning is an upstream policy; this
  parser does not execute code or fetch network resources and is not a secret scanner.
- Existing saved examples were not regenerated. New prompts intentionally differ
  from the old whole-external-file format; old audits can still be inspected as-is.

## Tests

```sh
uv run pytest tests/nep/context tests/nep/test_protocol.py tests/nep/test_cli.py
```

Coverage includes language fixtures, JSX/TSX examples from the design, nested body
removal, static blocks, Unicode source spans and offsets, dialect rejection, XML
entity safety, context budgets, current-state extraction, builder round trips,
CLI audit replay and preserved target code. These are structural checks, not
compilation/typechecking or proof that a downstream model uses the context.
