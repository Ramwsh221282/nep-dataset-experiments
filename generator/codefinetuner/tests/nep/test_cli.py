import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from codefinetuner.nep.__main__ import main
from codefinetuner.nep.engine import Scenario, Step
from codefinetuner.nep.protocol import render


def test_cli_reproducible_artifacts_and_audit_replay(tmp_path, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "provider.py").write_text("def calculate(x):\n    return x + 123\n")
    (root / "consumer.py").write_text("import provider\nx = provider.calculate(1)\ny = provider.calculate(2)\n")
    for name in ("one", "two"):
        main([str(root), str(tmp_path / name), "--repository-id", "demo", "--split", "train"])
    first, second = tmp_path / "one", tmp_path / "two"
    for name in ("train.jsonl", "audit.jsonl", "report.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    rows = [json.loads(line) for line in (first / "train.jsonl").read_text().splitlines()]
    audit = [json.loads(line) for line in (first / "audit.jsonl").read_text().splitlines()]
    assert rows and len(rows) == len(audit)
    for row, provenance in zip(rows, audit):
        data = provenance["scenario"]
        data["history"] = [Step(**s) for s in data["history"]]
        rebuilt = render(Scenario(**data), cursor_mode=provenance["cursor_mode"],
                         context_paths=tuple(provenance["context_paths"]))
        assert row == {"id": provenance["id"], **rebuilt}
        assert provenance["split"] == "train"
    assert (root / "consumer.py").read_text().startswith("import provider\nx = provider.calculate")
    with pytest.raises(SystemExit):
        main([str(root), str(first), "--repository-id", "demo", "--split", "train"])


def test_incomplete_snapshot_disables_semantic_scenarios(tmp_path, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "provider.py").write_text("def function():\n    return 1\n")
    (root / "consumer.py").write_text("import provider\nx = provider.function()\n")
    (root / "provider").mkdir()
    (root / "provider/__init__.py").write_text("#" + "x" * 300)
    output = tmp_path / "out"
    main([str(root), str(output), "--repository-id", "demo", "--split", "test", "--max-file-bytes", "100"])
    report = json.loads((output / "report.json").read_text())
    assert not report["semantic_scenarios_enabled"]
    assert report["skipped"]["oversized_file"] == 1
    assert report["counts"].get("kind:replace", 0) == 0


def test_budget_rejects_without_truncation(tmp_path, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("value = 1 + 234\n")
    out = tmp_path / "out"
    main([str(root), str(out), "--repository-id", "demo", "--split", "eval", "--max-example-chars", "1"])
    report = json.loads((out / "report.json").read_text())
    assert report["examples"] == 0
    assert report["skipped"]["example_budget"] > 0
    assert (out / "eval.jsonl").read_text() == ""


def test_dataset_module_does_not_import_training_stack():
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
    result = subprocess.run([sys.executable, "-c",
                             "import codefinetuner.nep.__main__, sys; "
                             "assert not {'torch', 'transformers', 'datasets', 'omegaconf'} & sys.modules.keys()"],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
