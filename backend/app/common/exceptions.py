import structlog
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

logger = structlog.get_logger()


class BuzzerError(Exception):
    """Base application error."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(BuzzerError):
    def __init__(self, message: str = "Resource not found"):
        super().__init__("NOT_FOUND", message, status.HTTP_404_NOT_FOUND)


class ForbiddenError(BuzzerError):
    def __init__(self, message: str = "Forbidden"):
        super().__init__("FORBIDDEN", message, status.HTTP_403_FORBIDDEN)


class UnauthorizedError(BuzzerError):
    def __init__(self, message: str = "Unauthorized"):
        super().__init__("UNAUTHORIZED", message, status.HTTP_401_UNAUTHORIZED)


class ConflictError(BuzzerError):
    def __init__(self, message: str = "Conflict"):
        super().__init__("CONFLICT", message, status.HTTP_409_CONFLICT)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BuzzerError)
    async def buzzer_error_handler(request: Request, exc: BuzzerError) -> JSONResponse:
        logger.warning(
            "app_error", code=exc.code, message=exc.message, path=str(request.url)
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.warning("validation_error", errors=exc.errors(), path=str(request.url))
        # exc.errors() can embed non-JSON-serializable objects (e.g. Pydantic v2 puts the
        # raw exception in a field-validator error's `ctx`), so this must go through
        # jsonable_encoder the same way FastAPI's own default handler does -- passing it
        # straight to JSONResponse crashes with a generic 500 that hides the real error.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "VALIDATION_ERROR",
                "detail": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(DBAPIError)
    async def db_error_handler(request: Request, exc: DBAPIError) -> JSONResponse:
        # T8 design §8: a deadlock or lock-wait timeout is a fallback, not the locking
        # strategy. The request's session has rolled back; the client may simply retry.
        if mysql_error_code(exc) in _RETRYABLE_MYSQL_ERRORS:
            logger.warning("db_busy", code=mysql_error_code(exc), path=str(request.url))
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"error": "BUSY", "message": "Busy, try again"},
            )
        return await unhandled_error_handler(request, exc)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_error", exc_info=exc, path=str(request.url))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
            },
        )


# MySQL ER_LOCK_DEADLOCK (1213) and ER_LOCK_WAIT_TIMEOUT (1205).
_RETRYABLE_MYSQL_ERRORS = (1213, 1205)


def mysql_error_code(exc: DBAPIError) -> int | None:
    """The MySQL error number behind a SQLAlchemy DB error, if the driver gave one."""
    args = getattr(exc.orig, "args", ())
    return args[0] if args and isinstance(args[0], int) else None
