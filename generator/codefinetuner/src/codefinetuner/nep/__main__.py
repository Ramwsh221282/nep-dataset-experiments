"""Generate NEP JSONL locally, without tokenization or model dependencies."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

from .engine import generate_scenarios
from .languages import CAPABILITIES
from .protocol import plain, render
from .context import ContextParseError, ContextStructureParserResolution
from .context.RepositorySymbolIndex import RepositorySymbolIndex

EXCLUDED = {".git", ".hg", ".svn", "node_modules", "vendor", "third_party", "build",
            "dist", "target", "__pycache__", "venv", "env"}


def read_repository(root: Path, *, max_file_bytes: int, max_files: int) -> tuple[dict, Counter]:
    files, skipped = {}, Counter()
    for directory, dirs, names in os.walk(root, followlinks=False):
        omitted = [d for d in dirs if d in EXCLUDED or d.startswith(".")
                   or (Path(directory) / d).is_symlink()]
        # Missing source must not be treated as evidence that no binding exists.
        harmless = {".git", ".hg", ".svn", "__pycache__", ".venv", ".venv-nep", "venv", "env"}
        skipped["uninspected_directories"] += sum(d not in harmless for d in omitted)
        dirs[:] = sorted(d for d in dirs if d not in omitted)
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if not ContextStructureParserResolution.supports(relative):
                continue
            if path.is_symlink():
                skipped["symlink_source"] += 1
                continue
            if path.stat().st_size > max_file_bytes:
                skipped["oversized_file"] += 1
                continue
            try:
                text = path.read_bytes().decode("utf-8")
                plain(text)
            except (UnicodeDecodeError, ValueError):
                skipped["encoding_or_reserved_tokens"] += 1
                continue
            files[relative] = text
            if len(files) > max_files:
                raise ValueError("Repository exceeds --max-files; select a smaller coherent source root")
    return files, skipped


def positive(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("Must be positive")
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="One repository/source root (read-only)")
    parser.add_argument("output", type=Path, help="New or empty artifact directory")
    parser.add_argument("--repository-id", required=True,
                        help="Stable repository identity, shared across revisions and variants")
    parser.add_argument("--split", required=True, choices=("train", "eval", "test"),
                        help="Assign the WHOLE repository to one split before generation")
    parser.add_argument("--max-file-bytes", type=positive, default=100_000)
    parser.add_argument("--max-files", type=positive, default=1000)
    parser.add_argument("--max-scenarios-per-file", type=positive, default=30)
    parser.add_argument("--max-example-chars", type=positive, default=24_000,
                        help="Reject oversized input+output; never truncate history or code")
    parser.add_argument("--max-context-chars", type=positive, default=6000,
                        help="Budget for processed context fragments; never truncate declarations")
    parser.add_argument("--include-noop", action="store_true",
                        help="Include synthetic completed trajectories (not inferred human intent)")
    args = parser.parse_args(argv)
    root, destination = args.source.resolve(), args.output.resolve()
    if not root.is_dir():
        parser.error("source must be a directory")
    if not args.repository_id.strip():
        parser.error("repository-id cannot be empty")
    if destination == root or root.is_relative_to(destination):
        parser.error("output cannot be the source directory or its ancestor")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        parser.error("output must be a new or empty directory; existing artifacts are never overwritten")
    try:
        files, skipped = read_repository(root, max_file_bytes=args.max_file_bytes, max_files=args.max_files)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if not files:
        parser.error("no supported UTF-8 source files found")
    allow_semantic = not any(skipped.values())
    destination.mkdir(parents=True, exist_ok=True)
    index = RepositorySymbolIndex(files)
    counts, seen = Counter(), set()
    snapshot_hash = hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with (destination / f"{args.split}.jsonl").open("x", encoding="utf-8") as dataset, \
            (destination / "audit.jsonl").open("x", encoding="utf-8") as audit:
        for scenario in generate_scenarios(files, max_scenarios_per_file=args.max_scenarios_per_file,
                                           allow_semantic=allow_semantic):
            if scenario.kind == "noop" and not args.include_noop:
                skipped["noop_disabled"] += 1
                continue
            # History providers are required; additional bound symbols are discovered at S_t.
            contexts = tuple(sorted({s.path for s in scenario.history} - {scenario.target_path}))
            for mode in ("at", "above", "below") if scenario.kind != "noop" else ("at",):
                try:
                    rendered = render(scenario, cursor_mode=mode, context_paths=contexts,
                                      include_blocks=True, max_context_chars=args.max_context_chars,
                                      repository_index=index)
                    row = {key: rendered[key] for key in ("input", "output")}
                except ContextParseError:
                    skipped["retrieval_context_unavailable"] += 1
                    continue
                except ValueError as exc:
                    if str(exc).startswith("No line "):
                        skipped["unavailable_cursor_variant"] += 1
                        continue
                    raise
                if len(row["input"]) + len(row["output"]) > args.max_example_chars:
                    skipped["example_budget"] += 1
                    continue
                fingerprint = hashlib.sha256((row["input"] + row["output"]).encode()).hexdigest()
                if fingerprint in seen:
                    skipped["duplicate"] += 1
                    continue
                seen.add(fingerprint)
                record_id = hashlib.sha256((args.repository_id + "\0" + fingerprint).encode()).hexdigest()
                dataset.write(json.dumps({"id": record_id, **row}, ensure_ascii=False) + "\n")
                # Keep ambiguity witnesses as well as positively retrieved files:
                # their absence on replay could turn an ambiguous import into a match.
                paths = {scenario.target_path, *contexts, *rendered['index_witness_paths']}
                provenance = {"initial": {p: scenario.initial[p] for p in sorted(paths)},
                              "history": [asdict(step) for step in scenario.history],
                              "target_path": scenario.target_path,
                              "target_after": scenario.target_after,
                              "kind": scenario.kind, "language": scenario.language,
                              "family_id": scenario.family_id}
                audit.write(json.dumps({"id": record_id, "repository_id": args.repository_id,
                                        "split": args.split, "snapshot_sha256": snapshot_hash,
                                        "cursor_mode": mode, "context_paths": contexts,
                                        "scenario": provenance,
                                        "input_blocks": rendered["input_blocks"],
                                        "context_diagnostics": rendered["context_diagnostics"],
                                        "index_witness_paths": rendered["index_witness_paths"],
                                        "max_context_chars": args.max_context_chars}, ensure_ascii=False) + "\n")
                counts[f"kind:{scenario.kind}"] += 1
                counts[f"language:{scenario.language}"] += 1
                counts[f"cursor:{mode}"] += 1
                for diagnostic in rendered["context_diagnostics"]:
                    counts["context:" + diagnostic["reason"]] += 1
    report = {"repository_id": args.repository_id, "split": args.split,
              "snapshot_sha256": snapshot_hash, "files": len(files), "examples": len(seen),
              "counts": dict(counts), "skipped": dict(skipped),
              "semantic_scenarios_enabled": allow_semantic,
              "capabilities": {k: sorted(v) for k, v in sorted(CAPABILITIES.items())},
              "context_extensions": sorted(ContextStructureParserResolution.STRATEGIES),
              "limits": {k: v for k, v in vars(args).items() if k.startswith("max_")},
              "notes": ["AST validity is not compilation or semantic correctness.",
                        "Files without eligible AST scenarios produce no examples.",
                        "Assign all revisions of a repository to the same split externally.",
                        "Noop labels express synthetic trajectory completion, not human intent."]}
    (destination / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
