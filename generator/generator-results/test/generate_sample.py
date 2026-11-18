"""Reproduce this ten-example inspection sample using the existing NEP engine.

Run with codefinetuner/.venv-nep/bin/python; output files are never overwritten.
The selection intentionally demonstrates supported Rust operations, not a
random or production-balanced dataset. No scenario transformations are added.
"""
from collections import Counter
from dataclasses import asdict
from difflib import unified_diff
import hashlib
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
GENERATOR = HERE.parents[1]
sys.path.insert(0, str(GENERATOR / "codefinetuner/src"))

from codefinetuner.nep.engine import generate_scenarios
from codefinetuner.nep.protocol import apply_completion, history_diff, parse_input, plain, render
from tree_sitter_language_pack import get_parser

SOURCE = GENERATOR / "generator-codebases/online-diary-backend-rust"
OUTPUTS = ("test.jsonl", "audit.jsonl", "report.json", "examples.md")
REPOSITORY = "online-diary-backend-rust"


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot():
    files = {}
    for directory, dirs, names in os.walk(SOURCE, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".")
                         and d not in {"target", "vendor", "node_modules"}
                         and not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            if path.suffix != ".rs" or path.is_symlink():
                continue
            text = path.read_bytes().decode("utf-8")
            plain(text)
            files[path.relative_to(SOURCE).as_posix()] = text
    return dict(sorted(files.items()))


