import asyncio
import json

import pytest

from .utils import turn_off_hyperparam_freeze_window

"""
Verify commands:

* btcli subnets create
* btcli sudo trigger-epoch
"""


def test_trigger_epoch(local_chain, wallet_setup):
    netuid = 2
    wallet_path_alice = "//Alice"
    wallet_path_bob = "//Bob"

    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup(
        wallet_path_bob
    )
    print("Created keypairs")

    # All direct substrate work must happen in a single asyncio.run: the websocket
    # connection binds to the event loop that first uses it, so a second
    # asyncio.run on the same local_chain object hangs forever.
    async def _supports_trigger_epoch_and_unfreeze() -> bool:
        # The owner-side trigger_epoch extrinsic only exists on dynamic-tempo
        # runtimes.
        try:
            await local_chain.compose_call(
                call_module="SubtensorModule",
                call_function="trigger_epoch",
                call_params={"netuid": netuid},
            )
        except ValueError:
            return False
        # With the freeze window on, a fresh subnet's next auto epoch can be close
        # enough that trigger_epoch fails with AutoEpochAlreadyImminent.
        await turn_off_hyperparam_freeze_window(local_chain, wallet_alice)
        return True

    if not asyncio.run(_supports_trigger_epoch_and_unfreeze()):
        pytest.skip(
            "Chain does not support SubtensorModule.trigger_epoch "
            "(pre-dynamic-tempo runtime)."
        )

    # Register a subnet with sudo as Alice
    result = exec_command_alice(
        command="subnets",
        sub_command="create",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--network",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--subnet-name",
            "Test Subnet",
            "--repo",
            "https://github.com/username/repo",
            "--contact",
            "alice@opentensor.dev",
            "--url",
            "https://testsubnet.com",
            "--discord",
            "alice#1234",
            "--description",
            "A test subnet for e2e testing",
            "--additional-info",
            "Created by Alice",
            "--logo-url",
            "https://testsubnet.com/logo.png",
            "--no-prompt",
            "--json-output",
            "--no-mev-protection",
        ],
    )
    result_output = json.loads(result.stdout)
    assert result_output["success"] is True
    assert result_output["netuid"] == netuid

    # The chain rejects trigger_epoch while commit-reveal is enabled
    # (DynamicTempoBlockedByCommitReveal), and localnet subnets have it enabled
    # by default — disable it first.
    cmd = exec_command_alice(
        command="sudo",
        sub_command="set",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--network",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--netuid",
            netuid,
            "--json-out",
            "--no-prompt",
            "--param",
            "commit_reveal_weights_enabled",
            "--value",
            "false",
        ],
    )
    cmd_json = json.loads(cmd.stdout)
    assert cmd_json["success"] is True, (cmd.stdout, cmd_json)

    # A non-owner cannot trigger an epoch
    cmd = exec_command_bob(
        command="sudo",
        sub_command="trigger-epoch",
        extra_args=[
            "--wallet-path",
            wallet_path_bob,
            "--network",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            wallet_bob.name,
            "--wallet-hotkey",
            wallet_bob.hotkey_str,
            "--netuid",
            netuid,
            "--json-out",
            "--no-prompt",
        ],
    )
    cmd_json = json.loads(cmd.stdout)
    assert cmd_json["success"] is False, (cmd.stdout, cmd_json)
    assert "doesn't own" in cmd_json["message"]

    # The subnet owner triggers an epoch
    cmd = exec_command_alice(
        command="sudo",
        sub_command="trigger-epoch",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--network",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--netuid",
            netuid,
            "--json-out",
            "--no-prompt",
        ],
    )
    cmd_json = json.loads(cmd.stdout)
    assert cmd_json["success"] is True, (cmd.stdout, cmd_json)
    assert isinstance(cmd_json["extrinsic_identifier"], str)
    # fires_at is read from the EpochTriggered event; it should decode on a
    # dynamic-tempo chain.
    assert isinstance(cmd_json["fires_at"], int), cmd_json
    print(f"Successfully triggered epoch on SN{netuid}")
