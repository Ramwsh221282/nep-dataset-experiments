import importlib

import pytest

from codefinetuner.nep.context.ContextStructureParserAlgorithm import ContextParseError


def parser(name):
    module = importlib.import_module(
        f"codefinetuner.nep.context.{name}ContextStructureParser"
    )
    return getattr(module, f"{name}ContextStructureParser")()


CASES = [
    ("Python", "# café\nimport os\n@decorator\nclass Box:\n    value: int = 42\n    callback = lambda x: secret_lambda(x)\n    @decorator\n    def get(self, x: int) -> str:\n        secret_body()\n", ["import os", "@decorator", "class Box", "value: int = 42", "def get(self, x: int) -> str", "lambda x:"]),
    ("Rust", "// café\nuse std::fmt;\n#[derive(Debug)]\nstruct Boxed<T> { value: T }\nimpl<T> Boxed<T> { pub fn get(&self) -> &T { secret_body() } }\nconst F: fn(i32)->i32 = |x| secret_lambda(x);", ["use std::fmt", "#[derive(Debug)]", "struct Boxed<T>", "value: T", "impl<T>", "fn get(&self) -> &T", "|x|"]),
    ("Csharp", "// café\nusing System;\nnamespace N { [Attr] class Box<T> { int value = 42; int P { get { return secret_body(); } set => secret_body(); } Box() { secret_body(); } int Get(int x) => secret_body(); Func<int,int> f = x => secret_lambda(x); } }", ["using System", "namespace N", "[Attr]", "class Box<T>", "int value = 42", "int P", "Box()", "int Get(int x)"]),
    ("Zig", "// café\nconst Box = struct { value: u32 = 42, pub fn get(x: u32) u32 { return secret_body(x); } };", ["const Box", "value: u32 = 42", "pub fn get(x: u32) u32"]),
    ("C", "// café\n#include <stdio.h>\nstruct Box { int value; };\nint value = 42;\nint get(int x) { return secret_body(x); }", ["#include <stdio.h>", "struct Box", "int value", "= 42", "int get(int x)"]),
    ("Cpp", "// café\n#include <vector>\nnamespace N { template<class T> class Box { T value; int get(int x) { return secret_body(x); } }; auto f = [](int x) { return secret_lambda(x); }; }", ["#include <vector>", "namespace N", "template<class T>", "class Box", "T value", "int get(int x)", "[](int x)"]),
    ("Java", "// café\nimport java.util.List;\nclass Box<T extends Number> { int value = 42; Box() { secret_body(); } @Override public String toString() { return secret_body(); } Runnable r = () -> secret_lambda(); }", ["import java.util.List", "class Box<T extends Number>", "int value = 42", "Box()", "@Override", "public String toString()"]),
    ("Go", "// café\npackage example\nimport \"fmt\"\ntype Box struct { Value int }\nconst Value = 42\nfunc (b Box) Get(x int) int { return secret_body(x) }\nvar f = func(x int) int { return secret_lambda(x) }", ["package example", "import \"fmt\"", "type Box struct", "Value int", "const Value = 42", "func (b Box) Get(x int) int"]),
]


@pytest.mark.parametrize("name,source,expected", CASES)
def test_declarations_and_no_bodies(name, source, expected):
    fragments = parser(name).parse("dir/example", source)
    result = "\n".join(fragment.code_text for fragment in fragments)
    for item in expected:
        assert item in result
    assert "secret_body" not in result
    assert "secret_lambda" not in result
    data = source.encode("utf-8")
    for fragment in fragments:
        assert fragment.path == "dir/example"
        assert 0 <= fragment.start_byte < fragment.end_byte <= len(data)
        assert data[fragment.start_byte:fragment.end_byte].decode("utf-8").strip()
    prefix = "# café" if name == "Python" else "// café"
    assert fragments[0].start_byte >= len(prefix.encode("utf-8"))


@pytest.mark.parametrize("name,source,_", CASES)
def test_invalid_syntax_fails_closed(name, source, _):
    with pytest.raises(ContextParseError):
        parser(name).parse("broken", source + "\n((((")


def test_unavailable_grammar_fails_closed(monkeypatch):
    import tree_sitter_language_pack

    def missing(_):
        raise LookupError("missing grammar")

    monkeypatch.setattr(tree_sitter_language_pack, "get_parser", missing)
    with pytest.raises(ContextParseError, match="missing grammar"):
        parser("Python").parse("x.py", "x = 1")


@pytest.mark.parametrize("name,source", [
    ("Csharp", "class C { object f = delegate(int x) { secret_body(); }; int P => secret_body(); }"),
    ("Cpp", "struct A { A() try : x(1) { secret_body(); } catch(...) { secret_body(); } int x; };"),
    ("Python", "def f():\n    # secret_body\n    return 1\n"),
    ("Zig", 'const X = struct { test "x" { secret_body(); } comptime { secret_body(); } };'),
])
def test_unusual_function_bodies_are_omitted(name, source):
    assert "secret_body" not in "\n".join(f.code_text for f in parser(name).parse("x", source))


