from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("codefinetuner")
except PackageNotFoundError:
    __version__ = "unknown"

def __getattr__(name):
    # Keep NEP usable without importing FIM tokenization dependencies.
    from importlib import import_module

    if name == "run_pipeline":
        value = import_module(".pipeline", __name__).run_pipeline
    elif name in {"preprocess", "nep"}:
        value = import_module(f".{name}", __name__)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value

__all__ = [
    "__version__",
    "run_pipeline",
    "preprocess",

    "nep",
]