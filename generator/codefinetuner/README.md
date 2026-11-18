# CodeFinetuner dataset generators

This fork prepares code datasets; it does not fine-tune, evaluate, or convert models.
Only dataset-generation runtime code is retained: extraction, tokenization,
validation and saving. Training/evaluation/conversion modules, model utilities,
experiment tracking and plotting code are physically removed. AST/debugging and
dataset-decoding helpers remain for dataset preparation.

It contains two independent generators:

- **FIM preprocessing** extracts tree-sitter AST blocks, creates fill-in-the-middle examples and tokenizes them. Optional random FIM examples and automatic or manual dataset splits are supported.
- **Next Edit Prediction (NEP)** synthesizes incremental edit histories and emits raw SPM marker-range examples with replayable audit records. It does not require a tokenizer or model.

## Install

From this directory, with Python 3.12 and [uv](https://docs.astral.sh/uv/):

```sh
uv sync --group dev
```

For the lightweight NEP-only source-tree environment, use `uv venv .venv-nep --python 3.12` and `uv pip install --python .venv-nep/bin/python -r requirements-nep.txt` instead; prefix the NEP commands below with `PYTHONPATH=src .venv-nep/bin/python -m` in place of `uv run python -m`.

## FIM preprocessing

Edit [`config/codefinetuner_config.yaml`](config/codefinetuner_config.yaml), including language, extensions, and `model_name` (a tokenizer repository ID or local tokenizer path). The tokenizer and configured special tokens must match; a remote tokenizer can require network access. No model weights are needed.

Place source files under `data/`, or for manual splitting under `data/train/`, `data/eval/`, and `data/test/`, and run:

```sh
uv run codefinetuner --config config/codefinetuner_config.yaml
```

Results include `outputs/preprocess/results/datasets/{train,eval,test}_dataset.jsonl` and `outputs/preprocess/results/split_log.jsonl`. Existing dataset files are replaced on each run. See the [configuration reference](docs/configuration/config-file.md) and [tree-sitter customization](docs/tree-sitter-customization/tree-sitter-customization.md).

## NEP

```sh
uv run python -m codefinetuner.nep /absolute/path/to/source-repository outputs/nep-run \
  --repository-id organization/project --split train
```

Use a new or empty output directory and assign all revisions and variants of one repository to the same split. The outputs are `<split>.jsonl` (`id`, `input`, `output`), `audit.jsonl` (replayable source and edit history), and `report.json`. Audit records contain source code and require the same access controls as the source repository.

This is a conservative synthetic-data MVP: semantic rename/deletion support is restricted, not a general refactoring engine. See the [design and limitations](../../docs/nep-generator-design.md) and `uv run python -m codefinetuner.nep --help`.

### Structured context

NEP context files are now parsed before prompt assembly: language-specific
strategies extract signatures, fields and declarations; JSX/TSX also retain
component declarations and markup. Document strategies handle Markdown, HTML,
XML, JSON/JSONC/JSON5/JSONL and TOML. `--max-context-chars` controls the processed
context budget (default 6000). `audit.jsonl` includes the structured `input_blocks`
and parsing diagnostics; the training JSONL format remains `id/input/output`.

See [context parser architecture and limitations](docs/context-parsers.md).
The exact editable source is not compressed. Context is discovered from the cursor
and completed edit history using a snapshot-local Tree-sitter index and versioned
[SCM queries](src/codefinetuner/nep/scms/), without an LSP. The retriever uses
scopes, direct imports, known receiver types and explicit local document links;
it skips ambiguous bindings instead of including every declaration with the same name.

## Container

```sh
docker build -f Dockerfile.cpu -t codefinetuner:cpu .
docker run --rm codefinetuner:cpu --help
```

The image defaults to the NEP CLI; mount source and output directories when generating data. Override the entrypoint to run FIM preprocessing.

## Tests

```sh
uv run pytest
```

Preprocess tests use the included local tokenizer fixture; model weights and network access are not required. NEP tests can also run in the lightweight environment as documented in the design guide.

The `codefinetuner` command and `codefinetuner.run_pipeline(config_path)` now run
preprocessing only. The former `--skip-*` flags and Python `skip_*` arguments
have been removed, along with model-training, evaluation, conversion and MLflow dependencies.

Licensed under the [Apache License 2.0](LICENSE.txt).
