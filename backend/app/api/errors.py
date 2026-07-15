from collections.abc import Callable
from typing import TypeVar

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError


T = TypeVar("T")


def with_db_error_handling(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=500, detail="Database error") from exc
