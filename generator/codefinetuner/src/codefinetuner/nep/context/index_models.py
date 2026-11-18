"""Snapshot-local facts. All ranges are half-open UTF-8 byte intervals."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Definition:
    path: str
    name: str
    kind: str
    start: int
    end: int
    scope: tuple[str, ...] = ()
    owner: str = ''
    type_name: str = ''
    name_start: int = -1
    name_end: int = -1
    direct_export: bool = False
    visibility: str = ''


@dataclass(frozen=True)
class Reference:
    name: str
    start: int
    end: int
    scope: tuple[str, ...] = ()
    receiver: str = ''


@dataclass(frozen=True)
class Import:
    local: str
    module: str
    remote: str = ''
    scope: tuple[str, ...] = ()
    explicit_alias: bool = False


@dataclass(frozen=True)
class FileFacts:
    definitions: tuple[Definition, ...] = ()
    references: tuple[Reference, ...] = ()
    imports: tuple[Import, ...] = ()
    partial: bool = False
    package: str = ''
