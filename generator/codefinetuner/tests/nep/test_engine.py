import ast

import pytest
from tree_sitter_language_pack import get_parser

from codefinetuner.nep.engine import Step, generate_scenarios, replay
from codefinetuner.nep.languages import CAPABILITIES, language_for_path


@pytest.mark.parametrize(
    ("path", "source", "language"),
    [
        ("a.py", "value = 1 + 234\n", "python"),
        ("a.js", "const value = 1 + 234;\n", "javascript"),
        ("a.jsx", "const value = 1 + 234;\n", "javascript"),
        ("a.ts", "const value: number = 1 + 234;\n", "typescript"),
        ("a.tsx", "const value: number = 1 + 234;\n", "tsx"),
        ("A.java", "class A { int value = 1 + 234; }\n", "java"),
        ("a.c", "int value = 1 + 234;\n", "c"),
        ("a.cpp", "int value = 1 + 234;\n", "cpp"),
        ("a.go", "package main\nvar value = 1 + 234\n", "go"),
        ("a.rs", "fn main() { let value = 1 + 234; }\n", "rust"),
        ("a.cs", "class A { int value = 1 + 234; }\n", "csharp"),
    ],
)
def test_language_fixtures(path, source, language):
    scenarios = list(generate_scenarios({path: source}))
    assert language_for_path(path) == language
    assert any(s.kind == "insert" for s in scenarios)
    for s in scenarios:
        assert s.language == language
        assert not get_parser(language).parse(s.target_after.encode()).root_node.has_error
        assert s.history
        current = replay(s.initial, s.history)
        assert current == s.current
        assert (current[path] == s.target_after) == (s.kind == "noop")
        assert all(step.before != step.after for step in s.history)


def test_multifile_context_is_preserved_and_deterministic():
    files = {"lib.py": "def f():\n    return 10 + 200\n", "use.py": "from lib import f\nx = f() + 123\n"}
    first = list(generate_scenarios(files))
    assert first == list(generate_scenarios(dict(reversed(list(files.items())))))
    assert {s.target_path for s in first} == set(files)
    for s in first:
        assert set(s.current) == set(files)
        assert all(s.current[p] == value for p, value in files.items() if p != s.target_path)
        assert all(step.path == s.target_path for step in s.history)
    assert files["lib.py"].startswith("def f")


def test_rename_is_a_causal_sequence_with_family():
    source = "def _calculate(value):\n    return value + value + value\n"
    scenarios = [s for s in generate_scenarios({"a.py": source}) if s.kind == "replace"]
    assert len(scenarios) == 3
    assert len({s.family_id for s in scenarios}) == 1
    for s in scenarios:
        assert "value_nep" in s.current["a.py"]
        assert s.current["a.py"] != s.target_after
        ast.parse(s.target_after)
    assert scenarios[-1].target_after == source
    assert len(scenarios[-1].history) > len(scenarios[0].history)


@pytest.mark.parametrize("source", [
    "def _f(x):\n    x = 1\n    return x + x\n",
    "def _f(x):\n    def inner(x):\n        return x + x\n    return x + x\n",
    "def _f(x):\n    return [x for x in (x, x)]\n",
    "def f(x):\n    return x + x\n",
    "def _f(x):\n    return x + x\n_f(x=1)\n",
    "def _f(x):\n    return x + x_nep + x\n",
    "def _f(x):\n    del y\n    return x + x\n",
    "def _f(_f):\n    return _f + _f\n",
    "def _f[T](x):\n    return x + x\n",
])
def test_ambiguous_rename_is_rejected(source):
    assert not any(s.kind == "replace" for s in generate_scenarios({"a.py": source}))


def test_delete_only_standalone_no_argument_call():
    source = "def _obsolete():\n    pass\n\n_obsolete()\nvalue = 1\n"
    scenarios = list(generate_scenarios({"a.py": source}))
    deletion = next(s for s in scenarios if s.kind == "delete")
    assert "def _obsolete" not in deletion.current["a.py"]
    assert "_obsolete()" in deletion.current["a.py"]
    assert "_obsolete" not in deletion.target_after
    assert ast.parse(deletion.target_after)
    assert any(s.kind == "noop" and s.family_id == deletion.family_id for s in scenarios)


@pytest.mark.parametrize("call", ["x = _obsolete()", "print(_obsolete())", "_obsolete(side_effect())", "_obsolete(); x = 1"])
def test_delete_rejects_used_result_and_unsafe_statements(call):
    source = f"def _obsolete(*args):\n    return 1\n\n{call}\n"
    assert not any(s.kind == "delete" for s in generate_scenarios({"a.py": source}))


def test_cross_file_mentions_and_signature_side_effects_rejected():
    files = {"lib.py": "def _f():\n    pass\n_f()\n", "other.py": "from lib import _f\n"}
    assert not any(s.kind == "delete" for s in generate_scenarios(files))
    files = {"lib.py": "def _f(x):\n    return x + x\n", "other.py": "from lib import _f\n"}
    assert not any(s.kind == "replace" for s in generate_scenarios(files))
    source = "def _f(x=side_effect()):\n    pass\n_f()\n"
    assert not any(s.kind == "delete" for s in generate_scenarios({"a.py": source}))


def cross_file_scenarios(files):
    return [s for s in generate_scenarios(files)
            if s.kind == "replace" and s.history[0].path != s.target_path]


