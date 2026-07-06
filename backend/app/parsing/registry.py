from app.parsing.base import ReportAdapter

_REGISTRY: dict[str, type[ReportAdapter]] = {}


def register(fmt: str):
    def deco(cls: type[ReportAdapter]):
        _REGISTRY[fmt] = cls
        return cls
    return deco


def get_adapter(fmt: str) -> ReportAdapter:
    if fmt not in _REGISTRY:
        raise LookupError(f"Aucun adaptateur pour le format '{fmt}'")
    return _REGISTRY[fmt]()
