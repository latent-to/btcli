"""
Standardized JSON output utilities for btcli.

This module provides consistent JSON response formatting across all btcli commands.
All JSON outputs should use these utilities to ensure schema compliance.

Standard Transaction Response Format:
{
    "success": bool,                    # Required: Whether the operation succeeded
    "message": str,                     # Optional: Human-readable message
    "extrinsic_identifier": str | None  # Optional: Block-extrinsic ID (e.g., "12345-2")
}

Standard Data Response Format:
{
    "success": bool,                    # Required: Whether the operation succeeded
    "data": list | dict | None,         # Optional: Command-specific response data
    "error": str | None                 # Optional: Error message if success=False
}
"""

import json
from typing import Any, Optional, Union
from rich.console import Console
from bittensor_cli.src.bittensor.balances import Balance

json_console = Console(
    markup=False, highlight=False, force_terminal=False, no_color=True
)


def _transaction_response(
    success: bool,
    message: Optional[str] = None,
    extrinsic_identifier: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create a standardized transaction response dictionary.

    Args:
        success: Whether the transaction succeeded
        message: Human-readable status message
        extrinsic_identifier: The extrinsic ID (e.g., "12345678-2")

    Returns:
        Dictionary with standardized transaction format
    """
    return {
        "success": success,
        "message": message,
        "extrinsic_identifier": extrinsic_identifier,
    }


# TODO remove
def print_transaction_response(
    success: bool,
    message: Optional[str] = None,
    extrinsic_identifier: Optional[str] = None,
) -> None:
    """
    Print a standardized transaction response as JSON.

    Args:
        success: Whether the transaction succeeded
        message: Human-readable status message
        extrinsic_identifier: The extrinsic ID (e.g., "12345678-2")
    """
    json_console.print_json(
        data=_transaction_response(success, message, extrinsic_identifier)
    )


class TransactionResult:
    """
    Helper class for building transaction responses.

    Provides a clean interface for transaction commands that need to
    build up response data before printing.
    """

    def __init__(
        self,
        success: bool,
        message: Optional[str] = None,
        extrinsic_identifier: Optional[str] = None,
    ):
        self.success = success
        self.message = message
        self.extrinsic_identifier = extrinsic_identifier

    def as_dict(self) -> dict[str, Any]:
        """Return the response as a dictionary."""
        return _transaction_response(
            self.success,
            self.message,
            self.extrinsic_identifier,
        )

    def print(self) -> None:
        """Print the response as JSON."""
        json_console.print_json(data=self.as_dict())


class MultiTransactionResult:
    """
    Helper class for commands that process multiple transactions.

    Builds a keyed dictionary of transaction results.
    """

    def __init__(self):
        self._results: dict[str, TransactionResult] = {}

    def add(
        self,
        key: str,
        success: bool,
        message: Optional[str] = None,
        extrinsic_identifier: Optional[str] = None,
    ) -> None:
        """Add a transaction result with the given key."""
        self._results[key] = TransactionResult(success, message, extrinsic_identifier)

    def add_result(self, key: str, result: TransactionResult) -> None:
        """Add an existing TransactionResult with the given key."""
        self._results[key] = result

    def as_dict(self) -> dict[str, dict[str, Any]]:
        """Return all results as a dictionary."""
        return {k: v.as_dict() for k, v in self._results.items()}

    def print(self) -> None:
        """Print all results as JSON."""
        json_console.print_json(data=self.as_dict())


def print_json_response(
    success: bool,
    *,
    data: Any = None,
    error: Optional[str] = None,
) -> None:
    """
    Prints a standardized JSON response string for non-trnasaction data.
    """
    _data = {"success": success, "data": data, "error": error}
    json_console.print_json(data=_data)


# TODO make private also what the fuck is the point?
def json_response(
    success: bool,
    data: Optional[Any] = None,
    error: Optional[str] = None,
) -> str:
    """
    Create a standardized JSON response string for data queries.

    Args:
        success: Whether the operation succeeded
        data: Optional response data (dict, list, or primitive)
        error: Optional error message (typically used when success=False)

    Returns:
        JSON string with standardized format

    Examples:
        >>> json_response(True, {"balance": 100.5})
        '{"success": true, "data": {"balance": 100.5}}'

        >>> json_response(False, error="Wallet not found")
        '{"success": false, "error": "Wallet not found"}'
    """
    response: dict[str, Any] = {"success": success}

    if data is not None:
        response["data"] = data

    if error is not None:
        response["error"] = error

    return json.dumps(response)


# TODO remove
def json_success(data: Any) -> str:
    """
    Create a successful JSON response string.

    Args:
        data: Response data to include

    Returns:
        JSON string with success=True and the provided data
    """
    return json_response(success=True, data=data)


# TODO remove
def json_error(error: str, data: Optional[Any] = None) -> str:
    """
    Create an error JSON response string.

    Args:
        error: Error message describing what went wrong
        data: Optional additional context data

    Returns:
        JSON string with success=False and error message
    """
    return json_response(success=False, data=data, error=error)


# TODO remove
def print_json_error(error: str, data: Optional[Any] = None) -> None:
    """
    Print an error JSON response.

    Args:
        error: Error message
        data: Optional additional context
    """
    print_json(json_error(error, data))


# TODO remove
def print_json_data(data: Any) -> None:
    """
    Print data directly as JSON (for simple data responses).

    Args:
        data: Data to print as JSON
    """
    json_console.print_json(data=data)


# TODO remove
def print_transaction_with_data(
    success: bool,
    message: Optional[str] = None,
    extrinsic_identifier: Optional[str] = None,
    **extra_data: Any,
) -> None:
    """
    Print a transaction response with additional data fields.

    Args:
        success: Whether the transaction succeeded
        message: Human-readable status message
        extrinsic_identifier: The extrinsic ID (e.g., "12345678-2")
        **extra_data: Additional fields to include in the response
    """
    response = {
        "success": success,
        "message": message,
        "extrinsic_identifier": extrinsic_identifier,
        **extra_data,
    }
    json_console.print_json(data=response)


def serialize_balance(balance: Balance | int | float) -> dict[str, Union[int, float]]:
    """
    Serialize a Balance object to a consistent dictionary format.

    Args:
        balance: A Balance object or numeric value

    Returns:
        Dictionary with 'rao' (int) and 'tao' (float) keys
    """
    if isinstance(balance, Balance):
        return {"rao": int(balance.rao), "tao": float(balance.tao)}
    # Assume it's already in tao if float, rao if int
    elif isinstance(balance, float):
        return {"rao": int(balance * 1e9), "tao": balance}
    elif isinstance(balance, int):
        return {"rao": balance, "tao": balance / 1e9}
    else:
        raise TypeError(f"Unsupported type {type(balance)}")
