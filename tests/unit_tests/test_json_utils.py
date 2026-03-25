"""
Unit tests for JSON output utilities.

Tests the standardized JSON response formatting used across btcli commands.
"""

import json
import pytest
from io import StringIO
from unittest.mock import patch

from bittensor_cli.src.bittensor.json_utils import (
    json_response,
    json_success,
    json_error,
    serialize_balance,
    _transaction_response,
    TransactionResult,
    MultiTransactionResult,
)


class TestJsonResponse:
    """Tests for the json_response function."""

    def test_success_with_data(self):
        """Test successful response with data."""
        result = json_response(success=True, data={"key": "value"})
        parsed = json.loads(result)

        assert parsed["success"] is True
        assert parsed["data"] == {"key": "value"}
        assert "error" not in parsed

    def test_success_without_data(self):
        """Test successful response without data."""
        result = json_response(success=True)
        parsed = json.loads(result)

        assert parsed["success"] is True
        assert "data" not in parsed
        assert "error" not in parsed

    def test_error_response(self):
        """Test error response."""
        result = json_response(success=False, error="Something went wrong")
        parsed = json.loads(result)

        assert parsed["success"] is False
        assert parsed["error"] == "Something went wrong"

    def test_error_with_data(self):
        """Test error response with additional data."""
        result = json_response(
            success=False, data={"partial": "data"}, error="Partial failure"
        )
        parsed = json.loads(result)

        assert parsed["success"] is False
        assert parsed["data"] == {"partial": "data"}
        assert parsed["error"] == "Partial failure"

    def test_nested_data(self):
        """Test response with nested data structures."""
        data = {
            "wallet": {
                "name": "test",
                "hotkeys": ["hk1", "hk2"],
                "balance": {"rao": 1000000000, "tao": 1.0},
            }
        }
        result = json_response(success=True, data=data)
        parsed = json.loads(result)

        assert parsed["success"] is True
        assert parsed["data"]["wallet"]["name"] == "test"
        assert len(parsed["data"]["wallet"]["hotkeys"]) == 2


class TestJsonSuccess:
    """Tests for the json_success helper."""

    def test_simple_data(self):
        """Test success with simple data."""
        result = json_success({"status": "ok"})
        parsed = json.loads(result)

        assert parsed["success"] is True
        assert parsed["data"]["status"] == "ok"

    def test_list_data(self):
        """Test success with list data."""
        result = json_success([1, 2, 3])
        parsed = json.loads(result)

        assert parsed["success"] is True
        assert parsed["data"] == [1, 2, 3]

    def test_primitive_data(self):
        """Test success with primitive data."""
        result = json_success("simple string")
        parsed = json.loads(result)

        assert parsed["success"] is True
        assert parsed["data"] == "simple string"


class TestJsonError:
    """Tests for the json_error helper."""

    def test_simple_error(self):
        """Test simple error message."""
        result = json_error("Connection failed")
        parsed = json.loads(result)

        assert parsed["success"] is False
        assert parsed["error"] == "Connection failed"
        assert "data" not in parsed

    def test_error_with_context(self):
        """Test error with additional context data."""
        result = json_error("Validation failed", data={"field": "amount"})
        parsed = json.loads(result)

        assert parsed["success"] is False
        assert parsed["error"] == "Validation failed"
        assert parsed["data"]["field"] == "amount"


class TestTransactionResponse:
    """Tests for the transaction_response helper."""

    def test_successful_transaction(self):
        """Test successful transaction response."""
        result = _transaction_response(
            success=True,
            message="Transfer successful",
            extrinsic_identifier="12345678-2",
        )

        assert result["success"] is True
        assert result["message"] == "Transfer successful"
        assert result["extrinsic_identifier"] == "12345678-2"

    def test_failed_transaction(self):
        """Test failed transaction response."""
        result = _transaction_response(success=False, message="Insufficient balance")

        assert result["success"] is False
        assert result["message"] == "Insufficient balance"
        assert result["extrinsic_identifier"] is None

    def test_transaction_without_message(self):
        """Test transaction without message."""
        result = _transaction_response(success=True, extrinsic_identifier="12345678-3")

        assert result["success"] is True
        assert result["message"] is None
        assert result["extrinsic_identifier"] == "12345678-3"