def test_cross_file_rename_replay_and_prefixes():
    files = {"provider.py": "# привет\ndef вычислить(x):\n    return x + 1\n",
             "consumer.py": "import provider\n# 🌍\na = provider.вычислить(1)\nb = provider.вычислить(2)\n"}
    scenarios = cross_file_scenarios(files)
    assert len(scenarios) == 2
    assert len({s.family_id for s in scenarios}) == 1
    for index, scenario in enumerate(scenarios):
        current = replay(scenario.initial, scenario.history)
        assert scenario.initial["provider.py"].count("вычислить_nep") == 1
        assert scenario.initial["consumer.py"].count("вычислить_nep") == 2
        assert current["provider.py"] == files["provider.py"]
        assert current["consumer.py"].count("вычислить_nep") == 2 - index
        assert scenario.target_after.count("вычислить_nep") == 1 - index
        assert len(scenario.history) == index + 1
        assert scenario.history[0].path == "provider.py"
        ast.parse(scenario.target_after)
    assert scenarios[-1].target_after == files["consumer.py"]
    assert scenarios == cross_file_scenarios(dict(reversed(list(files.items()))))
    complete = [s for s in generate_scenarios(files) if s.kind == "noop" and s.family_id == scenarios[0].family_id]
    assert len(complete) == 1 and complete[0].current == files
    assert len(list(generate_scenarios(files, max_scenarios_per_file=1))) <= len(files)


@pytest.mark.parametrize("consumer", [
    "import provider as p\nx = p.function()\n",
    "from provider import function\nx = function()\n",
    "import provider\nimport provider\nx = provider.function()\n",
    "import provider\nprovider = object()\nx = provider.function()\n",
    "x = provider.function()\nimport provider\n",
    "import provider\nprovider: int\nx = provider.function()\n",
    "import provider\nprovider, x = (1, provider.function())\n",
    "import provider\ndel provider\nx = provider.function()\n",
    "import provider\ndef nested(provider):\n    return provider.function()\n",
    "import provider\ndef nested():\n    provider = 1\n    return provider.function()\n",
    "import provider\nx = provider.function()\ny = function()\n",
    "import provider\nx = provider.function()\ny = getattr(provider, 'function')\n",
    "import provider\nx = provider.function()\ny = provider.other()\n",
    "import provider\nx = provider.function()\ny = provider\n",
    "import provider\nprovider.function = 1\nx = provider.function()\n",
    "import provider\nx = provider.function()\ny = function_nep\n",
])
def test_cross_file_rejects_ambiguous_consumers(consumer):
    assert not cross_file_scenarios({"provider.py": "def function():\n    return 1\n", "consumer.py": consumer})


@pytest.mark.parametrize("provider", [
    "@decorator\ndef function():\n    return 1\n",
    "def function():\n    return 1\ndef function():\n    return 2\n",
    "def function():\n    return 1\nfunction = other\n",
    "def function():\n    return function()\n",
    "def function():\n    return getattr(object, 'x')\n",
    "def function(function):\n    return function\n",
])
def test_cross_file_rejects_ambiguous_provider(provider):
    assert not cross_file_scenarios({"provider.py": provider, "consumer.py": "import provider\nx = provider.function()\n"})


def test_cross_file_rejects_other_mentions_and_module_ambiguity():
    files = {"provider.py": "def function():\n    return 1\n", "consumer.py": "import provider\nx = provider.function()\n"}
    assert cross_file_scenarios(files)
    assert not cross_file_scenarios({**files, "other.py": "from provider import function\n"})
    assert not cross_file_scenarios({**files, "provider/__init__.py": ""})
    assert not cross_file_scenarios({"pkg/" + p: text for p, text in files.items()})
    assert not cross_file_scenarios({"sys.py": "def function():\n    pass\n", "consumer.py": "import sys\nsys.function()\n"})


def test_unicode_offsets_and_multi_prefix_family():
    source = "# привет 🌍\ndef _f(значение):\n    return значение + значение + значение\n"
    scenarios = list(generate_scenarios({"unicode.py": source}))
    renames = [s for s in scenarios if s.kind == "replace"]
    assert renames and renames[-1].target_after == source
    inserts = [s for s in scenarios if s.kind == "insert"]
    assert len(inserts) >= 2
    assert inserts[0].family_id == inserts[1].family_id
    assert len(inserts[1].history) > len(inserts[0].history)
    for s in scenarios:
        ast.parse(s.target_after)
        assert s.target_after.startswith("# привет 🌍\n")


def test_limits_invalid_and_replay():
    files = {"a.py": "x = 1 + 200\ny = 2 + 300\n", "b.txt": "ignored", "bad.py": "x = ("}
    assert list(generate_scenarios(files, max_scenarios_per_file=0)) == []
    scenarios = list(generate_scenarios(files, max_scenarios_per_file=1))
    assert len(scenarios) == 1 and scenarios[0].target_path == "a.py"
    with pytest.raises(ValueError):
        replay({"a": "x"}, [Step("a", "stale", "y")])
    with pytest.raises(ValueError):
        replay({"a": "x"}, [Step("a", "x", "x")])
    assert CAPABILITIES["python"] >= {"insert", "replace", "delete", "noop"}
    assert CAPABILITIES["go"] == {"insert", "noop"}
