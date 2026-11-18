"""Language detection and the deliberately conservative generation capabilities.

All supported languages have CST-based incremental construction. Python also
has narrowly restricted, intra-file AST-based rename and deletion trajectories;
this is not a general scope or inter-module resolver.
"""

from pathlib import PurePosixPath

EXTENSIONS = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".java": "java",
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
    ".go": "go", ".rs": "rust", ".cs": "csharp",
}

CAPABILITIES = {
    language: frozenset({"insert", "noop", *({"replace", "delete"} if language == "python" else set())})
    for language in set(EXTENSIONS.values())
}


def language_for_path(path: str) -> str | None:
    return EXTENSIONS.get(PurePosixPath(path).suffix.lower())
