"""Snapshot package membership; paths are root-relative POSIX paths."""
import posixpath
from pathlib import PurePosixPath

from .code_facts import CODE_LANGUAGES
from .index_models import FileFacts


def package_keys(path, facts, files):
    """Keys contributed by one current file, suitable for overlay add/remove."""
    if path not in files or not isinstance(facts, FileFacts) or not facts.package:
        return ()
    language = CODE_LANGUAGES.get(PurePosixPath(path).suffix.lower())
    if language == 'go':
        return ((language, posixpath.dirname(path)),)
    if language in ('java', 'csharp'):
        return ((language, facts.package),)
    return ()


def build_packages(files, facts):
    """Build the base (language, module) -> sorted paths index once per repository."""
    result = {}
    for path in files:
        for key in package_keys(path, facts.get(path), files):
            result.setdefault(key, []).append(path)
    return {key: tuple(sorted(paths)) for key, paths in result.items()}
