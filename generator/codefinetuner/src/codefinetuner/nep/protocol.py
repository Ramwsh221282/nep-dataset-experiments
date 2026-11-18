"""NEP SPM serialization and independent range-application validation.

Offsets here are Unicode character offsets; AST byte offsets are resolved by the
scenario engine before serialization. A completion always replaces ONE range.
"""
from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from difflib import unified_diff

from .engine import Scenario

SUFFIX = "<|fim_suffix|>"
PREFIX = "<|fim_prefix|>"
MIDDLE = "<|fim_middle|>"
SEP = "<|file_sep|>"
CURSOR = "<|user_cursor|>"
EOS = "<|endoftext|>"
BOS = "<[begin▁of▁sentence]>"
MARKER = re.compile(r"<\|marker_(\d+)\|>")
RESERVED = re.compile(r"<\|[^\n]*?\|>|<filename>|<\[begin▁of▁sentence\]>")


def marker(number: int) -> str:
    return f"<|marker_{number}|>"


def plain(text: str) -> None:
    if RESERVED.search(text):
        raise ValueError("Source contains reserved NEP/chat separators")


def check_path(path: str) -> None:
    plain(path)
    if not path or path == "edit_history" or "\n" in path or "\r" in path:
        raise ValueError("Invalid file header")


@dataclass
class ParsedInput:
    path: str
    source: str
    boundaries: dict[int, int]
    cursor: int


def parse_input(prompt: str) -> ParsedInput:
    if prompt.startswith(BOS):
        prompt = prompt[len(BOS):]
    if (not prompt.startswith(SUFFIX) or not prompt.endswith(MIDDLE)
            or any(prompt.count(t) != 1 for t in (SUFFIX, PREFIX, MIDDLE))
            or EOS in prompt or prompt.count(CURSOR) != 1):
        raise ValueError("Invalid SPM framing")
    suffix, prefix = prompt[len(SUFFIX):-len(MIDDLE)].split(PREFIX)
    plain(suffix)
    sections = re.split(r"<\|file_sep\|>|<filename>", prefix)
    if sections[0] or len(sections) < 3:
        raise ValueError("Missing context/history/current file headers")
    headers = []
    for section in sections[1:]:
        path, sep, content = section.partition("\n")
        if not sep:
            raise ValueError("Header must end with newline")
        headers.append((path, content))
    if sum(path == "edit_history" for path, _ in headers[:-1]) != 1:
        raise ValueError("Exactly one history section is required")
    for path, content in headers[:-1]:
        if path != "edit_history":
            check_path(path)
        plain(content)
    path, marked = headers[-1]
    check_path(path)
    matches = list(MARKER.finditer(marked))
    if len(matches) < 2 or [int(m[1]) for m in matches] != list(range(1, len(matches) + 1)):
        raise ValueError("Input markers must be consecutive and unique, starting at 1")
    cursor_pos = marked.find(CURSOR)
    if not matches[0].end() <= cursor_pos < matches[-1].start():
        raise ValueError("Cursor must be inside editable boundaries")
    source, boundaries, last = "", {}, 0
    cursor = None
    for token in re.finditer(r"<\|marker_\d+\|>|<\|user_cursor\|>", marked):
        source += marked[last:token.start()]
        if token[0] == CURSOR:
            cursor = len(source)
        else:
            boundaries[int(MARKER.fullmatch(token[0])[1])] = len(source)
        last = token.end()
    source += marked[last:]
    plain(source)
    return ParsedInput(path, source + suffix, boundaries, cursor)


def apply_completion(prompt: str, completion: str) -> str:
    parsed = parse_input(prompt)
    if not completion.endswith(EOS) or completion.count(EOS) != 1:
        raise ValueError("Completion must end with exactly one EOS")
    body = completion[:-len(EOS)].strip()
    matches = list(MARKER.finditer(body))
    if len(matches) < 2 or matches[0].start() != 0 or matches[-1].end() != len(body):
        raise ValueError("Completion must be enclosed by boundaries")
    numbers = [int(m[1]) for m in matches]
    if any(n not in parsed.boundaries for n in numbers):
        raise ValueError("Completion references an unknown boundary")
    replacement = MARKER.sub("", body[matches[0].end():matches[-1].start()])
    if replacement.count(CURSOR) != 1:
        raise ValueError("Completion requires one cursor inside boundaries")
    replacement = replacement.replace(CURSOR, "")
    plain(replacement)
    if len(numbers) == 2 and numbers[0] == numbers[1] and not replacement.strip():
        return parsed.source
    if any(a >= b for a, b in zip(numbers, numbers[1:])):
        raise ValueError("Replacement boundaries must strictly increase")
    start, end = parsed.boundaries[numbers[0]], parsed.boundaries[numbers[-1]]
    return parsed.source[:start] + replacement + parsed.source[end:]


