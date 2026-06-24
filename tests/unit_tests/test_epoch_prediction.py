"""Unit tests for epoch prediction under the dynamic-tempo scheduler."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from bittensor_cli.src.commands.stake.children_hotkeys import (
    get_childkey_completion_block,
)


def _configure(
    mock_subtensor: MagicMock,
    block_number: int,
    tempo: int,
    blocks_since_last_step: int,
    next_epoch: int | None,
) -> None:
    mock_subtensor.substrate.get_block_number = AsyncMock(return_value=block_number)
    mock_subtensor.query = AsyncMock(return_value=blocks_since_last_step)
    mock_subtensor.get_hyperparameter = AsyncMock(return_value=tempo)
    mock_subtensor.get_next_epoch_start_block = AsyncMock(return_value=next_epoch)


@pytest.mark.asyncio
async def test_completion_uses_next_epoch_when_past_cooldown(mock_subtensor):
    # Cooldown ends at block 8_000; the chain already reports an epoch after it.
    _configure(
        mock_subtensor,
        block_number=800,
        tempo=360,
        blocks_since_last_step=100,
        next_epoch=8_100,
    )
    block_number, completion = await get_childkey_completion_block(
        mock_subtensor, netuid=1
    )
    assert block_number == 800
    assert completion == 8_100


@pytest.mark.asyncio
async def test_completion_steps_tempo_past_cooldown(mock_subtensor):
    # next_epoch is before the cooldown end (block 1_000 + 7_200 = 8_200): step
    # forward in tempo increments to the first epoch at or after it.
    _configure(
        mock_subtensor,
        block_number=1_000,
        tempo=360,
        blocks_since_last_step=100,
        next_epoch=1_260,
    )
    block_number, completion = await get_childkey_completion_block(
        mock_subtensor, netuid=1
    )
    assert block_number == 1_000
    expected = 1_260 + ((8_200 - 1_260 + 360 - 1) // 360) * 360
    assert completion == expected
    assert completion >= 8_200
    assert completion - 8_200 < 360


@pytest.mark.asyncio
async def test_completion_falls_back_to_legacy_modulo(mock_subtensor):
    # Chains without the dynamic-tempo runtime API return None: legacy math.
    block_number, tempo, blocks_since_last_step = 1_000, 360, 100
    _configure(
        mock_subtensor,
        block_number=block_number,
        tempo=tempo,
        blocks_since_last_step=blocks_since_last_step,
        next_epoch=None,
    )
    result_block, completion = await get_childkey_completion_block(
        mock_subtensor, netuid=1
    )
    cooldown = block_number + 7_200
    next_tempo = block_number + (tempo - blocks_since_last_step)
    expected = (cooldown - next_tempo) % (tempo + 1) + cooldown
    assert result_block == block_number
    assert completion == expected
