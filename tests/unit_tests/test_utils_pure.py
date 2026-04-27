"""
Unit tests for pure (side-effect-free) functions in:
  - bittensor_cli/src/bittensor/networking.py  (int_to_ip)
  - bittensor_cli/src/bittensor/utils.py       (validation & conversion functions)

All tests are synchronous and require no mocks.
"""

import pytest
import typer

from bittensor_cli.src.bittensor.networking import int_to_ip
from bittensor_cli.src.bittensor.utils import (
    u16_normalized_float,
    u64_normalized_float,
    float_to_u16,
    float_to_u64,
    u16_to_float,
    u64_to_float,
    validate_chain_endpoint,
    is_valid_ss58_address,
    is_valid_ed25519_pubkey,
    is_valid_bittensor_address_or_public_key,
    format_error_message,
    validate_netuid,
)
from .conftest import COLDKEY_SS58 as _VALID_SS58


# ---------------------------------------------------------------------------
# int_to_ip
# ---------------------------------------------------------------------------


class TestIntToIp:
    def test_zero_is_all_zeros(self):
        assert int_to_ip(0) == "0.0.0.0"

    def test_loopback_ipv4(self):
        assert int_to_ip(2130706433) == "127.0.0.1"

    def test_broadcast(self):
        assert int_to_ip(4294967295) == "255.255.255.255"

    def test_ipv6_loopback(self):
        # ::1 maps to integer 1 for IPv6
        result = int_to_ip(1)
        # netaddr returns IPv6 for 1 (>32-bit threshold)
        assert ":" in result or result == "0.0.0.1"

    def test_round_trip_with_ip_to_int(self):
        """int_to_ip should be the inverse of netaddr.IPAddress(str) -> int."""
        from bittensor_cli.src.bittensor.extrinsics.serving import ip_to_int

        ip = "192.168.1.100"
        assert int_to_ip(ip_to_int(ip)) == ip


# ---------------------------------------------------------------------------
# u16_normalized_float / u64_normalized_float
# ---------------------------------------------------------------------------