def main():
    if any((HERE / name).exists() for name in OUTPUTS):
        raise SystemExit("Sample outputs already exist; will not overwrite them")
    files = snapshot()
    parser = get_parser("rust")
    modes = ["at", "above", "below", "at", "above", "below", "at", "above"]
    selected, used_paths, pending_noops = [], set(), {}
    counts = Counter()
    # One insertion per file, at least four inserted characters. For each
    # selected expression retain its finished trajectory for optional no-op.
    for scenario in generate_scenarios(files, max_scenarios_per_file=30, allow_semantic=False):
        counts[scenario.kind] += 1
        if scenario.kind == "noop" and scenario.family_id in pending_noops:
            pending_noops[scenario.family_id] = scenario
        if len(selected) >= 8:
            if sum(value is not None for value in pending_noops.values()) >= 2:
                break
            continue
        path = scenario.target_path
        if scenario.kind != "insert" or path in used_paths:
            continue
        before = scenario.current[path]
        if len(scenario.target_after) - len(before) < 4:
            continue
        mode = modes[len(selected)]
        try:
            row = render(scenario, cursor_mode=mode)
        except ValueError as exc:
            if str(exc).startswith("No line "):
                continue
            raise
        if len(row["input"]) + len(row["output"]) > 24_000:
            continue
        selected.append((scenario, mode, row))
        used_paths.add(path)
        pending_noops[scenario.family_id] = None
    for scenario in pending_noops.values():
        if scenario is not None and len(selected) < 10:
            selected.append((scenario, "at", render(scenario)))
    assert len(selected) == 10, f"Only {len(selected)} eligible examples"
    rows, audit, markdown = [], [], [
        "# 10 примеров NEP: online-diary-backend-rust\n",
        "Выборка текущего генератора: 8 insert из разных файлов и 2 noop. "
        "Это иллюстративный отбор, а не случайная или сбалансированная обучающая выборка.\n",
        "История синтетическая: генератор реконструирует ввод исходного AST-выражения "
        "частями. Это не реальные действия автора проекта. Для Rust сейчас нет "
        "rename/delete и межфайлового разрешения зависимостей; внешний контекст пуст.\n",
        "`cursor_mode` — положение курсора относительно правки: at — на месте, "
        "above — выше (правка ниже курсора), below — ниже (правка выше курсора). "
        "Для noop позиция at не обозначает реальную правку.\n",
        "Input может содержать незавершённый Rust. Target проверен Tree-sitter, "
        "но Cargo/typecheck не выполнялись. Вставки восстанавливают исходный файл.\n",
        "В блоках ниже показаны полные input/output без JSON-экранирования. "
        "В JSONL каждая запись занимает одну физическую строку.\n",
    ]
    snapshot_hash = digest(json.dumps(files, sort_keys=True, ensure_ascii=False))
    for index, (scenario, mode, row) in enumerate(selected, 1):
        path = scenario.target_path
        before = scenario.current[path]
        after = scenario.target_after
        assert parse_input(row["input"]).source == before
        assert apply_completion(row["input"], row["output"]) == after
        assert not parser.parse(after.encode()).root_node.has_error
        assert after == files[path]
        record_id = digest(REPOSITORY + "\0" + row["input"] + row["output"])
        rows.append({"id": record_id, **row})
        provenance = {
            "initial": {path: scenario.initial[path]},
            "history": [asdict(step) for step in scenario.history],
            "target_path": path, "target_after": after,
            "kind": scenario.kind, "language": scenario.language,
            "family_id": scenario.family_id,
        }
        audit.append({"id": record_id, "repository_id": REPOSITORY, "split": "test",
                      "snapshot_sha256": snapshot_hash, "cursor_mode": mode,
                      "context_paths": [], "synthetic": True, "scenario": provenance})
        diff = "".join(unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                    fromfile="current/" + path, tofile="target/" + path, n=2))
        markdown.extend([
            f"## {index:02d}. {scenario.kind} · cursor={mode}\n",
            f"Файл: `{path}`  \nID: `{record_id}`  \nШагов истории: {len(scenario.history)}\n",
            "### Уже выполненная синтетическая история\n",
            "````diff\n" + history_diff(scenario) + "````\n",
            "### Следующая правка\n",
            "````diff\n" + diff + "````\n" if diff else "Изменений кода нет: траектория завершена.\n",
            "### Input\n", "````text\n" + row["input"] + "\n````\n",
            "### Output\n", "````text\n" + row["output"] + "\n````\n",
        ])
        print(f"{index:02d} {scenario.kind:6} cursor={mode:5} {path} history={len(scenario.history)}")
        if diff:
            print(diff)
    assert len({row["id"] for row in rows}) == 10
    assert files == snapshot(), "Source repository changed during generation"
    report = {
        "repository_id": REPOSITORY, "source": str(SOURCE), "split": "test",
        "purpose": "inspection_sample_not_random_sampling", "examples": len(rows),
        "source_rust_files": len(files), "source_snapshot_sha256": snapshot_hash,
        "excluded_directories": ["hidden directories", "target", "vendor", "node_modules", "symlinks"],
        "kind_counts": dict(Counter(s.kind for s, _, _ in selected)),
        "cursor_counts": dict(Counter(mode for _, mode, _ in selected)),
        "distinct_target_files": len(used_paths), "external_context_files": 0,
        "selection": "First eligible insertion per file, >=4 added characters, cycling cursor modes; two completed trajectories from selected families",
        "enumerated_scenarios_before_stopping": dict(counts),
        "limits": {"max_scenarios_per_file": 30, "max_example_chars": 24_000},
        "validation": {"history_replay": True, "protocol_and_exact_application": True,
                       "target_tree_sitter_parse": True, "target_matches_original_source": True,
                       "source_snapshot_unchanged": True, "cargo_check_run": False},
        "limitations": ["Rust supports only structural insert and synthetic noop scenarios",
                        "History is reconstructed, not a real developer trace",
                        "No Rust semantic replacement/deletion or cross-file dependency context",
                        "AST validity is not type checking or evidence that history is necessary for prediction"],
    }
    for name, text in [
        ("test.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)),
        ("audit.jsonl", "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in audit)),
        ("report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n"),
        ("examples.md", "\n".join(markdown)),
    ]:
        with (HERE / name).open("x", encoding="utf-8") as output:
            output.write(text)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
