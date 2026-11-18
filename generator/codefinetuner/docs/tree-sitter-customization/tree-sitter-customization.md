# Tree-sitter Customization

## Add New Language Block Definitions
1. To find the relevant node names for the language you want to add, run the helper script [`print_treesitter_tree.py`](../../scripts/print_treesitter_tree.py) on a sample source file to output its syntax tree and node types. Alternatively, use the web-based [Syntax Tree Playground](https://tree-sitter.github.io/tree-sitter/7-playground.html) to inspect the node structure interactively.
2. Create a new file under `codefinetuner/config/`, e.g., `codefinetuner/config/tree_sitter_definitions.json`.
Add a new entry for your language, defining the following nodes:
    * `block_types`: The outer syntax nodes (e.g., functions, classes). These are the structural elements from which we create FIM examples.
    * `subblock_types`: The inner syntax nodes (e.g., statements, expressions). These are masked to form the predicted middle portion of the FIM example.

    Example entry for the C programming language:
      ```json
      "c": {
        "block_types": [
          "function_definition",
          "struct_specifier",
          "union_specifier",
          "enum_specifier"
        ],
        "subblock_types": [
          "compound_statement",
          "parameter_list",
          "declaration",
          "expression_statement",
          "if_statement",
          "while_statement",
          "for_statement",
          "switch_statement",
          "case_statement",
          "return_statement",
          "field_declaration_list",
          "field_declaration",
          "enumerator_list",
          "enumerator"
        ]
      }
    ```
3. Set the parameter in your config file `codefinetuner_config.yaml` to the path of that file:
    ```yaml
    tree_sitter_definitions_path: "codefinetuner/config/tree_sitter_definitions.json"
    ```

## Build Custom Parser
If the language you want to preprocess is not present in the [tree-sitter-language-pack](https://github.com/Goldziher/tree-sitter-language-pack), you can build a tree-sitter language parser from source.

Parsers must be compiled for ABI **13 or 14**: CodeFinetuner pins `tree-sitter>=0.22,<0.23` (for compatibility with the installed tree-sitter bindings), and that version only loads parsers in that ABI range. `tree-sitter-cli generate` defaults to the newer ABI 15, so the `--abi 14` flag below is part of the standard build. The grammar rules themselves are unaffected by ABI version, only the compiled binary layout changes.

### 1. Clone the grammar

```bash
cd /path/to/codefinetuner/third_party
git clone --depth 1 https://github.com/<owner>/tree-sitter-<lang>.git
# or, if codefinetuner is itself a git repo and you want it tracked as a submodule:
# git submodule add https://github.com/<owner>/tree-sitter-<lang>.git third_party/tree-sitter-<lang>
cd tree-sitter-<lang>
```

### 2. Check the checked-in ABI version

Some grammar repos already ship a `parser.c` compatible with our range — check before regenerating:

```bash
grep -m1 'LANGUAGE_VERSION' src/parser.c
```

- Prints `13` or `14` → skip to [step 4](#4-compile-os-specific).
- Prints `15` → continue to step 3.

### 3. Generate at ABI 14

```bash
rm -f src/parser.c
npx --yes tree-sitter-cli generate --abi 14

grep -m1 'LANGUAGE_VERSION' src/parser.c   # should print 14
```

### 4. Compile (OS-specific)

Do not use `make` here — its default rule tries to regenerate `parser.c` via a bare `tree-sitter` binary that usually isn't on `PATH` (only available through `npx`), which fails and aborts before compiling anything. Compile directly instead:

**Linux**
```bash
cc -Isrc -std=c11 -fPIC -c -o src/parser.o src/parser.c
cc -shared -o libtree-sitter-<lang>.so src/parser.o

nm -D --defined-only libtree-sitter-<lang>.so | grep tree_sitter_<lang> \
  || echo "BROKEN BUILD"
```

**macOS**
```bash
cc -Isrc -std=c11 -fPIC -c -o src/parser.o src/parser.c
cc -dynamiclib -o libtree-sitter-<lang>.dylib src/parser.o

nm -gU libtree-sitter-<lang>.dylib | grep tree_sitter_<lang> \
  || echo "BROKEN BUILD"
```
macOS prefixes C symbols with an underscore, so the expected match is `_tree_sitter_<lang>` — that leading `_` is normal, not an error. **Do not use `cc -shared` on macOS** — it produces a file `nm` reports as having no dynamic symbol table, not a real loadable library.

You should end up with a shared library file named `libtree-sitter-<lang>.so` (Linux) or `libtree-sitter-<lang>.dylib` (macOS) in the root of the grammar's directory.

### 5. Update Config File
```yaml
tree_sitter_parser_path: "path/to/shared_library_file"  # e.g., "./third_party/tree-sitter-<lang>/libtree-sitter-<lang>.dylib"
tree_sitter_definitions_path: "codefinetuner/config/tree_sitter_definitions.json"
```

A `.dylib` built on macOS only runs on macOS; a `.so` built on Linux only runs on Linux. Build the library on the machine that will actually run the pipeline.