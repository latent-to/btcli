"""
Extrinsics for serving operations (axon management).
"""

import typing
from typing import Optional

import netaddr
from bittensor_wallet import Wallet

from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    console,
    print_error,
    print_success,
    format_error_message,
    unlock_key,
    print_extrinsic_id,
)

if typing.TYPE_CHECKING:
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface


def ip_to_int(ip_str: str) -> int:
    """
    Converts an IP address string to its integer representation.

    Args:
        ip_str: IP address string (e.g., "192.168.1.1")

    Returns:
        Integer representation of the IP address
    """
    import netaddr

    return int(netaddr.IPAddress(ip_str))


async def reset_axon_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    netuid: int,
    prompt: bool = False,
    decline: bool = False,
    quiet: bool = False,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
) -> tuple[bool, str, Optional[str]]:
    """
    Resets the axon information for a neuron on the network.

    This effectively removes the serving endpoint by setting the IP to 0.0.0.0
    and port to 0, indicating the neuron is no longer serving.

    Args:
        subtensor: The subtensor interface to use for the extrinsic
        wallet: The wallet containing the hotkey to reset the axon for
        netuid: The network UID where the neuron is registered
        prompt: Whether to prompt for confirmation before submitting
        wait_for_inclusion: Whether to wait for the extrinsic to be included in a block
        wait_for_finalization: Whether to wait for the extrinsic to be finalized

    Returns:
        Tuple of (success: bool, message: str, extrinsic_id: Optional[str])
    """
    # Unlock the hotkey
    if not (
        unlock_status := unlock_key(wallet, unlock_type="hot", print_out=False)
    ).success:
        return False, unlock_status.message, None

    # Prompt for confirmation if requested
    if prompt:
        if not confirm_action(
            f"Do you want to reset the axon for hotkey [bold]{wallet.hotkey.ss58_address}[/bold] "
            f"on netuid [bold]{netuid}[/bold]?",
            decline=decline,
            quiet=quiet,
        ):
            return False, "User cancelled the operation", None

    with console.status(
        f":satellite: Resetting axon on [white]netuid {netuid}[/white]..."
    ):
        try:
            # Compose the serve_axon call with reset values (IP: 0.0.0.0, port: 1)
            # Note: Port must be >= 1 as chain rejects port 0 as invalid
            call = await subtensor.substrate.compose_call(
                call_module="SubtensorModule",
                call_function="serve_axon",
                call_params={
                    "netuid": netuid,
                    "version": 0,
                    "ip": ip_to_int("0.0.0.0"),
                    "port": 1,
                    "ip_type": 4,  # IPv4
                    "protocol": 4,
                    "placeholder1": 0,
                    "placeholder2": 0,
                },
            )

            # Sign with hotkey and submit the extrinsic
            extrinsic = await subtensor.substrate.create_signed_extrinsic(
                call=call,
                keypair=wallet.hotkey,
            )
            response = await subtensor.substrate.submit_extrinsic(
                extrinsic,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
            )

            # We only wait here if we expect finalization.
            if not wait_for_finalization and not wait_for_inclusion:
                print_success(
                    "[dark_sea_green3]Axon reset successfully[/dark_sea_green3]"
                )
                return True, "Not waiting for finalization or inclusion.", None

            success = await response.is_success
            if not success:
                error_msg = format_error_message(await response.error_message)
                print_error(f"Failed: {error_msg}")
                return False, error_msg, None
            else:
                ext_id = await response.get_extrinsic_identifier()
                await print_extrinsic_id(response)
                print_success(
                    "[dark_sea_green3]Axon reset successfully[/dark_sea_green3]"
                )
                return True, "Axon reset successfully", ext_id

        except Exception as e:
            error_message = format_error_message(e)
            print_error(f"Failed to reset axon: {error_message}")
            return False, error_message, None


