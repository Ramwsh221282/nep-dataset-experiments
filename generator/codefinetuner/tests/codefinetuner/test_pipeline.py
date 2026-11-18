import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

import codefinetuner
from codefinetuner.pipeline import _parse_args, main, run_pipeline


def test_parse_args(mocker):
    mocker.patch("sys.argv", ["codefinetuner", "--config", "test.yaml", "--log-level", "DEBUG"])
    args = _parse_args()
    assert args.config == Path("test.yaml")
    assert args.log_level == "DEBUG"


def test_parse_args_requires_config(mocker):
    mocker.patch("sys.argv", ["codefinetuner"])
    with pytest.raises(SystemExit):
        _parse_args()


@pytest.mark.parametrize("flag", ["--skip-preprocess", "--skip-finetune", "--skip-evaluate", "--skip-convert"])
def test_removed_pipeline_flags_are_rejected(mocker, flag):
    mocker.patch("sys.argv", ["codefinetuner", "--config", "test.yaml", flag])
    with pytest.raises(SystemExit):
        _parse_args()


def test_run_pipeline_only_preprocesses(mocker):
    config = mocker.patch("codefinetuner.pipeline.PreprocessConfig.load_from_yaml")
    run = mocker.patch("codefinetuner.pipeline.preprocess_run")
    run_pipeline("test.yaml")
    config.assert_called_once_with(Path("test.yaml"))
    run.assert_called_once_with(config.return_value)
    assert codefinetuner.run_pipeline is run_pipeline


def test_main_success(mocker):
    mocker.patch("codefinetuner.pipeline._parse_args", return_value=SimpleNamespace(config=Path("a.yaml"), log_level="INFO"))
    setup = mocker.patch("codefinetuner.pipeline._setup_logger")
    run = mocker.patch("codefinetuner.pipeline.run_pipeline")
    main()
    setup.assert_called_once_with("INFO")
    run.assert_called_once_with(config_path=Path("a.yaml"))


def test_main_failure(mocker):
    mocker.patch("codefinetuner.pipeline._parse_args", return_value=SimpleNamespace(config=Path("a.yaml"), log_level="INFO"))
    mocker.patch("codefinetuner.pipeline._setup_logger")
    mocker.patch("codefinetuner.pipeline.run_pipeline", side_effect=ValueError("invalid config"))
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


@pytest.mark.parametrize("name", ["finetune", "evaluate", "convert", "tracking"])
def test_removed_modules_are_not_exposed(name):
    assert name not in codefinetuner.__all__
    assert importlib.util.find_spec(f"codefinetuner.{name}") is None
    with pytest.raises(AttributeError):
        getattr(codefinetuner, name)
