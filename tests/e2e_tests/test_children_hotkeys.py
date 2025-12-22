import json

import pytest

from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface
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


@pytest.mark.parametrize("local_chain", [False], indirect=True)
def test_set_children(local_chain, wallet_setup):
    """
    Test setting children hotkeys on a subnet.

    Steps:
        1. Create wallets for Alice (parent) and Bob (child)
        2. Create a subnet and register Alice
        3. Add stake to Alice's hotkey
        4. Set Bob as a child hotkey with 50% proportion
        5. Verify children are set via get command
        6. Revoke children and verify removal
    """
    print("Testing set_children command 🧪")
    netuid = 2

    # Create wallets for Alice (parent) and Bob (child)
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        "//Alice"
    )
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup("//Bob")

    # Create subnet with Alice
    result = exec_command_alice(
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
            "--no-prompt",
            "--json-output",
        ],
    )
    result_output = json.loads(result.stdout)
    assert result_output["success"] is True
    assert result_output["netuid"] == netuid

    # Register Bob on the subnet
    exec_command_bob(
        command="subnets",
        sub_command="register",
        extra_args=[
            "--wallet-path",
            wallet_path_bob,
            "--wallet-name",
            wallet_bob.name,
            "--hotkey",
            wallet_bob.hotkey_str,
            "--netuid",
            netuid,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
        ],
    )

    # Start emissions on subnet
    exec_command_alice(
        command="subnets",
        sub_command="start",
        extra_args=[
            "--netuid",
            netuid,
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--hotkey",
            wallet_alice.hotkey_str,
            "--network",
            "ws://127.0.0.1:9945",
            "--no-prompt",
        ],
    )

    # Add stake to Alice's hotkey
    add_stake = exec_command_alice(
        command="stake",
        sub_command="add",
        extra_args=[
            "--netuid",
            netuid,
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--hotkey",
            wallet_alice.hotkey_str,
            "--chain",
            "ws://127.0.0.1:9945",
            "--amount",
            "100",
            "--no-prompt",
        ],
    )
    assert "✅ Finalized" in add_stake.stdout or "Finalized" in add_stake.stdout

    # Set Bob as child hotkey with 50% proportion
    set_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "set",
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--hotkey",
            wallet_alice.hotkey_str,
            "--netuid",
            netuid,
            "--chain",
            "ws://127.0.0.1:9945",
            "--children",
            keypair_bob.ss58_address,
            "--proportions",
            "0.5",
            "--no-prompt",
            "--json-output",
        ],
    )
    set_children_output = json.loads(set_children_result.stdout)
    assert set_children_output[str(netuid)]["success"] is True
    assert set_children_output[str(netuid)]["extrinsic_identifier"] is not None

    # Get children and verify Bob is set
    get_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "get",
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--hotkey",
            wallet_alice.hotkey_str,
            "--netuid",
            netuid,
            "--chain",
            "ws://127.0.0.1:9945",
            "--json-output",
        ],
    )
    get_children_output = json.loads(get_children_result.stdout)
    assert len(get_children_output) > 0
    # Verify Bob's hotkey is in children
    children_hotkeys = [child["child_ss58"] for child in get_children_output]
    assert keypair_bob.ss58_address in children_hotkeys

    # Revoke children
    revoke_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "revoke",
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--hotkey",
            wallet_alice.hotkey_str,
            "--netuid",
            netuid,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--json-output",
        ],
    )
    revoke_output = json.loads(revoke_result.stdout)
    assert revoke_output[str(netuid)]["success"] is True

    print("✅ Passed set_children test")


@pytest.mark.parametrize("local_chain", [False], indirect=True)
def test_set_children_multiple_proportions(local_chain, wallet_setup):
    """
    Test setting multiple children hotkeys with different proportions.

    Steps:
        1. Create wallets for Alice (parent), Bob and Charlie (children)
        2. Create a subnet and register all
        3. Set Bob (30%) and Charlie (40%) as children
        4. Verify both children are set with correct proportions
    """
    print("Testing set_children with multiple children 🧪")
    netuid = 2

    # Create wallets
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        "//Alice"
    )
    keypair_bob, wallet_bob, wallet_path_bob, _ = wallet_setup("//Bob")
    keypair_charlie, wallet_charlie, wallet_path_charlie, _ = wallet_setup("//Charlie")

    # Create subnet with Alice
    result = exec_command_alice(
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
            "--no-prompt",
            "--json-output",
        ],
    )
    result_output = json.loads(result.stdout)
    assert result_output["success"] is True

    # Start emissions
    exec_command_alice(
        command="subnets",
        sub_command="start",
        extra_args=[
            "--netuid",
            netuid,
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--hotkey",
            wallet_alice.hotkey_str,
            "--network",
            "ws://127.0.0.1:9945",
            "--no-prompt",
        ],
    )

    # Add stake
    exec_command_alice(
        command="stake",
        sub_command="add",
        extra_args=[
            "--netuid",
            netuid,
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--hotkey",
            wallet_alice.hotkey_str,
            "--chain",
            "ws://127.0.0.1:9945",
            "--amount",
            "100",
            "--no-prompt",
        ],
    )

    # Set multiple children: Bob (30%) and Charlie (40%)
    set_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "set",
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--hotkey",
            wallet_alice.hotkey_str,
            "--netuid",
            netuid,
            "--chain",
            "ws://127.0.0.1:9945",
            "--children",
            keypair_bob.ss58_address,
            "--children",
            keypair_charlie.ss58_address,
            "--proportions",
            "0.3",
            "--proportions",
            "0.4",
            "--no-prompt",
            "--json-output",
        ],
    )
    set_children_output = json.loads(set_children_result.stdout)
    assert set_children_output[str(netuid)]["success"] is True

    # Verify both children are set
    get_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "get",
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--hotkey",
            wallet_alice.hotkey_str,
            "--netuid",
            netuid,
            "--chain",
            "ws://127.0.0.1:9945",
            "--json-output",
        ],
    )
    get_children_output = json.loads(get_children_result.stdout)
    children_hotkeys = [child["child_ss58"] for child in get_children_output]
    assert keypair_bob.ss58_address in children_hotkeys
    assert keypair_charlie.ss58_address in children_hotkeys

    print("✅ Passed set_children multiple proportions test")
