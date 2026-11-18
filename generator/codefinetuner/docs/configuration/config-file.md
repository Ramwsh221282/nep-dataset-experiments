# FIM preprocessing configuration

Set parameters under `preprocess` in [`config/codefinetuner_config.yaml`](../../config/codefinetuner_config.yaml). NEP has its own CLI options and does not use this file.

## Required parameters

| Parameter | Description |
|-----------|-------------|
| `model_name` | Hugging Face tokenizer repository ID or local tokenizer directory. Only tokenizer files are needed. |
| `fim_prefix_token`, `fim_middle_token`, `fim_suffix_token`, `fim_pad_token`, `eos_token` | Special tokens present in the selected tokenizer. |
| `max_token_sequence_length` | Maximum token sequence length. |
| `data_language` | Tree-sitter language identifier in the language definitions. |
| `data_extensions` | Source file extensions to include. |

## Optional parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `workspace_path` | `null` | Workspace directory; defaults to current working directory. |
| `raw_data_path` | `null` | Source directory; defaults to `<workspace_path>/data`. |
| `split_mode` | `auto` | `auto` or `manual`; manual expects `train`, `eval`, and `test` subdirectories. |
| `train_ratio`, `eval_ratio`, `test_ratio` | `0.8`, `0.1`, `0.1` | Auto split ratios; must sum to 1. |
| `max_code_blocks_ast_depth` | `4` | AST depth limit for block extraction; root has depth 1. |
| `min_middle_tokens_length`, `max_middle_tokens_length` | `8`, `128` | FIM middle length bounds; AST extraction estimates lengths from bytes. |
| `fim_examples_per_subblock_ratio` | `0.5` | Fraction of available AST FIM examples to select. |
| `rand_to_ast_fim_examples_ratio` | `0.0` | Ratio of random to AST FIM examples. |
| `rand_examples_min_prefix_suffix_tokens_length` | `8` | Minimum prefix and suffix length for random FIM examples. |
| `rand_examples_per_chunk` | `10` | Candidate random examples per chunk. |
| `tokenizer_batch_size` | `32` | Tokenization batch size. |
| `tree_sitter_parser_path` | `null` | Custom shared parser library; defaults to the language pack. |
| `tree_sitter_definitions_path` | `null` | Custom block definitions JSON; defaults to bundled definitions. |
| `rng_seed` | `0` | Random seed for splitting and generation. |

The example configuration may choose values different from these defaults. FIM preprocessing overwrites its existing output datasets when run again.
