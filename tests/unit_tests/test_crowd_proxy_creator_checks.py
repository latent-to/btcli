from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bittensor_cli.src.bittensor.balances import Balance
from .conftest import COLDKEY_SS58, PROXY_SS58


def _make_crowdloan(
    creator: str,
    *,
    finalized: bool = False,
    raised_tao: float = 5.0,
    cap_tao: float = 10.0,
) -> MagicMock:
    crowdloan = MagicMock()
    crowdloan.creator = creator
    crowdloan.finalized = finalized
    crowdloan.raised = Balance.from_tao(raised_tao)
    crowdloan.cap = Balance.from_tao(cap_tao)
    return crowdloan


@pytest.mark.asyncio
async def test_finalize_crowdloan_allows_proxy_creator_actor(
    mock_wallet, mock_subtensor
):
    from bittensor_cli.src.commands.crowd.create import finalize_crowdloan

    mock_subtensor.get_single_crowdloan = AsyncMock(
        return_value=_make_crowdloan(creator=PROXY_SS58)
    )
    mock_subtensor.substrate.get_block_number = AsyncMock(return_value=12345)

    result = await finalize_crowdloan(
        subtensor=mock_subtensor,
        wallet=mock_wallet,
        proxy=PROXY_SS58,
        crowdloan_id=7,
        wait_for_inclusion=True,
        wait_for_finalization=False,
        prompt=False,
        json_output=False,
    )

    assert result == (False, "Crowdloan has not reached its cap.")


@pytest.mark.asyncio
async def test_finalize_crowdloan_rejects_non_creator_proxy_actor(
    mock_wallet, mock_subtensor
):
    from bittensor_cli.src.commands.crowd.create import finalize_crowdloan

    mock_subtensor.get_single_crowdloan = AsyncMock(
        return_value=_make_crowdloan(creator=COLDKEY_SS58)
    )
    mock_subtensor.substrate.get_block_number = AsyncMock(return_value=12345)

    result = await finalize_crowdloan(
        subtensor=mock_subtensor,
        wallet=mock_wallet,
        proxy=PROXY_SS58,
        crowdloan_id=7,
        wait_for_inclusion=True,
        wait_for_finalization=False,
        prompt=False,
        json_output=False,
    )

    assert result == (False, "Only the creator can finalize a crowdloan.")


@pytest.mark.asyncio
async def test_update_crowdloan_allows_proxy_creator_actor(mock_wallet, mock_subtensor):
    from bittensor_cli.src.commands.crowd.update import update_crowdloan

    mock_subtensor.get_single_crowdloan = AsyncMock(
        return_value=_make_crowdloan(creator=PROXY_SS58)
    )
    mock_subtensor.substrate.get_chain_head = AsyncMock(return_value="0xhead")
    mock_subtensor.substrate.get_block_number = AsyncMock(return_value=12345)
    mock_subtensor.substrate.init_runtime = AsyncMock(return_value=MagicMock())

    with (
        patch(
            "bittensor_cli.src.commands.crowd.update.get_constant",
            new_callable=AsyncMock,
            side_effect=[Balance.from_tao(1).rao, 1, 1000],
        ),
        patch(
            "bittensor_cli.src.commands.crowd.update.show_crowdloan_details",
            new_callable=AsyncMock,
        ),
    ):
        result = await update_crowdloan(
            subtensor=mock_subtensor,
            wallet=mock_wallet,
            proxy=PROXY_SS58,
            crowdloan_id=9,
            min_contribution=None,
            end=None,
            cap=None,
            prompt=False,
            json_output=False,
        )

    assert result == (False, "No update parameter specified.")


@pytest.mark.asyncio
async def test_dissolve_crowdloan_allows_proxy_creator_actor(
    mock_wallet, mock_subtensor
):
    from bittensor_cli.src.commands.crowd.dissolve import dissolve_crowdloan

    crowdloan = _make_crowdloan(creator=PROXY_SS58, raised_tao=12.0, cap_tao=20.0)
    mock_subtensor.get_single_crowdloan = AsyncMock(return_value=crowdloan)
    mock_subtensor.substrate.get_block_number = AsyncMock(return_value=12345)
    mock_subtensor.get_crowdloan_contribution = AsyncMock(
        return_value=Balance.from_tao(1.0)
    )

    result = await dissolve_crowdloan(
        subtensor=mock_subtensor,
        wallet=mock_wallet,
        proxy=PROXY_SS58,
        crowdloan_id=11,
        prompt=False,
        json_output=False,
    )

    assert result == (False, "Crowdloan not ready to dissolve.")
