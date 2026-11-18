import json
from pathlib import Path

import pytest

from codefinetuner.nep.context import ContextParseError, ContextStructureParserResolution
from codefinetuner.nep.InputPromptBuilder import InputPromptBuilder
from codefinetuner.nep.engine import Scenario, Step
from codefinetuner.nep.protocol import render, parse_input, apply_completion
from codefinetuner.nep.__main__ import main, read_repository


@pytest.mark.parametrize("path,strategy", [
    ("a.RS", "Rust"), ("a.py", "Python"), ("a.pyi", "Python"),
    ("a.ts", "Typescript"), ("a.d.ts", "Typescript"), ("a.tsx", "TSX"),
    ("a.js", "Javascript"), ("a.jsx", "JSX"), ("a.cs", "Csharp"),
    ("a.zig", "Zig"), ("a.html", "HTML"), ("a.svg", "XML"),
    ("a.xml", "XML"), ("a.md", "Markdown"), ("a.json", "JSON"),
    ("a.jsonc", "JSONC"), ("a.json5", "JSON5"), ("a.jsonl", "JSONL"),
    ("a.toml", "TOML"), ("a.cpp", "Cpp"), ("a.java", "Java"), ("a.go", "Go"),
])
def test_resolution(path, strategy):
    parser = ContextStructureParserResolution.resolve(path)
    assert type(parser.algorithm).__name__ == strategy + "ContextStructureParser"


def test_unknown_and_invalid_context_never_falls_back_to_source():
    with pytest.raises(ContextParseError):
        ContextStructureParserResolution.resolve("a.unknown")
    with pytest.raises(ContextParseError):
        ContextStructureParserResolution.resolve("a.rs").parse("a.rs", "fn broken( {")


def test_wrapper_selects_without_truncation_or_nested_duplicates():
    parser = ContextStructureParserResolution.resolve("settings.json")
    source = '{"scripts":{"build":"tsc"},"secret":"not relevant"}'
    fragments = parser.parse("settings.json", source, selectors=("/scripts",))
    assert len(fragments) == 1
    assert "build" in fragments[0].code_text
    assert "secret" not in fragments[0].code_text
    with pytest.raises(ContextParseError):
        parser.parse("settings.json", source, selectors=("/scripts",), max_chars=1)
    assert parser.parse("settings.json", source, query_terms=("does_not_exist",)) == []


def sample():
    library_before = "def calculate_old(value):\n    return value + 100\n"
    library_after = "def calculate(value):\n    return value + 100\n"
    current = "def helper(value):\n    return value * 2\n\n# gap\n# gap\nresult = calculate_old(helper(2))\n"
    return Scenario({"lib.py": library_before, "app.py": current},
                    [Step("lib.py", library_before, library_after)], "app.py",
                    current.replace("calculate_old", "calculate"), "replace", "python", "example")


def test_context_prepared_before_builder_and_exact_target_unchanged():
    item = sample()
    rendered = render(item, context_paths=("lib.py",), include_blocks=True)
    blocks = rendered["input_blocks"]
    assert blocks["retrieval_context"]
    assert blocks["current_file_context"]
    retrieval = "\n".join(f["code_text"] for f in blocks["retrieval_context"])
    current = "\n".join(f["code_text"] for f in blocks["current_file_context"])
    assert "def calculate(value)" in retrieval and "return value + 100" not in retrieval
    assert "def helper(value)" in current and "return value * 2" not in current
    assert "calculate_old(helper(2))" in blocks["edit_target"]["code_text"]
    assert blocks["edit_target"]["code_text"] == item.current["app.py"]
    assert InputPromptBuilder().build(blocks) == rendered["input"] == blocks["input_result"]
    assert apply_completion(rendered["input"], rendered["output"]) == item.target_after
    # Builder consumes already prepared data and does not invoke the parser.
    blocks["retrieval_context"][0]["code_text"] = "def calculate(value): ..."
    assert "def calculate(value): ..." in InputPromptBuilder().build(blocks)


def test_current_incomplete_input_is_reported_without_future_context():
    item = Scenario({"a.rs": "fn main() { let x = call; }"},
                    [Step("a.rs", "fn main() { let x = call; }", "fn main() { let x = call(; }")],
                    "a.rs", "fn main() { let x = call(123); }", "insert", "rust", "f")
    row = render(item, include_blocks=True)
    assert row["input_blocks"]["current_file_context"] == []
    assert row["context_diagnostics"][0]["reason"] == "partial_syntax_tree"
    assert "123" not in row["input"]