async def set_axon_extrinsic(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    netuid: int,
    ip: str,
    port: int,
    ip_type: int = 4,
    protocol: int = 4,
    prompt: bool = False,
    decline: bool = False,
    quiet: bool = False,
    wait_for_inclusion: bool = True,
    wait_for_finalization: bool = False,
) -> tuple[bool, str, Optional[str]]:
    """
    Sets the axon information for a neuron on the network.

    This configures the serving endpoint for a neuron by specifying its IP address
    and port, allowing other neurons to connect to it.

    Args:
        subtensor: The subtensor interface to use for the extrinsic
        wallet: The wallet containing the hotkey to set the axon for
        netuid: The network UID where the neuron is registered
        ip: The IP address to set (e.g., "192.168.1.1")
        port: The port number to set
        ip_type: IP type (4 for IPv4, 6 for IPv6)
        protocol: Protocol version (default: 4)
        prompt: Whether to prompt for confirmation before submitting
        wait_for_inclusion: Whether to wait for the extrinsic to be included in a block
        wait_for_finalization: Whether to wait for the extrinsic to be finalized

    Returns:
        Tuple of (success: bool, message: str, extrinsic_id: Optional[str])
    """
    # Validate port
    if not (0 <= port <= 65535):
        return False, f"Invalid port number: {port}. Must be between 0 and 65535.", None

    # Validate ip_type
    if ip_type not in (4, 6):
        return (
            False,
            f"Invalid ip_type: {ip_type}. Must be 4 (IPv4) or 6 (IPv6).",
            None,
        )

    # Validate IP address and that its version matches the supplied ip_type.
    # netaddr.IPAddress auto-detects v4 vs v6 from the string, so a mismatch
    # would otherwise be silently submitted on-chain (the integer is
    # 32-bit when interpreted as ip_type=4 and 128-bit when interpreted as
    # ip_type=6, so the wrong combination yields an unreachable axon).
    try:
        parsed_ip = netaddr.IPAddress(ip)
        ip_int = int(parsed_ip)
    except Exception as e:
        return False, f"Invalid IP address: {ip}. Error: {str(e)}", None

    if parsed_ip.version != ip_type:
        return (
            False,
            f"IP {ip} is IPv{parsed_ip.version}, but --ip-type was set to {ip_type}.",
            None,
        )

    # Unlock the hotkey
    if not (
        unlock_status := unlock_key(wallet, unlock_type="hot", print_out=False)
    ).success:
        return False, unlock_status.message, None

    # Prompt for confirmation if requested
    if prompt:
        if not confirm_action(
            f"Do you want to set the axon for hotkey [bold]{wallet.hotkey.ss58_address}[/bold] "
            f"on netuid [bold]{netuid}[/bold] to [bold]{ip}:{port}[/bold]?",
            decline=decline,
            quiet=quiet,
        ):
            return False, "User cancelled the operation", None

    with console.status(
        f":satellite: Setting axon on [white]netuid {netuid}[/white] to [white]{ip}:{port}[/white]..."
    ):
        try:
            # Compose the serve_axon call
            call = await subtensor.substrate.compose_call(
                call_module="SubtensorModule",
                call_function="serve_axon",
                call_params={
                    "netuid": netuid,
                    "version": 0,
                    "ip": ip_int,
                    "port": port,
                    "ip_type": ip_type,
                    "protocol": protocol,
                    "placeholder1": 0,
                    "placeholder2": 0,
                },
            )

            # Sign with hotkey and submit the extrinsic
            extrinsic = await subtensor.substrate.create_signed_extrinsic(
                call=call,
                keypair=wallet.hotkey,
            )
            response = await subtensor.substrate.submit_extrinsic(
                extrinsic,
                wait_for_inclusion=wait_for_inclusion,
                wait_for_finalization=wait_for_finalization,
            )

            # We only wait here if we expect finalization.
            if not wait_for_finalization and not wait_for_inclusion:
                print_success(
                    f"[dark_sea_green3]Axon set successfully to {ip}:{port}[/dark_sea_green3]"
                )
                return True, "Not waiting for finalization or inclusion.", None

            success = await response.is_success
            if not success:
                error_msg = format_error_message(await response.error_message)
                print_error(f"Failed: {error_msg}")
                return False, error_msg, None
            else:
                ext_id = await response.get_extrinsic_identifier()
                await print_extrinsic_id(response)
                print_success(
                    f"[dark_sea_green3]Axon set successfully to {ip}:{port}[/dark_sea_green3]"
                )
                return True, f"Axon set successfully to {ip}:{port}", ext_id

        except Exception as e:
            error_message = format_error_message(e)
            print_error(f"Failed to set axon: {error_message}")
            return False, error_message, None
