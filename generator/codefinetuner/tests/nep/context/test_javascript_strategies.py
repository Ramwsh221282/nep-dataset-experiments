import pytest

from codefinetuner.nep.context.ContextStructureParserAlgorithm import ContextParseError
from codefinetuner.nep.context.JavascriptContextStructureParser import JavascriptContextStructureParser
from codefinetuner.nep.context.TypescriptContextStructureParser import TypescriptContextStructureParser
from codefinetuner.nep.context.JSXContextStructureParser import JSXContextStructureParser
from codefinetuner.nep.context.TSXContextStructureParser import TSXContextStructureParser


def output(parser, source):
    return "\n".join(f.code_text for f in parser.parse("example", source))


@pytest.mark.parametrize("parser", [JavascriptContextStructureParser(), TypescriptContextStructureParser()])
def test_normal_code_recursively_strips_implementations(parser):
    source = '''export const data = { value: 42, run(x) { secret(x); },
      nested: { f: (x) => x + secret(), g: function() { hidden(); } } };
      export function run(a) { secret(a); }
      class Test { field = 3; method(a) { privateEffect(); } }
      const hook = useMemo(() => expensive(), []);'''
    result = output(parser, source)
    for expected in ("value: 42", "run(x) ;", "field = 3", "method(a) ;", "useMemo(() => {/* тело функции опущено */}, [])"):
        assert expected in result
    for forbidden in ("secret(", "hidden()", "privateEffect()", "expensive()"):
        assert forbidden not in result


@pytest.mark.parametrize("parser", [JavascriptContextStructureParser(), TypescriptContextStructureParser(), JSXContextStructureParser(), TSXContextStructureParser()])
def test_class_static_blocks_are_omitted_recursively(parser):
    source = '''export class A {
      static { doSecret(); const value = hidden(); }
      static field = preserveValue();
      method() { methodSecret(); }
      static { anotherSecret(); }
    }
    export const Nested = class { static { nestedSecret(); } field = 42; };'''
    result = output(parser, source)
    for expected in ('export class A', 'static field = preserveValue()', 'method()', 'export const Nested', 'field = 42'):
        assert expected in result
    for forbidden in ('doSecret()', 'hidden()', 'anotherSecret()', 'nestedSecret()', 'methodSecret()', 'static {'):
        assert forbidden not in result


def test_typescript_types_decorators_and_namespaces():
    source = '''export namespace API {
      export interface Props { value: string; onSelect: (id: string) => void; }
      @sealed export class Box<T extends string> {
        @field readonly value: T = "x" as T;
        @log getValue<U extends T>(v: U): T { return secret(v); }
      }
      export const n: number = 4;
    }'''
    result = output(TypescriptContextStructureParser(), source)
    for expected in ("namespace API", "interface Props", "onSelect: (id: string) => void", "@sealed", "@log", "<U extends T>", "value: T", "n: number = 4"):
        assert expected in result
    assert "secret(v)" not in result


def test_jsx_structure_and_handlers():
    source = '''export function SearchPanel({ initialQuery = "", onSearch }) {
      const placeholder = "Search users";
      let query = initialQuery;
      function normalizeQuery(value) { return value.trim(); }
      const handleChange = (event) => { query = event.target.value; };
      performEffect();
      return <form onSubmit={(event) => { event.preventDefault(); onSearch(); }}>
        <input placeholder={placeholder} onChange={handleChange} />
        <button>Search</button>
      </form>;
    }'''
    result = output(JSXContextStructureParser(), source)
    for expected in ("function SearchPanel", 'const placeholder = "Search users"', "let query = initialQuery", "function normalizeQuery(value) ;", "<form", "<button>Search</button>", "onSubmit={(event) => {/* тело функции опущено */}}"):
        assert expected in result
    for forbidden in ("value.trim()", "event.target.value", "performEffect()", "preventDefault()", "onSearch()"):
        assert forbidden not in result


def test_tsx_component_and_render_with_lists_and_hook_callbacks():
    source = '''export interface UserCardProps { user: User; onSelect: (id: string) => void; }
    export const UserCard = ({ user, onSelect }: UserCardProps): React.ReactElement => {
      const title = formatUserName(user);
      let subtitle: string = user.email;
      const computed = useMemo(() => { secretHook(); return title; }, [title]);
      function selectUser(id: string): void { onSelect(id); }
      const handleClick = (): void => { selectUser(user.id); };
      effect();
      return <><button onClick={() => onSelect(user.id)} {...user}>{title}</button>
        {user.items.map(item => { const leakValue = secretList(); leak(); return <span>{item}</span>; })}
        {subtitle && <i>{subtitle}</i>}</>;
    };
    class View extends React.Component { helper() { secretMethod(); }
      render() { effectInRender(); return <UserCard user={this.props.user} />; } }'''
    result = output(TSXContextStructureParser(), source)
    for expected in ("interface UserCardProps", "onSelect: (id: string) => void", "const title = formatUserName(user)", "let subtitle: string = user.email", "useMemo(() => {/* тело функции опущено */}", "function selectUser(id: string): void ;", "<span>{item}</span>", "<i>{subtitle}</i>", "{...user}", "render()", "<UserCard"):
        assert expected in result
    for forbidden in ("secretHook()", "onSelect(id);", "selectUser(user.id)", "effect()", "leak()", "secretList()", "secretMethod()", "effectInRender()", "onSelect(user.id)"):
        assert forbidden not in result
    assert "onClick={() => {/* тело функции опущено */}}" in result


