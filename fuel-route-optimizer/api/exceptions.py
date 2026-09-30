"""Uniform JSON error responses.

Every error returned by the API has the same shape:

    {"error": {"code": "location_not_found", "message": "...", "details": {...}}}
"""

import logging

from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

from .services.exceptions import FuelOptimizerError

logger = logging.getLogger(__name__)


def error_payload(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def api_exception_handler(exc, context):
    if isinstance(exc, FuelOptimizerError):
        if exc.status_code >= 500:
            logger.warning("%s: %s %s", exc.code, exc.message, exc.details)
        return Response(error_payload(exc.code, exc.message, exc.details), status=exc.status_code)

    if isinstance(exc, ValidationError):
        return Response(
            error_payload("invalid_request", "One or more request parameters are invalid.", exc.detail),
            status=status.HTTP_400_BAD_REQUEST,
        )

    response = exception_handler(exc, context)
    if response is not None:
        code = exc.default_code if isinstance(exc, APIException) else "error"
        message = response.data.get("detail", str(exc)) if isinstance(response.data, dict) else str(exc)
        response.data = error_payload(str(code), str(message))
        return response

    # Unexpected error: log the traceback, never leak it to clients.
    logger.exception("Unhandled error while planning a route")
    if settings.DEBUG:
        return None  # let Django render its debug page
    return Response(
        error_payload("internal_error", "An unexpected error occurred."),
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
