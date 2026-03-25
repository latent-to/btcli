import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

from bittensor_wallet import Wallet
import numpy as np
from numpy.typing import NDArray
from async_substrate_interface.errors import SubstrateRequestException

from bittensor_cli.src.bittensor.utils import (
    confirm_action,
    print_error,
    print_success,
    console,
    format_error_message,
    get_hotkey_pub_ss58,
    print_extrinsic_id,
)
from bittensor_cli.src.bittensor.json_utils import print_transaction_response
from bittensor_cli.src.bittensor.extrinsics.root import (
    convert_weights_and_uids_for_emit,
    generate_weight_hash,
)

if TYPE_CHECKING:
    from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface


# helpers and extrinsics


class SetWeightsExtrinsic:
    def __init__(
        self,
        subtensor: "SubtensorInterface",
        wallet: Wallet,
        netuid: int,
        proxy: Optional[str],
        uids: NDArray,
        weights: NDArray,
        salt: list[int],
        version_key: int,
        prompt: bool = False,
        decline: bool = False,
        quiet: bool = False,
        wait_for_inclusion: bool = False,
        wait_for_finalization: bool = False,
    ):
        self.subtensor = subtensor
        self.wallet = wallet
        self.netuid = netuid
        self.proxy = proxy
        self.uids = uids
        self.weights = weights
        self.salt = salt
        self.version_key = version_key
        self.prompt = prompt
        self.decline = decline
        self.quiet = quiet
        self.wait_for_inclusion = wait_for_inclusion
        self.wait_for_finalization = wait_for_finalization

    async def set_weights_extrinsic(self) -> tuple[bool, str, Optional[str]]:
        """
        Sets the inter-neuronal weights for the specified neuron. This process involves specifying the
        influence or trust a neuron places on other neurons in the network, which is a fundamental aspect
        of Bittensor's decentralized learning architecture.

        This function is crucial in shaping the network's collective intelligence, where each neuron's
        learning and contribution are influenced by the weights it sets towards others.

        Returns:
            Tuple[bool, str]:
                `True` if the setting of weights is successful, False otherwise. And `msg`, a string
                value describing the success or potential error.
        """

        # Reformat and normalize.
        weight_uids, weight_vals = convert_weights_and_uids_for_emit(
            self.uids, self.weights
        )

        # Ask before moving on.
        formatted_weight_vals = [float(v / 65535) for v in weight_vals]
        if self.prompt and not confirm_action(
            f"Do you want to set weights:\n[bold white]"
            f"  weights: {formatted_weight_vals}\n  uids: {weight_uids}[/bold white ]?",
            decline=self.decline,
            quiet=self.quiet,
        ):
            return False, "Prompt refused.", None

        # Check if the commit-reveal mechanism is active for the given netuid.
        if bool(
            await self.subtensor.get_hyperparameter(
                param_name="get_commit_reveal_weights_enabled",
                netuid=self.netuid,
                reuse_block=False,
            )
        ):
            return await self._commit_reveal(
                weight_uids,
                weight_vals,
            )
        else:
            return await self._set_weights_without_commit_reveal(
                weight_uids,
                weight_vals,
            )

    async def commit_weights(
        self,
        uids: list[int],
        weights: list[int],
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Commits a hash of the neuron's weights to the Bittensor blockchain using the provided wallet.
        This action serves as a commitment or snapshot of the neuron's current weight distribution.

        Args:
            uids (np.ndarray): NumPy array of neuron UIDs for which weights are being committed.
            weights (np.ndarray): NumPy array of weight values corresponding to each UID.

        Returns:
            Tuple[bool, str]: ``True`` if the weight commitment is successful, False otherwise. And `msg`, a string
            value describing the success or potential error.

        This function allows neurons to create a tamper-proof record of their weight distribution at a specific point in time,
        enhancing transparency and accountability within the Bittensor network.
        """

        # Generate the hash of the weights
        commit_hash = generate_weight_hash(
            address=get_hotkey_pub_ss58(self.wallet),
            netuid=self.netuid,
            uids=uids,
            values=weights,
            salt=self.salt,
            version_key=self.version_key,
        )

        # _logger.info("Commit Hash: {}".format(commit_hash))
        try:
            success, message, ext_id = await self.do_commit_weights(
                commit_hash=commit_hash
            )
        except SubstrateRequestException as e:
            print_error(f"Error committing weights: {format_error_message(e)}")
            # bittensor.logging.error(f"Error committing weights: {e}")
            success = False
            message = "No attempt made. Perhaps it is too soon to commit weights!"
            ext_id = None

        return success, message, ext_id

    async def _commit_reveal(
        self, weight_uids: list[int], weight_vals: list[int]
    ) -> tuple[bool, str, Optional[str]]:
        interval = int(
            await self.subtensor.get_hyperparameter(
                param_name="get_commit_reveal_period",
                netuid=self.netuid,
                reuse_block=False,
            )
        )

        if not self.salt:
            # Generate a random salt of specified length to be used in the commit-reveal process
            salt_length = 8
            self.salt = list(os.urandom(salt_length))

        # Attempt to commit the weights to the blockchain.
        commit_success, commit_msg, ext_id = await self.commit_weights(
            uids=weight_uids,
            weights=weight_vals,
        )

        if commit_success:
            current_time = datetime.now().astimezone().replace(microsecond=0)
            reveal_time = (current_time + timedelta(seconds=interval)).isoformat()
            cli_retry_cmd = f"--netuid {self.netuid} --uids {weight_uids} --weights {self.weights} --reveal-using-salt {self.salt}"
            # Print params to screen and notify user this is a blocking operation
            print_success("Weights hash committed to chain")
            console.print(
                f":alarm_clock: [dark_orange3]Weights hash will be revealed at {reveal_time}[/dark_orange3]"
            )
            console.print(
                ":alarm_clock: [red]WARNING: Turning off your computer will prevent this process from executing!!![/red]"
            )
            console.print(
                f"To manually retry after {reveal_time} run:\n{cli_retry_cmd}"
            )

            # bittensor.logging.info(msg=f"Weights hash committed and will be revealed at {reveal_time}")

            console.print(
                "Note: BTCLI will wait until the reveal time. To place BTCLI into background:"
            )
            console.print(
                "[red]CTRL+Z[/red] followed by the command [red]bg[/red] and [red]ENTER[/red]"
            )
            console.print(
                "To bring BTLCI into the foreground use the command [red]fg[/red] and [red]ENTER[/red]"
            )

            # Attempt executing reveal function after a delay of 'interval'
            await self.subtensor.substrate.close()
            await asyncio.sleep(interval)
            async with self.subtensor:
                return await self.reveal(weight_uids, weight_vals)
        else:
            print_error(f"Failed: error:{commit_msg}")
            # bittensor.logging.error(msg=commit_msg, prefix="Set weights with hash commit",
            #                         suffix=f"<red>Failed: {commit_msg}</red>")
            return False, f"Failed to commit weights hash. {commit_msg}", None

    async def reveal(self, weight_uids, weight_vals) -> tuple[bool, str, Optional[str]]:
        # Attempt to reveal the weights using the salt.
        success, msg, ext_id = await self.reveal_weights_extrinsic(
            weight_uids, weight_vals
        )

        if success:
            if not self.wait_for_finalization and not self.wait_for_inclusion:
                return True, "Not waiting for finalization or inclusion.", ext_id

            print_success("Weights hash revealed on chain")
            return (
                True,
                "Successfully revealed previously committed weights hash.",
                ext_id,
            )
        else:
            return False, "Failed to reveal weights.", None

    async def _set_weights_without_commit_reveal(
        self,
        weight_uids,
        weight_vals,
    ) -> tuple[bool, str, Optional[str]]:
        async def _do_set_weights() -> tuple[bool, str, Optional[str]]:
            call = await self.subtensor.substrate.compose_call(
                call_module="SubtensorModule",
                call_function="set_weights",
                call_params={
                    "dests": weight_uids,
                    "weights": weight_vals,
                    "netuid": self.netuid,
                    "version_key": self.version_key,
                },
            )
            # Period dictates how long the extrinsic will stay as part of waiting pool
            success, err_msg, response = await self.subtensor.sign_and_send_extrinsic(
                call=call,
                sign_with="hotkey",
                wallet=self.wallet,
                era={"period": 5},
                wait_for_finalization=True,
                wait_for_inclusion=True,
                proxy=self.proxy,
            )
            # We only wait here if we expect finalization.
            if not self.wait_for_finalization and not self.wait_for_inclusion:
                return True, "Not waiting for finalization or inclusion.", None

            if success:
                ext_id_ = await response.get_extrinsic_identifier()
                await print_extrinsic_id(response)
                return True, "Successfully set weights.", ext_id_
            else:
                return False, err_msg, None

        with console.status(
            f":satellite: Setting weights on [white]{self.subtensor.network}[/white] ..."
        ):
            success, error_message, ext_id = await _do_set_weights()

            if not self.wait_for_finalization and not self.wait_for_inclusion:
                return True, "Not waiting for finalization or inclusion.", None

            if success:
                print_success("Finalized")
                # bittensor.logging.success(prefix="Set weights", suffix="<green>Finalized: </green>" + str(success))
                return True, "Successfully set weights and finalized.", ext_id
            else:
                # bittensor.logging.error(msg=error_message, prefix="Set weights", suffix="<red>Failed: </red>")
                return False, error_message, None

    async def reveal_weights_extrinsic(
        self, weight_uids, weight_vals
    ) -> tuple[bool, str, Optional[str]]:
        if self.prompt and not confirm_action(
            "Would you like to reveal weights?", decline=self.decline, quiet=self.quiet
        ):
            return False, "User cancelled the operation.", None

        call = await self.subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function="reveal_weights",
            call_params={
                "netuid": self.netuid,
                "uids": weight_uids,
                "values": weight_vals,
                "salt": self.salt,
                "version_key": self.version_key,
            },
        )
        success, error_message, response = await self.subtensor.sign_and_send_extrinsic(
            call=call,
            wallet=self.wallet,
            sign_with="hotkey",
            wait_for_inclusion=self.wait_for_inclusion,
            wait_for_finalization=self.wait_for_finalization,
            proxy=self.proxy,
        )

        if not self.wait_for_finalization and not self.wait_for_inclusion:
            return True, "", None

        if success:
            await print_extrinsic_id(response)
            return (
                True,
                "Successfully revealed weights.",
                await response.get_extrinsic_identifier(),
            )
        else:
            return False, error_message, None

    async def do_commit_weights(
        self, commit_hash
    ) -> tuple[bool, Optional[str], Optional[str]]:
        call = await self.subtensor.substrate.compose_call(
            call_module="SubtensorModule",
            call_function="commit_weights",
            call_params={
                "netuid": self.netuid,
                "commit_hash": commit_hash,
            },
        )
        success, err_msg, response = await self.subtensor.sign_and_send_extrinsic(
            call=call,
            wallet=self.wallet,
            sign_with="hotkey",
            wait_for_inclusion=self.wait_for_inclusion,
            wait_for_finalization=self.wait_for_finalization,
            proxy=self.proxy,
        )

        if not self.wait_for_finalization and not self.wait_for_inclusion:
            return True, None, None

        if success:
            ext_id = await response.get_extrinsic_identifier()
            await print_extrinsic_id(response)
            return True, None, ext_id
        else:
            return False, err_msg, None


# commands


async def reveal_weights(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    netuid: int,
    proxy: Optional[str],
    uids: list[int],
    weights: list[float],
    salt: list[int],
    version: int,
    json_output: bool = False,
    prompt: bool = True,
) -> None:
    """Reveal weights for a specific subnet."""
    uids_ = np.array(
        uids,
        dtype=np.int64,
    )
    weights_ = np.array(
        weights,
        dtype=np.float32,
    )
    salt_ = np.array(
        salt,
        dtype=np.int64,
    )
    weight_uids, weight_vals = convert_weights_and_uids_for_emit(
        uids=uids_, weights=weights_
    )
    # Call the reveal function in the module set_weights from extrinsics package
    extrinsic = SetWeightsExtrinsic(
        subtensor=subtensor,
        wallet=wallet,
        netuid=netuid,
        uids=uids_,
        weights=weights_,
        salt=list(salt_),
        version_key=version,
        prompt=prompt,
        proxy=proxy,
    )
    success, message, ext_id = await extrinsic.reveal(weight_uids, weight_vals)
    if json_output:
        print_transaction_response(success, message, ext_id)
    else:
        if success:
            console.print("Weights revealed successfully")
        else:
            print_error(f"Failed to reveal weights: {message}")


async def commit_weights(
    subtensor: "SubtensorInterface",
    wallet: Wallet,
    netuid: int,
    uids: list[int],
    proxy: Optional[str],
    weights: list[float],
    salt: list[int],
    version: int,
    json_output: bool = False,
    prompt: bool = True,
):
    """Commits weights and then reveals them for a specific subnet"""
    uids_ = np.array(
        uids,
        dtype=np.int64,
    )
    weights_ = np.array(
        weights,
        dtype=np.float32,
    )
    salt_ = np.array(
        salt,
        dtype=np.int64,
    )
    extrinsic = SetWeightsExtrinsic(
        subtensor=subtensor,
        wallet=wallet,
        netuid=netuid,
        uids=uids_,
        weights=weights_,
        salt=list(salt_),
        version_key=version,
        prompt=prompt,
        proxy=proxy,
    )
    success, message, ext_id = await extrinsic.set_weights_extrinsic()
    if json_output:
        print_transaction_response(success, message, ext_id)
    else:
        if success:
            console.print("Weights set successfully")
        else:
            print_error(f"Failed to commit weights: {message}")
