"""Domain errors mapped to HTTP status codes by the API layer."""

from __future__ import annotations


class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    status_code = 409


class InvalidRequestError(DomainError):
    status_code = 422


class ServiceUnavailableError(DomainError):
    status_code = 503


class PreconditionRequiredError(DomainError):
    """428: a mandatory strong If-Match header was missing or empty."""

    status_code = 428
    error_code = "precondition_required"


class PreconditionFailedError(DomainError):
    """412: a supplied If-Match no longer matches the resource's current strong ETag."""

    status_code = 412
    error_code = "precondition_failed"


class OpalValidationError(DomainError):
    """422 rendered as the OPAL standard error envelope rather than HTTPValidationError."""

    status_code = 422
    error_code = "invalid_request"
