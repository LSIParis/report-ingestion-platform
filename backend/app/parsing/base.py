from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ParseResult:
    status: str                                          # 'ok' | 'partial' | 'failed'
    rows: list[dict] = field(default_factory=list)       # brut, pré-normalisation
    errors: list[dict] = field(default_factory=list)     # [{code, message, row_index, field, severity}]
    metadata: dict = field(default_factory=dict)


class ReportAdapter(ABC):
    format: str

    @abstractmethod
    def parse(self, raw: bytes, profile) -> ParseResult: ...
