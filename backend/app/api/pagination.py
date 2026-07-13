from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Page générique. Le paramètre de type est OBLIGATOIRE côté route
    (`response_model=Page[ReportOut]`) : avec un `items: list[Any]`, Pydantic v2 ne
    convertit pas les objets SQLAlchemy et lève « Unable to serialize unknown type »
    à la sérialisation — toutes les routes paginées renvoient alors un 500."""

    items: list[T]
    total: int
    page: int
    size: int


def paginate(query, page: int, size: int) -> dict:
    total = query.order_by(None).count()
    items = query.limit(size).offset((page - 1) * size).all()
    # dict : FastAPI valide contre le response_model Page[X] de la route, qui sait
    # convertir les objets ORM (from_attributes).
    return {"items": items, "total": total, "page": page, "size": size}


def page_params(page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)):
    return page, size