class TestNormalizedFloat:
    def test_u16_zero(self):
        assert u16_normalized_float(0) == pytest.approx(0.0)

    def test_u16_max(self):
        assert u16_normalized_float(65535) == pytest.approx(1.0)

    def test_u16_midpoint(self):
        result = u16_normalized_float(65535 // 2)
        assert 0.0 < result < 1.0

    def test_u64_zero(self):
        assert u64_normalized_float(0) == pytest.approx(0.0)

    def test_u64_max(self):
        assert u64_normalized_float(2**64 - 1) == pytest.approx(1.0)

    def test_u64_midpoint(self):
        result = u64_normalized_float((2**64 - 1) // 2)
        assert 0.0 < result < 1.0


# ---------------------------------------------------------------------------
# float_to_u16 / u16_to_float
# ---------------------------------------------------------------------------


class TestU16Conversion:
    def test_float_to_u16_zero(self):
        assert float_to_u16(0.0) == 0

    def test_float_to_u16_one(self):
        assert float_to_u16(1.0) == 65535

    def test_float_to_u16_half(self):
        result = float_to_u16(0.5)
        assert 32000 < result < 33000

    def test_float_to_u16_out_of_range_high(self):
        with pytest.raises(ValueError):
            float_to_u16(1.1)

    def test_float_to_u16_out_of_range_low(self):
        with pytest.raises(ValueError):
            float_to_u16(-0.1)

    def test_u16_to_float_zero(self):
        assert u16_to_float(0) == pytest.approx(0.0)

    def test_u16_to_float_max(self):
        assert u16_to_float(65535) == pytest.approx(1.0)

    def test_u16_to_float_out_of_range(self):
        with pytest.raises(ValueError):
            u16_to_float(65536)

    def test_round_trip(self):
        value = 0.75
        assert u16_to_float(float_to_u16(value)) == pytest.approx(value, rel=1e-4)


# ---------------------------------------------------------------------------
# float_to_u64 / u64_to_float
# ---------------------------------------------------------------------------


class TestU64Conversion:
    def test_float_to_u64_zero(self):
        assert float_to_u64(0.0) == 0

    def test_float_to_u64_one(self):
        # float precision: 1.0 * (2**64-1) rounds up to 2**64 in float arithmetic
        result = float_to_u64(1.0)
        assert result >= 2**64 - 1

    def test_float_to_u64_out_of_range_high(self):
        with pytest.raises(ValueError):
            float_to_u64(1.1)

    def test_float_to_u64_out_of_range_low(self):
        with pytest.raises(ValueError):
            float_to_u64(-0.1)

    def test_u64_to_float_zero(self):
        assert u64_to_float(0) == pytest.approx(0.0)

    def test_u64_to_float_max(self):
        assert u64_to_float(2**64 - 1) == pytest.approx(1.0)

    def test_u64_to_float_out_of_range(self):
        with pytest.raises(ValueError):
            u64_to_float(2**64 + 10)

    def test_round_trip(self):
        value = 0.6
        result = u64_to_float(float_to_u64(value))
        assert result == pytest.approx(value, rel=1e-9)


# ---------------------------------------------------------------------------
# validate_chain_endpoint
# ---------------------------------------------------------------------------


class TestValidateChainEndpoint:
    def test_ws_localhost_valid(self):
        ok, msg = validate_chain_endpoint("ws://localhost:9944")
        assert ok is True
        assert msg == ""

    def test_wss_valid(self):
        ok, msg = validate_chain_endpoint("wss://finney.opentensor.ai:443")
        assert ok is True

    def test_http_invalid(self):
        ok, msg = validate_chain_endpoint("http://localhost:9944")
        assert ok is False
        assert len(msg) > 0

    def test_https_invalid(self):
        ok, msg = validate_chain_endpoint("https://example.com")
        assert ok is False

    def test_no_scheme_invalid(self):
        ok, msg = validate_chain_endpoint("localhost:9944")
        assert ok is False

    def test_missing_netloc_invalid(self):
        ok, msg = validate_chain_endpoint("ws://")
        assert ok is False


# ---------------------------------------------------------------------------
# is_valid_ss58_address
# ---------------------------------------------------------------------------


class TestIsValidSS58Address:
    def test_valid_address(self):
        assert is_valid_ss58_address(_VALID_SS58) is True

    def test_garbage_string(self):
        assert is_valid_ss58_address("not_an_address") is False

    def test_empty_string(self):
        assert is_valid_ss58_address("") is False

    def test_too_short(self):
        assert is_valid_ss58_address("5ABC") is False

    def test_another_valid_address(self):
        # Another well-known valid SS58 address
        assert (
            is_valid_ss58_address("5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY")
            is True
        )


# ---------------------------------------------------------------------------
# is_valid_ed25519_pubkey
# ---------------------------------------------------------------------------


class TestIsValidEd25519Pubkey:
    def test_valid_64_char_hex(self):
        # 64 hex chars = 32 bytes
        valid_hex = "a" * 64
        assert is_valid_ed25519_pubkey(valid_hex) is True

    def test_bytes_wrong_length_returns_false(self):
        # 16 bytes is wrong length — returns False
        assert is_valid_ed25519_pubkey(b"\x00" * 16) is False

    def test_too_short_string(self):
        assert is_valid_ed25519_pubkey("abc") is False

    def test_non_string_non_bytes(self):
        assert is_valid_ed25519_pubkey(12345) is False


# ---------------------------------------------------------------------------
# is_valid_bittensor_address_or_public_key
# ---------------------------------------------------------------------------


class TestIsValidBittensorAddressOrPublicKey:
    def test_valid_ss58_dispatches_correctly(self):
        assert is_valid_bittensor_address_or_public_key(_VALID_SS58) is True

    def test_hex_prefix_dispatches_to_ed25519(self):
        # 0x prefix → ed25519 path; 64 hex chars valid
        assert is_valid_bittensor_address_or_public_key("0x" + "a" * 64) is True

    def test_hex_prefix_wrong_length(self):
        assert is_valid_bittensor_address_or_public_key("0xabc") is False

    def test_int_invalid(self):
        assert is_valid_bittensor_address_or_public_key(12345) is False

    def test_garbage_string(self):
        assert is_valid_bittensor_address_or_public_key("definitely_not_valid") is False


# ---------------------------------------------------------------------------
# format_error_message
# ---------------------------------------------------------------------------


class TestFormatErrorMessage:
    def test_dict_with_type_name_docs(self):
        err = {
            "type": "ModuleError",
            "name": "StakeNotEnough",
            "docs": ["Stake too low"],
        }
        result = format_error_message(err)
        assert "StakeNotEnough" in result

    def test_dict_with_code_message_data(self):
        err = {"code": 1001, "message": "Bad request", "data": "Custom error: details"}
        result = format_error_message(err)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_plain_exception(self):
        exc = Exception("something went wrong")
        result = format_error_message(exc)
        assert "something went wrong" in result

    def test_exception_with_dict_arg(self):
        # SubstrateRequestException pattern: arg is a dict literal string
        err_dict = str({"error": {"type": "Err", "name": "TestErr", "docs": ["doc"]}})
        exc = Exception(err_dict)
        result = format_error_message(exc)
        assert isinstance(result, str)

    def test_unknown_dict_falls_back(self):
        err = {"unknown_key": "value"}
        result = format_error_message(err)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# validate_netuid
# ---------------------------------------------------------------------------


class TestValidateNetuid:
    def test_positive_passes_through(self):
        assert validate_netuid(1) == 1

    def test_zero_passes_through(self):
        assert validate_netuid(0) == 0

    def test_none_passes_through(self):
        assert validate_netuid(None) is None

    def test_negative_raises_bad_parameter(self):
        with pytest.raises(typer.BadParameter):
            validate_netuid(-1)

    def test_large_value_passes(self):
        assert validate_netuid(999) == 999
