import json
import time

import pytest

from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface
from bittensor_cli.src.bittensor.utils import U64_MAX
from bittensor_cli.src.commands.stake.children_hotkeys import (
    get_childkey_completion_block,
)


@pytest.mark.asyncio
async def test_get_childkey_completion_block(local_chain):
    async with SubtensorInterface("ws://127.0.0.1:9945") as subtensor:
        current_block, completion_block = await get_childkey_completion_block(
            subtensor, 1
        )
        assert (completion_block - current_block) >= 7200


def test_children_hotkeys(local_chain, wallet_setup):
    """
    Test child hotkey set, get, take, and revoke flows using Alice (parent) and Bob (child).

    Steps:
        1. Setup: Create a subnet, register Alice and Bob.
        2. Set: Alice sets Bob as a child hotkey with proportion 0.5 on the subnet.
        3. Get: Verify Bob appears as Alice's child hotkey.
        4. Take: Alice sets a child take for Bob on the subnet.
        5. Revoke: Alice revokes Bob as a child hotkey.
        6. Get: Verify Bob no longer appears as Alice's child hotkey.
    """
    print("Testing child hotkey commands 🧪")

    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        "//Alice"
    )
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup("//Bob")

    # Create a subnet for testing
    create_subnet_result = exec_command_alice(
        command="subnets",
        sub_command="create",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--chain",
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
            "A test subnet for child hotkey e2e testing",
            "--additional-info",
            "Created by Alice",
            "--logo-url",
            "https://testsubnet.com/logo.png",
            "--no-prompt",
            "--no-mev-protection",
            "--json-output",
        ],
    )
    create_subnet_payload = json.loads(create_subnet_result.stdout)
    assert create_subnet_payload["success"] is True, (
        create_subnet_result.stdout,
        create_subnet_result.stderr,
    )
    netuid = create_subnet_payload["netuid"]

    # Start emission schedule for the subnet
    start_emission_result = exec_command_alice(
        command="subnets",
        sub_command="start",
        extra_args=[
            "--netuid",
            str(netuid),
            "--wallet-name",
            wallet_alice.name,
            "--no-prompt",
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-path",
            wallet_path_alice,
        ],
    )
    assert (
        f"Successfully started subnet {netuid}'s emission schedule."
        in start_emission_result.stdout
    )

    # Register Bob on the subnet
    register_bob_result = exec_command_bob(
        command="subnets",
        sub_command="register",
        extra_args=[
            "--netuid",
            str(netuid),
            "--wallet-path",
            wallet_path_bob,
            "--wallet-name",
            wallet_bob.name,
            "--hotkey",
            wallet_bob.hotkey_str,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
        ],
    )
    assert "✅ Registered" in register_bob_result.stdout, register_bob_result.stderr

    time.sleep(3)

    ################################
    # TEST 1: Set child hotkey
    # Alice sets Bob as a child with 50% proportion
    ################################

    set_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "set",
            "--children",
            wallet_bob.hotkey.ss58_address,
            "--proportions",
            "0.5",  # 50%
            "--netuid",
            str(netuid),
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--wait-for-inclusion",
            "--wait-for-finalization",
        ],
    )
    assert "Set children hotkeys" in set_children_result.stdout, (
        set_children_result.stderr
    )

    time.sleep(8)
    ################################
    # TEST 2: Get child hotkeys
    # Verify Bob is listed as Alice's child on the subnet
    ################################
    get_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "get",
            "--netuid",
            str(netuid),
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--network",
            "ws://127.0.0.1:9945",
            "--json-output",
            "--verbose",
        ],
    )
    get_children_result_json = json.loads(get_children_result.stdout)
    # should be 50% which is U64_MAX / 2
    assert get_children_result_json[str(netuid)][wallet_bob.hotkey.ss58_address] == int(
        U64_MAX / 2
    ), (
        f"Bob's hotkey not found in children output:\n{get_children_result.stdout} | {get_children_result.stderr}"
    )
    time.sleep(3)
    ################################
    # TEST 3: Set child take
    # Alice sets a 10% take for Bob as child hotkey on the subnet
    ################################

    set_take_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "take",
            "--child-hotkey-ss58",
            wallet_bob.hotkey.ss58_address,
            "--netuid",
            str(netuid),
            "--take",
            "0.10",
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--wait-for-inclusion",
            "--wait-for-finalization",
            "--json-output",
        ],
    )
    try:
        set_take_result_json = json.loads(set_take_result.stdout)
    except json.decoder.JSONDecodeError:
        print("DEBUG221", set_take_result.stdout, set_take_result.stderr)
        raise Exception
    assert set_take_result_json[str(netuid)]["success"] is True, (
        f"Take not set:\n{set_take_result.stdout}\n{set_take_result.stderr}"
    )

    ################################
    # TEST 3: Revoke child hotkeys
    # Alice revokes Bob as a child hotkey on the subnet
    ################################

    revoke_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "revoke",
            "--netuid",
            str(netuid),
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--wait-for-inclusion",
            "--wait-for-finalization",
        ],
    )
    assert "revocation request" in revoke_children_result.stdout.lower() or (
        "✅" in revoke_children_result.stdout
    ), (
        f"Revoke did not succeed:\n{revoke_children_result.stdout}\n{revoke_children_result.stderr}"
    )

    # Verify Bob is no longer listed as a child
    get_children_after_revoke = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "get",
            "--netuid",
            str(netuid),
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
        ],
    )
    assert wallet_bob.hotkey.ss58_address not in get_children_after_revoke.stdout, (
        f"Bob's hotkey still found after revoke:\n{get_children_after_revoke.stdout}"
    )

    print("Passed child hotkey commands")
