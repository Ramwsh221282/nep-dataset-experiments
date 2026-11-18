"""Preprocessing-only entry point; no model training or inference stages."""
import logging
import sys
from pathlib import Path

from .preprocess.config import Config as PreprocessConfig
from .preprocess.run import _parse_args, _setup_logger, run as preprocess_run

logger = logging.getLogger(__name__)


def run_pipeline(config_path: Path | str) -> None:
    """Extract code, generate FIM examples, tokenize and save dataset splits."""
    config = PreprocessConfig.load_from_yaml(Path(config_path))
    preprocess_run(config)


def main() -> None:
    args = _parse_args()
    _setup_logger(args.log_level)
    try:
        run_pipeline(config_path=args.config)
    except Exception:
        logger.exception("Preprocessing failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
