import json
import random
from pathlib import Path

import pytest

from codefinetuner.nep.engine import Scenario, Step, generate_scenarios
from codefinetuner.nep.protocol import (
    CURSOR, EOS, apply_completion, history_diff, parse_input, render,
)


def scenario(before, after, kind="replace"):
    return Scenario({"a.py": "# earlier\n" + before},
                    [Step("a.py", "# earlier\n" + before, before)],
                    "a.py", after, kind, "python", "test")


@pytest.mark.parametrize("before,after,kind", [
    ("a = 1\nb = 2\nc = 3\n", "a = 1\nb = 20\nc = 3\n", "insert"),
    ("a = 1\nb = 2\nc = 3\n", "a = 1\nc = 3\n", "delete"),
    ("", "hello\n", "insert"),
    ("x = '🌍'\n", "x = 'привет'\n", "replace"),
    ("x", "", "delete"),
    ("x = 1\r\ny = 2\r\n", "x = 2\r\ny = 3\r\n", "replace"),
    ("x = 1", "x = 12", "insert"),
    ("x = 1\n", "x = 1\n", "noop"),
    ("", "", "noop"),
    ("abc\nkeep\ndef\n", "ABC\nkeep\nDEF\n", "replace"),
])
def test_exact_roundtrip(before, after, kind):
    row = render(scenario(before, after, kind))
    assert parse_input(row["input"]).source == before
    assert apply_completion(row["input"], row["output"]) == after
    assert row["input"].count(CURSOR) == row["output"].count(CURSOR) == 1


def test_cursor_does_not_restrict_edit():
    before, after = "x = 1\ny = 2\nz = 3\n", "x = 1\ny = 4\nz = 3\n"
    rows = [render(scenario(before, after), cursor_mode=mode) for mode in ("above", "at", "below")]
    cursors = [parse_input(r["input"]).cursor for r in rows]
    assert cursors[0] < cursors[1] < cursors[2]
    assert len({r["output"] for r in rows}) == 1
    assert all(apply_completion(r["input"], r["output"]) == after for r in rows)


def test_actual_generated_scenarios_and_current_cross_file_context():
    files = {"provider.py": "def calculate(x):\n    return x + 123\n",
             "consumer.py": "import provider\na = provider.calculate(1)\nb = provider.calculate(2)\n",
             "local.py": "def _f(value):\n    return value + value\n",
             "delete.py": "def _obsolete():\n    pass\n_obsolete()\n"}
    kinds, cross_file = set(), False
    for item in generate_scenarios(files, max_scenarios_per_file=100):
        contexts = tuple({s.path for s in item.history} - {item.target_path})
        row = render(item, context_paths=contexts)
        kinds.add(item.kind)
        assert parse_input(row["input"]).source == item.current[item.target_path]
        assert apply_completion(row["input"], row["output"]) == item.target_after
        if contexts:
            cross_file = True
            context = row["input"].split("<|file_sep|>edit_history")[0]
            assert "<|file_sep|>provider.py\ndef calculate(x):" in context
            assert "return x + 123" not in context
            if item.kind != "noop":
                assert "calculate_nep" in item.current[item.target_path]
    assert kinds == {"insert", "replace", "delete", "noop"}
    assert cross_file


def test_unchanged_context_and_preserved_gap():
    before = "first = 1\nkeep = 2\nlast = 3\n"
    after = "first = 4\nkeep = 2\nlast = 5\n"
    row = render(scenario(before, after))
    assert "keep = 2\n" in row["output"]


@pytest.mark.parametrize("completion", [
    "<|marker_1|>text<|marker_2|><|endoftext|>",
    "<|marker_1|><|user_cursor|><|marker_99|><|endoftext|>",
    "<|marker_2|><|user_cursor|><|marker_1|><|endoftext|>",
    "<|marker_1|>text<|user_cursor|><|marker_1|><|endoftext|>",
    "<|marker_1|><|fim_prefix|><|user_cursor|><|marker_2|><|endoftext|>",
    "<|marker_1|><|user_cursor|><|marker_2|><|endoftext|>\n",
    "explanation<|marker_1|><|user_cursor|><|marker_2|><|endoftext|>",
])
def test_reject_malformed_completion(completion):
    row = render(scenario("x = 1\n", "x = 2\n"))
    with pytest.raises(ValueError):
        apply_completion(row["input"], completion)


@pytest.mark.parametrize("mutate", [
    lambda p: p + "\n",
    lambda p: p.replace("<|marker_1|>", "<|marker_2|>"),
    lambda p: p.replace(CURSOR, ""),
    lambda p: p.replace("edit_history", "other.py"),
    lambda p: p.replace("<|fim_suffix|>", "<|fim_prefix|>"),
    lambda p: p.replace("<|fim_middle|>", EOS + "<|fim_middle|>"),
])
def test_reject_malformed_input(mutate):
    row = render(scenario("x = 1\n", "x = 2\n"))
    with pytest.raises(ValueError):
        parse_input(mutate(row["input"]))


def test_reserved_source_and_history_without_newline():
    with pytest.raises(ValueError):
        render(scenario("x = '<|fim_suffix|>'", "x = 1"))
    assert "\\ No newline at end of file\n" in history_diff(scenario("abc", "abd"))


def test_documented_jsonl_examples_parse():
    examples = Path(__file__).resolve().parents[4] / "docs/nep-data-examples/NEP_FILTERED_EXAMPLES_10.jsonl"
    for line in examples.read_text().splitlines():
        row = json.loads(line)
        parse_input(row["input"])
        apply_completion(row["input"], row["output"])


def test_randomized_serializer_roundtrips():
    rng = random.Random(42)
    for _ in range(1000):
        before = "".join(rng.choice("ab \n🌍") for _ in range(rng.randrange(30)))
        start = rng.randrange(len(before) + 1)
        end = rng.randrange(start, len(before) + 1)
        replacement = "".join(rng.choice("xy\nж") for _ in range(rng.randrange(8)))
        after = before[:start] + replacement + before[end:]
        item = scenario(before, after, "noop" if before == after else "replace")
        for mode in ("at", "above", "below"):
            try:
                row = render(item, cursor_mode=mode)
            except ValueError as exc:
                if str(exc).startswith("No line "):
                    continue
                raise
            assert apply_completion(row["input"], row["output"]) == after


def test_debug_fstring_and_string_export_rejected():
    for source, kind in [("def _f(x):\n    return f'{x=} {x=}'\n", "replace"),
                         ("def _obsolete():\n    pass\n_obsolete()\n__all__ = ['_obsolete']\n", "delete")]:
        assert not any(s.kind == kind for s in generate_scenarios({"a.py": source}))
