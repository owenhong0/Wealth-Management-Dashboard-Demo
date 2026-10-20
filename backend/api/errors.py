"""Every error leaves the API in one shape:

    {"error": {"status": 400, "code": "bad_request", "message": "...", "details": {...}}}

`details` is only present when there is something useful to add, e.g. the
allowed values for a bad query parameter.
"""
from typing import Any

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

CODES = {
    400: "bad_request",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    500: "internal_error",
    503: "service_unavailable",
}


class APIError(Exception):
    def __init__(self, status: int, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details


class BadRequest(APIError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(400, message, details)


class NotFound(APIError):
    def __init__(self, message: str):
        super().__init__(404, message)


def error_response(status: int, message: str, details: dict[str, Any] | None = None):
    body: dict[str, Any] = {
        "status": status,
        "code": CODES.get(status, "error"),
        "message": message,
    }
    if details:
        body["details"] = details
    return jsonify(error=body), status


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(APIError)
    def _api_error(err: APIError):
        return error_response(err.status, err.message, err.details)

    @app.errorhandler(HTTPException)
    def _http_error(err: HTTPException):
        # Unknown routes, wrong methods, ids that fail an <int:...> converter.
        response, status = error_response(err.code or 500, err.description or err.name)
        if err.code == 405 and getattr(err, "valid_methods", None):
            response.headers["Allow"] = ", ".join(err.valid_methods)
        return response, status

    @app.errorhandler(Exception)
    def _unexpected(err: Exception):
        # Full traceback in the server log; nothing internal in the response.
        app.logger.exception("unhandled error")
        return error_response(500, "Internal server error")