"""Shared, model-free context extraction contract."""
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass


class ContextParseError(ValueError):
    """Unsupported or unsafe context must never silently become a full file."""


@dataclass(frozen=True)
class ContextFragment:
    path: str
    format: str
    kind: str
    code_text: str
    selector: str = ""
    representation: str = "extracted"
    start_byte: int = 0
    end_byte: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class ContextStructureParserAlgorithm(ABC):
    @abstractmethod
    def parse(self, path: str, source_text: str) -> list[ContextFragment]:
        """Extract structured candidates, retaining original UTF-8 provenance.

        Implementations raise ContextParseError on malformed/unsupported input.
        No execution, model imports, network access or full-source fallback.
        Selection and budgets are handled by ContextStructureParser.
        """
        raise NotImplementedError
