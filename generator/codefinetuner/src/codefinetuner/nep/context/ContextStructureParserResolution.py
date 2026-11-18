"""Extension-based strategy factory. Unknown formats never return raw source."""
from functools import lru_cache
from importlib import import_module
from pathlib import PurePosixPath

from .ContextStructureParser import ContextStructureParser
from .ContextStructureParserAlgorithm import ContextParseError


class ContextStructureParserResolution:
    STRATEGIES = {
        ".rs": "Rust", ".py": "Python", ".pyi": "Python",
        ".js": "Javascript", ".mjs": "Javascript", ".cjs": "Javascript",
        ".jsx": "JSX", ".ts": "Typescript", ".mts": "Typescript", ".cts": "Typescript", ".tsx": "TSX",
        ".cs": "Csharp", ".zig": "Zig", ".c": "C", ".h": "C",
        ".cpp": "Cpp", ".cc": "Cpp", ".cxx": "Cpp", ".hpp": "Cpp", ".hh": "Cpp", ".hxx": "Cpp",
        ".java": "Java", ".go": "Go",
        ".html": "HTML", ".htm": "HTML", ".xml": "XML", ".svg": "XML",
        ".md": "Markdown", ".markdown": "Markdown",
        ".json": "JSON", ".jsonc": "JSONC", ".json5": "JSON5", ".jsonl": "JSONL", ".ndjson": "JSONL",
        ".toml": "TOML",
    }

    @classmethod
    def supports(cls, path: str) -> bool:
        return PurePosixPath(path).suffix.lower() in cls.STRATEGIES

    @classmethod
    def resolve(cls, path: str) -> ContextStructureParser:
        name = cls.STRATEGIES.get(PurePosixPath(path).suffix.lower())
        if name is None:
            raise ContextParseError(f"Unsupported context extension: {path}")
        return cls._parser(name)

    @staticmethod
    @lru_cache(maxsize=32)
    def _parser(name: str) -> ContextStructureParser:
        name += "ContextStructureParser"
        module = import_module(f".{name}", __package__)
        return ContextStructureParser(getattr(module, name)())
