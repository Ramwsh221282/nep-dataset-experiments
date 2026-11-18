"""Immutable base facts plus bounded, per-repository caches of synthetic revisions."""
from collections.abc import Mapping
from functools import cached_property, lru_cache
from pathlib import PurePosixPath
from types import MappingProxyType

from .code_facts import CODE_LANGUAGES, extract_code
from .document_links import DocumentFacts, analyze_document
from .index_models import FileFacts
from .packages import build_packages, package_keys


def is_code(path):
    return PurePosixPath(path).suffix.lower() in CODE_LANGUAGES


def _index_file(path, text):
    if is_code(path):
        try:
            return extract_code(path, text)
        except (UnicodeError, RecursionError):
            return FileFacts(partial=True)
    return analyze_document(path, text)


class RepositorySymbolIndex:
    """Parse each base file once. A view can only query paths in its own snapshot."""

    def __init__(self, files):
        self.sources = MappingProxyType(dict(files))
        self.base = {path: _index_file(path, text) for path, text in self.sources.items()}
        self.package_index = build_packages(self.sources, self.base)
        self._revision = lru_cache(maxsize=128)(_index_file)

    def revision(self, path, text):
        if path in self.sources and self.sources[path] == text:
            return self.base[path]
        return self._revision(path, text)

    def view(self, files):
        return SnapshotView(self, files)


class WitnessFiles(Mapping):
    """Record positive lookup dependencies, including ambiguous module candidates."""

    def __init__(self, state):
        self.state = state
        self.witnesses = set()

    def __getitem__(self, path):
        value = self.state[path]
        self.witnesses.add(path)
        return value

    def __len__(self):
        return len(self.state)

    def __iter__(self):
        self.witnesses.update(self.state)
        return iter(self.state)


class SnapshotView:
    def __init__(self, index, files):
        self.index = index
        self._state = MappingProxyType(dict(files))
        self.files = WitnessFiles(self._state)

    @cached_property
    def package_changes(self):
        # Built only for snapshots needing package-based resolution. Never scan
        # workspace files for every reference or reuse a base namespace after a move.
        index = self.index
        removed, added = {}, {}
        for path in index.sources.keys() | self._state.keys():
            if self._state.get(path) == index.sources.get(path):
                continue
            for key in package_keys(path, index.base.get(path), index.sources):
                removed.setdefault(key, set()).add(path)
            if path in self._state:
                for key in package_keys(path, index.revision(path, self._state[path]), self._state):
                    added.setdefault(key, set()).add(path)
        return removed, added

    def packages(self, module, language):
        key = (language, module)
        removed, added = self.package_changes
        paths = set(self.index.package_index.get(key, ())) - removed.get(key, set())
        result = tuple(sorted(paths | added.get(key, set())))
        self.files.witnesses.update(result)
        return result

    def code(self, path):
        facts = self.index.revision(path, self.files[path])
        return facts if isinstance(facts, FileFacts) else FileFacts()

    def document(self, path):
        facts = self.index.revision(path, self.files[path])
        return facts if isinstance(facts, DocumentFacts) else DocumentFacts()