@pytest.mark.parametrize("parser", [JavascriptContextStructureParser(), TypescriptContextStructureParser(), JSXContextStructureParser(), TSXContextStructureParser()])
def test_utf8_spans_and_errors(parser):
    source = 'const привет = "мир";\nexport function f(x) { return x; }'
    fragments = parser.parse("unicode", source)
    assert len(fragments) == 2
    encoded = source.encode()
    assert encoded[fragments[0].start_byte:fragments[0].end_byte].decode() == 'const привет = "мир";'
    assert encoded[fragments[1].start_byte:fragments[1].end_byte].decode() == 'export function f(x) { return x; }'
    assert all(f.path == "unicode" and f.format == parser.format for f in fragments)
    with pytest.raises(ContextParseError):
        parser.parse("broken", "export function (")


def test_conditional_component_returns_strip_branch_effects():
    source = 'function C() { switch (x) { case 1: bad(); return <A/>; default: worse(); return <B/>; } }'
    result = output(JSXContextStructureParser(), source)
    assert '<A/>' in result and '<B/>' in result
    assert 'bad()' not in result and 'worse()' not in result


@pytest.mark.parametrize("parser", [TypescriptContextStructureParser(), TSXContextStructureParser()])
def test_class_fields_accessors_and_anonymous_default_do_not_leak(parser):
    source = '''export default function () { anonymousEffect(); }
    export class Store {
      value: number = 7;
      handler: (n: number) => number = (n) => fieldEffect(n);
      get current(): number { return getterEffect(); }
      set current(n: number) { setterEffect(n); }
      helper() { helperEffect(); return <X/>; }
    }'''
    if not parser.jsx:
        source = source.replace('return <X/>;', 'return 1;')
    result = output(parser, source)
    for text in ('export default function', 'value: number = 7', 'handler: (n: number) => number',
                 'get current()', 'set current(n: number)', 'helper()'):
        assert text in result
    for text in ('anonymousEffect()', 'fieldEffect(n)', 'getterEffect()', 'setterEffect(n)', 'helperEffect()', '<X/>'):
        assert text not in result


def test_jsx_helpers_and_nested_callbacks_are_not_components():
    source = '''const obj = { helper() { effect(); return <Helper/>; } };
    const helper = () => wrap(() => <Nested/>);
    class View { get render() { getterEffect(); return <Bad/>; }
      field = (() => { fieldEffect(); return <BadField/>; }); }
    const List = () => items.map(item => <Item value={item}/>);'''
    result = output(TSXContextStructureParser(), source)
    for text in ('effect()', '<Helper/>', '<Nested/>', 'getterEffect()', '<Bad/>', 'fieldEffect()', '<BadField/>'):
        assert text not in result
    assert '<Item value={item}/>' in result


def test_jsx_attribute_callback_returning_jsx_is_omitted():
    source = 'export const C = () => <X render={() => { effect(); return <Y/>; }} other={() => <Z/>}/>;'
    result = output(TSXContextStructureParser(), source)
    assert '<X ' in result
    assert result.count('=> {/* тело функции опущено */}') == 2
    assert 'effect()' not in result and '<Y/>' not in result and '<Z/>' not in result


def test_js_file_with_jsx_uses_structural_rendering():
    source = 'export function View() { const value = compute(); effect(); return <div>{value}</div>; }'
    result = JavascriptContextStructureParser().parse('src/view.js', source)[0]
    assert result.format == 'javascript'
    assert 'const value = compute()' in result.code_text
    assert '<div>{value}</div>' in result.code_text
    assert 'effect()' not in result.code_text


@pytest.mark.parametrize('error', [LookupError('missing grammar'), ImportError('missing package')])
def test_parser_initialization_errors_are_context_errors(monkeypatch, error):
    def fail(_language):
        raise error
    monkeypatch.setattr('codefinetuner.nep.context.JavascriptStructureSupport.get_parser', fail)
    with pytest.raises(ContextParseError) as exc:
        JavascriptContextStructureParser().parse('file.js', 'const a = 1;')
    assert exc.value.__cause__ is error
    assert 'file.js' in str(exc.value)


def test_root_tsx_mixed_declarations_example():
    source = '''export interface UserCardProps {
  user: User;
  compact?: boolean;
  onSelect: (id: string) => void;
}
export const UserCard = ({ user, compact = false, onSelect }: UserCardProps): React.ReactElement => {
  const title = formatUserName(user);
  let subtitle: string = compact ? "" : user.email;
  function selectUser(id: string): void { onSelect(id); }
  const handleClick = (): void => { selectUser(user.id); };
  let formatLabel = (value: string): string => value.trim();
  return <button onClick={handleClick} aria-label={formatLabel(title)}>
    <strong>{title}</strong>{!compact && <span>{subtitle}</span>}
  </button>;
};'''
    result = output(TSXContextStructureParser(), source)
    for text in ('export interface UserCardProps', 'onSelect: (id: string) => void',
                 'const title = formatUserName(user)', 'let subtitle: string = compact ? "" : user.email',
                 'function selectUser(id: string): void ;', 'const handleClick = (): void => {/* тело функции опущено */}',
                 'let formatLabel = (value: string): string => {/* тело функции опущено */}',
                 '<strong>{title}</strong>', '!compact && <span>{subtitle}</span>'):
        assert text in result
    for text in ('onSelect(id);', 'selectUser(user.id);', 'value.trim()'):
        assert text not in result


def test_jsx_does_not_invent_types_or_treat_callbacks_as_components():
    result = output(JSXContextStructureParser(), 'const cb = (x) => { effect(x); return x; }; export function Widget() { return <div/>; }')
    assert "effect(x)" not in result
    assert "<div/>" in result
    assert ": React" not in result