def changed_range(before: str, after: str) -> tuple[int, int, int]:
    """Return shared start, old end, new end for a single encompassing edit."""
    start = 0
    while start < min(len(before), len(after)) and before[start] == after[start]:
        start += 1
    end, new_end = len(before), len(after)
    while end > start and new_end > start and before[end - 1] == after[new_end - 1]:
        end -= 1
        new_end -= 1
    return start, end, new_end


def history_diff(scenario: Scenario) -> str:
    chunks = []
    for step in scenario.history:
        check_path(step.path)
        plain(step.before)
        plain(step.after)
        for line in unified_diff(step.before.splitlines(keepends=True),
                                 step.after.splitlines(keepends=True),
                                 fromfile=f"a/{step.path}", tofile=f"b/{step.path}", n=3):
            chunks.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(chunks)


def render(scenario: Scenario, *, cursor_mode: str = "at", context_paths: tuple[str, ...] = (),
           include_blocks: bool = False, max_context_chars: int = 6000, repository_index=None) -> dict:
    """Render one cursor variant; unavailable above/below variants raise ValueError."""
    if cursor_mode not in {"above", "at", "below"}:
        raise ValueError("Unknown cursor mode")
    if not scenario.history:
        raise ValueError("A causal example requires actual prior edits")
    state = scenario.current
    path = scenario.target_path
    check_path(path)
    before, after = state[path], scenario.target_after
    plain(before)
    plain(after)
    is_noop = before == after
    if is_noop != (scenario.kind == "noop"):
        raise ValueError("Scenario kind does not agree with applied change")
    start, end, new_end = changed_range(before, after) if not is_noop else (0, 0, 0)
    offsets = [0]
    for line in before.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    if len(offsets) == 1:
        # Empty files still need two distinct protocol boundaries at offset zero.
        offsets.append(0)
    left = min(bisect_right(offsets, start) - 1, len(offsets) - 2)
    right = max(left + 1, bisect_left(offsets, end))
    right = min(right, len(offsets) - 1)
    if cursor_mode == "above":
        if left == 0:
            raise ValueError("No line above this edit")
        cursor = offsets[left - 1]
    elif cursor_mode == "below":
        if right >= len(offsets) - 1:
            raise ValueError("No line below this edit")
        cursor = offsets[right]
    else:
        cursor = start
    window_left = max(0, left - 1)
    window_right = min(len(offsets) - 1, right + 1)
    bounds = offsets[window_left:window_right + 1]
    from .InputPromptBuilder import InputPromptBuilder
    from .context.ContextPreparation import ContextPreparation

    byte_bounds = [len(before[:offset].encode("utf-8")) for offset in bounds]
    from .context.RepositorySymbolIndex import RepositorySymbolIndex

    if repository_index is None:
        repository_index = RepositorySymbolIndex(scenario.initial)
    cursor_byte = len(before[:cursor].encode("utf-8"))
    view = repository_index.view(state)
    retrieval, current, diagnostics = ContextPreparation().prepare(
        view, path, cursor_byte, scenario.history,
        byte_bounds[0], byte_bounds[-1], required_paths=context_paths, max_chars=max_context_chars)
    blocks = {
        "schema_version": InputPromptBuilder.SCHEMA_VERSION,
        "retrieval_context": retrieval,
        "current_file_context": current,
        "edit_history": [{"id": i, "path": step.path, "before": step.before, "after": step.after}
                         for i, step in enumerate(scenario.history, 1)],
        "edit_target": {"path": path, "code_text": before, "position_encoding": "utf8_bytes",
                        "cursor": {"offset": cursor_byte},
                        "markers": [{"id": i, "offset": offset} for i, offset in enumerate(byte_bounds, 1)]},
    }
    prompt = InputPromptBuilder().build(blocks)
    blocks["input_result"] = prompt
    first, last = left - window_left + 1, right - window_left + 1
    if is_noop:
        completion = marker(first) + CURSOR + marker(first) + EOS
    else:
        replacement = (before[offsets[left]:start] + after[start:new_end] + CURSOR
                       + before[end:offsets[right]])
        completion = marker(first) + replacement + marker(last) + EOS
    if parse_input(prompt).source != before or apply_completion(prompt, completion) != after:
        raise ValueError("NEP serialization failed exact replay validation")
    result = {"input": prompt, "output": completion}
    if include_blocks:
        result.update(input_blocks=blocks, context_diagnostics=diagnostics,
                      index_witness_paths=sorted(view.files.witnesses))
    return result
