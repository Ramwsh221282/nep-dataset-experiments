"""Guard the physical source tree, not just the public entry point."""
import ast
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_DEPENDENCIES = {
    "torch", "tensorflow", "keras", "peft", "unsloth", "bitsandbytes",
    "accelerate", "mlflow", "gguf", "evaluate", "datasets", "trl", "codebleu",
    "matplotlib",
}


def test_only_dataset_modules_exist_on_disk():
    package = ROOT / "src/codefinetuner"
    components = {p.name for p in package.iterdir() if p.name != "__pycache__"}
    assert components == {"__init__.py", "pipeline.py", "preprocess", "nep"}
    for name in ("finetune", "evaluate", "convert"):
        assert not (ROOT / "tests" / name).exists()
    assert not (ROOT / "Dockerfile.gpu").exists()
    assert not (package / "preprocess/analyze.py").exists()
    scripts = {p.name for p in (ROOT / "scripts").iterdir() if p.is_file()}
    assert scripts == {"decode_dataset.py", "print_treesitter_tree.py",
                       "test_ast_parser.py", "unzip_all_in_directory.py"}


def test_no_training_imports_or_calls_in_owned_runtime_code():
    paths = [*(ROOT / "src").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert not {n.name.split(".")[0] for n in node.names} & FORBIDDEN_DEPENDENCIES, path
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in FORBIDDEN_DEPENDENCIES, path
                if (node.module or "").split(".")[0] == "transformers":
                    assert {n.name for n in node.names} <= {"AutoTokenizer"}, path
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"train", "fit", "backward", "merge_and_unload"}, path


def test_lock_has_no_training_or_plotting_stack():
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert not {p["name"] for p in lock["package"]} & FORBIDDEN_DEPENDENCIES