def empty_range_blocks():
    text = "await repository;"
    offset = len("await repository".encode())
    return {"schema_version": "nep-input-v1", "retrieval_context": [], "current_file_context": [],
            "edit_history": [{"id": 1, "path": "a.ts", "before": "await;", "after": text}],
            "edit_target": {"path": "a.ts", "code_text": text, "position_encoding": "utf8_bytes",
                            "cursor": {"offset": offset},
                            "markers": [{"id": 1, "offset": offset}, {"id": 2, "offset": offset}]}}


def test_builder_empty_insertion_and_suffix():
    blocks = empty_range_blocks()
    prompt = InputPromptBuilder().build(blocks)
    assert "<|marker_1|><|user_cursor|><|marker_2|>" in prompt
    assert prompt.startswith("<|fim_suffix|>;")
    output = "<|marker_1|>.getUser<|user_cursor|><|marker_2|><|endoftext|>"
    assert apply_completion(prompt, output) == "await repository.getUser;"


def test_builder_unicode_byte_offsets_and_rejection():
    blocks = empty_range_blocks()
    target = blocks["edit_target"]
    target["code_text"] = "🌍x"
    target["markers"] = [{"id": 1, "offset": 4}, {"id": 2, "offset": 5}]
    target["cursor"]["offset"] = 4
    assert parse_input(InputPromptBuilder().build(blocks)).source == "🌍x"
    target["cursor"]["offset"] = 1
    with pytest.raises(ValueError, match="UTF-8"):
        InputPromptBuilder().build(blocks)


@pytest.mark.parametrize("mutation", [
    lambda b: b["edit_target"].update(position_encoding="characters"),
    lambda b: b["edit_target"]["markers"][1].update(id=3),
    lambda b: b["edit_target"]["cursor"].update(offset=999),
    lambda b: b.update(retrieval_context=[{"path": "a.ts", "code_text": "wrong group"}]),
    lambda b: b.update(current_file_context=[{"path": "other.ts", "code_text": "wrong group"}]),
    lambda b: b.update(retrieval_context=[{"path": "b.ts", "code_text": "<|fim_prefix|>"}]),
    lambda b: b["edit_history"][0].update(after="await;"),
])
def test_builder_rejects_invalid_blocks(mutation):
    blocks = empty_range_blocks()
    mutation(blocks)
    with pytest.raises(ValueError):
        InputPromptBuilder().build(blocks)


def test_reader_admits_context_formats_and_ignores_rust_build_output(tmp_path):
    (tmp_path / "a.rs").write_text("fn main() {}")
    (tmp_path / "Cargo.toml").write_text('[package]\nname="demo"\n')
    (tmp_path / "README.md").write_text("# Demo\nDescription\n")
    (tmp_path / "target").mkdir()
    (tmp_path / "target/generated.rs").write_text("fn generated() {}")
    files, _ = read_repository(tmp_path, max_file_bytes=1000, max_files=10)
    assert set(files) == {"a.rs", "Cargo.toml", "README.md"}


def test_cli_persists_building_blocks_and_respects_custom_budget(tmp_path, capsys):
    source = tmp_path / "source"
    source.mkdir()
    (source / "provider.py").write_text("def calculate(x):\n    return x + 123\n")
    (source / "consumer.py").write_text("import provider\nx = provider.calculate(1)\n")
    output = tmp_path / "out"
    main([str(source), str(output), "--repository-id", "demo", "--split", "test", "--max-context-chars", "100"])
    rows = [json.loads(line) for line in (output / "test.jsonl").read_text().splitlines()]
    audit = [json.loads(line) for line in (output / "audit.jsonl").read_text().splitlines()]
    assert rows
    for row, record in zip(rows, audit):
        assert InputPromptBuilder().build(record["input_blocks"]) == row["input"]
        assert record["max_context_chars"] == 100
        context = record["input_blocks"]["retrieval_context"] + record["input_blocks"]["current_file_context"]
        assert sum(len(f["code_text"]) for f in context) <= 100
