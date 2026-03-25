"""
Axon commands for managing neuron serving endpoints.
"""

from typing import TYPE_CHECKING

from bittensor_wallet import Wallet

from bittensor_cli.src.bittensor.utils import print_error
from bittensor_cli.src.bittensor.json_utils import print_transaction_with_data
from bittensor_cli.src.bittensor.extrinsics.serving import (
    reset_axon_extrinsic,
    set_axon_extrinsic,
)

if TYPE_CHECKING:
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface


async def reset(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    netuid: int,
    json_output: bool,
    prompt: bool,
    wait_for_inclusion: bool,
    wait_for_finalization: bool,
):
    """
    Reset the axon information for a neuron on the network.

    This command removes the serving endpoint by setting the IP to 0.0.0.0 and port to 1,
    indicating the neuron is no longer serving.

    Args:
        wallet: The wallet containing the hotkey to reset the axon for
        subtensor: The subtensor interface to use for the extrinsic
        netuid: The network UID where the neuron is registered
        json_output: Whether to output results in JSON format
        prompt: Whether to prompt for confirmation before submitting
        wait_for_inclusion: Whether to wait for the extrinsic to be included in a block
        wait_for_finalization: Whether to wait for the extrinsic to be finalized
    """
    success, message, ext_id = await reset_axon_extrinsic(
        subtensor=subtensor,
        wallet=wallet,
        netuid=netuid,
        prompt=prompt,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
    )

    if json_output:
        print_transaction_with_data(
            success=success,
            message=message,
            extrinsic_identifier=ext_id,
            netuid=netuid,
            hotkey=wallet.hotkey.ss58_address,
        )
    elif not success:
        print_error(f"Failed to reset axon: {message}")


async def set_axon(
    wallet: Wallet,
    subtensor: "SubtensorInterface",
    netuid: int,
    ip: str,
    port: int,
    ip_type: int,
    protocol: int,
    json_output: bool,
    prompt: bool,
    wait_for_inclusion: bool,
    wait_for_finalization: bool,
):
    """
    Set the axon information for a neuron on the network.

    This command configures the serving endpoint for a neuron by specifying its IP address
    and port, allowing other neurons to connect to it.

    Args:
        wallet: The wallet containing the hotkey to set the axon for
        subtensor: The subtensor interface to use for the extrinsic
        netuid: The network UID where the neuron is registered
        ip: IP address to set for the axon
        port: Port number to set for the axon
        ip_type: IP type (4 for IPv4, 6 for IPv6)
        protocol: Protocol version
        json_output: Whether to output results in JSON format
        prompt: Whether to prompt for confirmation before submitting
        wait_for_inclusion: Whether to wait for the extrinsic to be included in a block
        wait_for_finalization: Whether to wait for the extrinsic to be finalized
    """
    success, message, ext_id = await set_axon_extrinsic(
        subtensor=subtensor,
        wallet=wallet,
        netuid=netuid,
        ip=ip,
        port=port,
        ip_type=ip_type,
        protocol=protocol,
        prompt=prompt,
        wait_for_inclusion=wait_for_inclusion,
        wait_for_finalization=wait_for_finalization,
    )

    if json_output:
        print_transaction_with_data(
            success=success,
            message=message,
            extrinsic_identifier=ext_id,
            netuid=netuid,
            hotkey=wallet.hotkey.ss58_address,
            ip=ip,
            port=port,
        )
    elif not success:
        print_error(f"Failed to set axon: {message}")