class TestTransactionResult:
    """Tests for the TransactionResult class."""

    def test_as_dict(self):
        """Test conversion to dictionary."""
        result = TransactionResult(
            success=True, message="Success", extrinsic_identifier="12345-1"
        )
        d = result.as_dict()

        assert d["success"] is True
        assert d["message"] == "Success"
        assert d["extrinsic_identifier"] == "12345-1"

    def test_failed_result(self):
        """Test failed transaction result."""
        result = TransactionResult(success=False, message="Error occurred")
        d = result.as_dict()

        assert d["success"] is False
        assert d["message"] == "Error occurred"
        assert d["extrinsic_identifier"] is None


class TestMultiTransactionResult:
    """Tests for the MultiTransactionResult class."""

    def test_add_results(self):
        """Test adding multiple results."""
        multi = MultiTransactionResult()
        multi.add("pos1", True, "Success 1", "12345-1")
        multi.add("pos2", False, "Failed 2", None)

        d = multi.as_dict()

        assert "pos1" in d
        assert "pos2" in d
        assert d["pos1"]["success"] is True
        assert d["pos1"]["extrinsic_identifier"] == "12345-1"
        assert d["pos2"]["success"] is False
        assert d["pos2"]["extrinsic_identifier"] is None

    def test_add_result_object(self):
        """Test adding TransactionResult objects."""
        multi = MultiTransactionResult()
        result = TransactionResult(True, "Test", "123-1")
        multi.add_result("key1", result)

        d = multi.as_dict()
        assert d["key1"]["success"] is True
        assert d["key1"]["message"] == "Test"


class TestSerializeBalance:
    """Tests for balance serialization."""

    def test_balance_object(self):
        """Test serializing a Balance-like object."""

        class MockBalance:
            rao = 1000000000
            tao = 1.0

        result = serialize_balance(MockBalance())

        assert result["rao"] == 1000000000
        assert result["tao"] == 1.0

    def test_float_value(self):
        """Test serializing a float (assumes TAO)."""
        result = serialize_balance(2.5)

        assert result["tao"] == 2.5
        assert result["rao"] == 2500000000

    def test_int_value(self):
        """Test serializing an int (assumes RAO)."""
        result = serialize_balance(5000000000)

        assert result["rao"] == 5000000000
        assert result["tao"] == 5.0

    def test_unknown_type(self):
        """Test serializing unknown type returns zeros."""
        result = serialize_balance("invalid")

        assert result["rao"] == 0
        assert result["tao"] == 0.0


class TestJsonOutputConsistency:
    """Tests to verify JSON output schema consistency."""

    def test_success_always_has_success_field(self):
        """Verify success responses always include 'success' field."""
        responses = [
            json_success({}),
            json_success([]),
            json_success("test"),
            json_success(None),
        ]

        for response in responses:
            parsed = json.loads(response)
            assert "success" in parsed
            assert parsed["success"] is True

    def test_error_always_has_success_and_error_fields(self):
        """Verify error responses always include required fields."""
        responses = [
            json_error("error1"),
            json_error("error2", data={}),
        ]

        for response in responses:
            parsed = json.loads(response)
            assert "success" in parsed
            assert "error" in parsed
            assert parsed["success"] is False

    def test_transaction_response_schema(self):
        """Verify transaction responses have consistent schema."""
        success_result = _transaction_response(True, "OK", "123-1")
        fail_result = _transaction_response(False, "Failed", None)

        # Both should have all three keys
        for result in [success_result, fail_result]:
            assert "success" in result
            assert "message" in result
            assert "extrinsic_identifier" in result

    def test_json_is_valid(self):
        """Verify all outputs are valid JSON."""
        test_cases = [
            json_response(True, {"test": "data"}),
            json_success({"nested": {"deep": True}}),
            json_error("test error"),
        ]

        for output in test_cases:
            # Should not raise
            json.loads(output)
