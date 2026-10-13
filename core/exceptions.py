from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler


class CustomAPIException(APIException):
    def __init__(self, code: str, message: str, status_code: int = status.HTTP_400_BAD_REQUEST, details: list = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []
        super().__init__(detail=message)


class DuplicateError(CustomAPIException):
    def __init__(self, message: str = "Resource already exists", details: list = None):
        super().__init__(code="DUPLICATE", message=message, status_code=status.HTTP_409_CONFLICT, details=details)


class InsufficientStockError(CustomAPIException):
    def __init__(self, message: str, details: list = None):
        super().__init__(code="INSUFFICIENT_STOCK", message=message, status_code=status.HTTP_409_CONFLICT, details=details)


class InvalidTransitionError(CustomAPIException):
    def __init__(self, message: str = "Invalid status transition", details: list = None):
        super().__init__(code="INVALID_TRANSITION", message=message, status_code=status.HTTP_409_CONFLICT, details=details)


class OrderLockedError(CustomAPIException):
    def __init__(self, message: str = "Cannot edit order that is not in DRAFT status", details: list = None):
        super().__init__(code="ORDER_LOCKED", message=message, status_code=status.HTTP_409_CONFLICT, details=details)


class IdempotencyConflictError(CustomAPIException):
    def __init__(self, message: str = "Idempotency key reused with different request payload", details: list = None):
        super().__init__(code="IDEMPOTENCY_CONFLICT", message=message, status_code=status.HTTP_409_CONFLICT, details=details)


class SupplierUnavailableError(CustomAPIException):
    def __init__(self, message: str = "Supplier feed unavailable", details: list = None):
        super().__init__(code="SUPPLIER_UNAVAILABLE", message=message, status_code=status.HTTP_502_BAD_GATEWAY, details=details)


def custom_exception_handler(exc, context):
    """
    Transform all exceptions to exact contract shape:
    {"error": {"code": "...", "message": "...", "details": []}}
    """
    if isinstance(exc, CustomAPIException):
        return Response(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details
                }
            },
            status=exc.status_code
        )

    response = exception_handler(exc, context)

    if response is not None:
        details = []
        code = "VALIDATION_ERROR"
        message = "Validation error"

        if response.status_code == status.HTTP_404_NOT_FOUND or isinstance(exc, NotFound):
            code = "NOT_FOUND"
            message = "Resource not found"
        elif response.status_code == status.HTTP_409_CONFLICT:
            code = "DUPLICATE"
            message = "Conflict"
        elif isinstance(exc, ValidationError):
            code = "VALIDATION_ERROR"
            # Format validation errors into details list and message
            if isinstance(response.data, dict):
                first_msg = None
                for field, err in response.data.items():
                    err_text = err[0] if isinstance(err, list) and len(err) > 0 else str(err)
                    details.append(f"{field}: {err_text}")
                    if first_msg is None:
                        first_msg = f"{field}: {err_text}"
                message = first_msg or "Validation error"
            elif isinstance(response.data, list):
                details = [str(e) for e in response.data]
                message = details[0] if details else "Validation error"
            else:
                message = str(response.data)
        else:
            if isinstance(response.data, dict) and "detail" in response.data:
                message = str(response.data["detail"])
            else:
                message = str(response.data)

        response.data = {
            "error": {
                "code": code,
                "message": message,
                "details": details
            }
        }

    return response