def test_python_class_executable_statements_are_omitted():
    source = 'class C:\n    secret_body()\n    if True:\n        secret_body()\n    value = 3\n'
    result = parser("Python").parse("x.py", source)[0].code_text
    assert "secret_body" not in result
    assert "value = 3" in result


def test_rust_nested_module_generic_where_clauses_and_attributes():
    source = '''// café
mod inner {
    #[derive(Debug)]
    pub struct Boxed<T> where T: Clone { value: T }
    impl<T> Boxed<T> where T: Clone {
        #[inline]
        pub fn get<U: Copy>(&self, arg: U) -> T where U: Into<T> {
            fn nested() { secret_nested(); }
            secret_method()
        }
    }
}
'''
    fragment, = parser("Rust").parse("x.rs", source)
    for text in ("mod inner", "#[derive(Debug)]", "pub struct Boxed<T> where T: Clone",
                 "value: T", "impl<T> Boxed<T> where T: Clone", "#[inline]",
                 "pub fn get<U: Copy>(&self, arg: U) -> T where U: Into<T>"):
        assert text in fragment.code_text
    assert "secret_" not in fragment.code_text
    assert "fn nested" not in fragment.code_text
    original = source.encode("utf-8")
    assert original[fragment.start_byte:fragment.end_byte].decode().startswith("mod inner")
    assert original[fragment.start_byte:fragment.end_byte].decode().endswith("}")


def test_python_dataclass_decorators_fields_and_nested_lambdas():
    source = '''from dataclasses import dataclass, field
@dataclass(frozen=True)
class Box:
    count: int = 42
    values: list[int] = field(default_factory=lambda: secret_factory())

    @staticmethod
    @logged(level=2)
    def get(x: int = 7) -> str:
        def nested():
            secret_nested()
        return secret_method()
'''
    fragments = parser("Python").parse("x.py", source)
    result = "\n".join(f.code_text for f in fragments)
    for text in ("from dataclasses import dataclass, field", "@dataclass(frozen=True)",
                 "count: int = 42", "values: list[int] = field(default_factory=lambda: ...)",
                 "@staticmethod", "@logged(level=2)", "def get(x: int = 7) -> str"):
        assert text in result
    assert "secret_" not in result
    assert "def nested" not in result


def test_csharp_nested_namespaces_accessors_constructor_and_initializers():
    source = '''namespace Outer { namespace Inner {
class C<T> : Base {
    public C(int n = 5) : base(() => secret_init()) {
        void Nested() { secret_nested(); }
        secret_ctor();
    }
    int P { get => secret_get(); set { secret_set(); } }
    int Q => secret_q();
    int Count { get; set; } = 42;
    System.Func<int> Factory { get; } = () => secret_factory();
}
} }'''
    result = parser("Csharp").parse("x.cs", source)[0].code_text
    for text in ("namespace Outer", "namespace Inner", "class C<T> : Base",
                 "public C(int n = 5) : base(() => { /* body omitted */ })",
                 "int P", "get ;", "set ;", "int Q", "int Count { get; set; } = 42",
                 "Factory { get; } = () => { /* body omitted */ }"):
        assert text in result
    assert "secret_" not in result
    assert "void Nested" not in result


def test_cpp_templates_requires_defaults_attributes_and_nested_lambdas():
    source = '''namespace outer {
template<typename T> requires requires(T t) { t.foo(); }
struct Box {
    [[nodiscard]] auto get(T x = T{}, int n = [](){ return secret_default(); }()) const
        -> T requires Comparable<T> {
        auto f = [](){ return secret_nested(); };
        return x;
    }
    Box() : value([](){ return secret_init(); }()) { secret_ctor(); }
    T value;
};
}'''
    result = parser("Cpp").parse("x.cpp", source)[0].code_text
    for text in ("namespace outer", "template<typename T>", "requires requires(T t) { t.foo(); }",
                 "struct Box", "[[nodiscard]]", "T x = T{}", "-> T requires Comparable<T>",
                 "T value", "[](){ /* body omitted */ }"):
        assert text in result
    assert "secret_" not in result


@pytest.mark.parametrize("name,source", [
    ("Rust", "const F: fn(i32) -> i32 = |x| secret_body(x);"),
    ("Csharp", "class C { object f = delegate(int x) { secret_body(); }; }"),
    ("Cpp", "auto f = [](int x) { return secret_body(x); };"),
    ("Java", "class C { Runnable f = () -> secret_body(); }"),
    ("Go", "package p\nvar f = func() { secret_body() }"),
])
def test_non_python_function_values_are_explicitly_omitted(name, source):
    result = "\n".join(f.code_text for f in parser(name).parse("x", source))
    assert "{ /* body omitted */ }" in result
    assert "secret_body" not in result
