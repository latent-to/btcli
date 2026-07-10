"""
Unit tests for sudo.get_hyperparameters.

Covers the (success, hyperparameter names) return contract and the `numbered`
selection mode used by the interactive `btcli sudo set` flow: one table where
params missing from the runtime API are filled in from SubtensorModule storage
and setter aliases are excluded.
"""

import pytest
from unittest.mock import AsyncMock

from bittensor_cli.src import (
    HYPERPARAMS,
    HYPERPARAMS_SETTER_ALIASES,
    HYPERPARAMS_STORAGE,
)
from bittensor_cli.src.bittensor.chain_data import SubnetHyperparameters
from bittensor_cli.src.commands.sudo import get_hyperparameters

CHAIN_PARAMS = {"immunity_period": 65535, "kappa": 32767, "tempo": 360}
STORAGE_VALUES = {
    "MaxAllowedUids": 257,
    "MinAllowedUids": 64,
    "NetworkPowRegistrationAllowed": True,
    "RecycleOrBurn": "Burn",
    "SubnetOwnerHotkey": "5HdrwVQQbMa8Wh271PDzvMHmM44wYM5wfnXCW3o97gDisuaY",
}


def _prepare_subtensor(mock_subtensor) -> SubnetHyperparameters:
    subnet = SubnetHyperparameters(hyperparameters=dict(CHAIN_PARAMS))
    mock_subtensor.get_subnet_hyperparameters = AsyncMock(return_value=subnet)
    mock_subtensor.subnet = AsyncMock(return_value=AsyncMock(subnet_name="test"))
    mock_subtensor.query = AsyncMock(
        side_effect=lambda module, func, params: STORAGE_VALUES.get(func)
    )
    return subnet


@pytest.mark.asyncio
async def test_returns_chain_params_in_display_order(mock_subtensor):
    _prepare_subtensor(mock_subtensor)

    success, param_names = await get_hyperparameters(mock_subtensor, netuid=18)

    assert success is True
    assert param_names == sorted(CHAIN_PARAMS)
    mock_subtensor.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_numbered_fills_missing_values_from_storage(mock_subtensor):
    subnet = _prepare_subtensor(mock_subtensor)

    success, param_names = await get_hyperparameters(
        mock_subtensor, netuid=18, numbered=True
    )

    assert success is True
    for name, storage_function in HYPERPARAMS_STORAGE.items():
        assert subnet.hyperparameters[name] == STORAGE_VALUES[storage_function]
        assert name in param_names
    mock_subtensor.query.assert_awaited_with(
        "SubtensorModule", "SubnetOwnerHotkey", [18]
    )


@pytest.mark.asyncio
async def test_numbered_excludes_setter_aliases_and_has_no_duplicates(mock_subtensor):
    _prepare_subtensor(mock_subtensor)

    _, param_names = await get_hyperparameters(mock_subtensor, netuid=18, numbered=True)

    assert HYPERPARAMS_SETTER_ALIASES.isdisjoint(param_names)
    assert set(HYPERPARAMS) - HYPERPARAMS_SETTER_ALIASES <= set(param_names)
    assert len(param_names) == len(set(param_names))


@pytest.mark.asyncio
async def test_numbered_falls_back_to_placeholder_when_storage_empty(mock_subtensor):
    _prepare_subtensor(mock_subtensor)
    mock_subtensor.query = AsyncMock(return_value=None)

    _, param_names = await get_hyperparameters(mock_subtensor, netuid=18, numbered=True)

    assert "sn_owner_hotkey" in param_names  # appended with "-" placeholders


@pytest.mark.asyncio
async def test_numbered_has_no_effect_on_json_output(mock_subtensor, capsys):
    _prepare_subtensor(mock_subtensor)

    success, param_names = await get_hyperparameters(
        mock_subtensor, netuid=18, json_output=True, numbered=True
    )

    assert success is True
    assert param_names == sorted(CHAIN_PARAMS)
    assert "sn_owner_hotkey" not in capsys.readouterr().out
    mock_subtensor.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonexistent_subnet_returns_failure(mock_subtensor):
    mock_subtensor.subnet_exists = AsyncMock(return_value=False)

    success, param_names = await get_hyperparameters(mock_subtensor, netuid=999)

    assert success is False
    assert param_names == []
