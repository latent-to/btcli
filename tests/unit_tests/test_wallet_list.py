import json
import pytest
from unittest.mock import MagicMock, patch

from .conftest import COLDKEY_SS58, HOTKEY_SS58

MODULE = "bittensor_cli.src.commands.wallets"


def _make_list_wallet(name: str = "coldkey1") -> MagicMock:
    wallet = MagicMock()
    wallet.name = name
    wallet.coldkeypub_file.exists_on_device.return_value = True
    wallet.coldkeypub_file.path = f"/tmp/{name}/coldkeypub.txt"
    wallet.coldkeypub_file.is_encrypted.return_value = False
    wallet.coldkeypub.ss58_address = COLDKEY_SS58
    wallet.coldkeypub.crypto_type = 1
    return wallet


def _make_hotkey_wallet(name: str = "default") -> MagicMock:
    hotkey = MagicMock()
    hotkey.name = name
    hotkey.hotkey_str = name
    hotkey.get_hotkey.return_value.ss58_address = HOTKEY_SS58
    hotkey.get_hotkey.return_value.crypto_type = 1
    return hotkey


def test_natural_sort_key_orders_numeric_suffixes():
    from bittensor_cli.src.commands.wallets import _natural_sort_key

    names = ["coldkey10", "coldkey2", "coldkey1", "zebra", "alice"]
    assert sorted(names, key=_natural_sort_key) == [
        "alice",
        "coldkey1",
        "coldkey2",
        "coldkey10",
        "zebra",
    ]


@pytest.mark.asyncio
async def test_wallet_list_sorts_coldkeys_naturally_in_json_output():
    from bittensor_cli.src.commands.wallets import wallet_list

    wallets = [
        _make_list_wallet("coldkey10"),
        _make_list_wallet("coldkey2"),
        _make_list_wallet("coldkey1"),
    ]

    with (
        patch(f"{MODULE}.utils.get_coldkey_wallets_for_path", return_value=wallets),
        patch(f"{MODULE}.json_console") as mock_json,
    ):
        await wallet_list("/tmp/wallets", json_output=True, coldkeys_only=True)

    payload = json.loads(mock_json.print.call_args[0][0])
    assert [wallet["name"] for wallet in payload["wallets"]] == [
        "coldkey1",
        "coldkey2",
        "coldkey10",
    ]


@pytest.mark.asyncio
async def test_wallet_list_sorts_hotkeys_naturally_in_json_output():
    from bittensor_cli.src.commands.wallets import wallet_list

    coldkey = _make_list_wallet()
    hotkeys = [
        _make_hotkey_wallet("hotkey10"),
        _make_hotkey_wallet("hotkey2"),
        _make_hotkey_wallet("hotkey1"),
    ]

    with (
        patch(f"{MODULE}.utils.get_coldkey_wallets_for_path", return_value=[coldkey]),
        patch(f"{MODULE}.utils.get_hotkey_wallets_for_wallet", return_value=hotkeys),
        patch(f"{MODULE}.json_console") as mock_json,
    ):
        await wallet_list("/tmp/wallets", json_output=True, coldkeys_only=False)

    payload = json.loads(mock_json.print.call_args[0][0])
    assert [hk["name"] for hk in payload["wallets"][0]["hotkeys"]] == [
        "hotkey1",
        "hotkey2",
        "hotkey10",
    ]


@pytest.mark.asyncio
async def test_wallet_list_coldkeys_only_skips_hotkey_lookup():
    from bittensor_cli.src.commands.wallets import wallet_list

    coldkey = _make_list_wallet()

    with (
        patch(f"{MODULE}.utils.get_coldkey_wallets_for_path", return_value=[coldkey]),
        patch(f"{MODULE}.utils.get_hotkey_wallets_for_wallet") as mock_hotkeys,
        patch(f"{MODULE}.console"),
    ):
        await wallet_list("/tmp/wallets", json_output=False, coldkeys_only=True)

    mock_hotkeys.assert_not_called()


@pytest.mark.asyncio
async def test_wallet_list_default_includes_hotkeys():
    from bittensor_cli.src.commands.wallets import wallet_list

    coldkey = _make_list_wallet()
    hotkey = _make_hotkey_wallet()

    with (
        patch(f"{MODULE}.utils.get_coldkey_wallets_for_path", return_value=[coldkey]),
        patch(
            f"{MODULE}.utils.get_hotkey_wallets_for_wallet", return_value=[hotkey]
        ) as mock_hotkeys,
        patch(f"{MODULE}.console"),
    ):
        await wallet_list("/tmp/wallets", json_output=False, coldkeys_only=False)

    mock_hotkeys.assert_called_once_with(coldkey, show_nulls=True, show_encrypted=True)


@pytest.mark.asyncio
async def test_wallet_list_coldkeys_only_json_has_empty_hotkeys():
    from bittensor_cli.src.commands.wallets import wallet_list

    coldkey = _make_list_wallet()

    with (
        patch(f"{MODULE}.utils.get_coldkey_wallets_for_path", return_value=[coldkey]),
        patch(f"{MODULE}.utils.get_hotkey_wallets_for_wallet") as mock_hotkeys,
        patch(f"{MODULE}.json_console") as mock_json,
    ):
        await wallet_list("/tmp/wallets", json_output=True, coldkeys_only=True)

    mock_hotkeys.assert_not_called()
    payload = json.loads(mock_json.print.call_args[0][0])
    assert len(payload["wallets"]) == 1
    assert payload["wallets"][0]["name"] == "coldkey1"
    assert payload["wallets"][0]["hotkeys"] == []
