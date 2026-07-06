from typing import Any

from fastapi import Query
from pydantic import BaseModel


class Page(BaseModel):
    items: list[Any]
    total: int
    page: int
    size: int


def paginate(query, page: int, size: int) -> Page:
    total = query.order_by(None).count()
    items = query.limit(size).offset((page - 1) * size).all()
    return Page(items=items, total=total, page=page, size=size)


def page_params(page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)):
    return page, size
