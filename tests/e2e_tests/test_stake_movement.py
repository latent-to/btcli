import asyncio
import json
import pytest

from .utils import find_stake_entries, set_storage_extrinsic


@pytest.mark.parametrize("local_chain", [False], indirect=True)
def test_stake_movement(local_chain, wallet_setup):
    """
    Exercise stake move, transfer, and swap flows across subnets using Alice and Bob.

    Steps:
        0. Initial setup: Make alice own SN 0, create SN2, SN3, SN4, start emissions on all subnets.
        1. Activation: Register Bob on subnets 2 and 3; add initial stake for V3 activation.
        2. Move: Move stake from Alice's hotkey on netuid 2 to Bob's hotkey on netuid 3.
        3. Transfer: Transfer all root (netuid 0) stake from Alice's coldkey to Bob's coldkey.
        4. Swap: Swap Alice's stake from netuid 4 to the root netuid.

    Note:
        - All movement commands executed with mev shield
        - Stake commands executed without shield to speed up tests
        - Shield for stake commands is already covered in its own test
    """
    print("Testing stake movement commands 🧪")

    wallet_path_alice = "//Alice"
    wallet_path_bob = "//Bob"

    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup(
        wallet_path_bob
    )

    # Force Alice to own SN0 by setting storage
    sn0_owner_storage_items = [
        (
            bytes.fromhex(
                "658faa385070e074c85bf6b568cf055536e3e82152c8758267395fe524fbbd160000"
            ),
            bytes.fromhex(
                "d43593c715fdd31c61141abd04a99fd6822c8558854ccde39a5684e7a56da27d"
            ),
        )
    ]
    asyncio.run(
        set_storage_extrinsic(
            local_chain,
            wallet=wallet_alice,
            items=sn0_owner_storage_items,
        )
    )

    # Create SN2, SN3, SN4 for move/transfer/swap checks
    subnets_to_create = [2, 3, 4]
    for netuid in subnets_to_create:
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
                "A test subnet for e2e testing",
                "--additional-info",
                "Created by Alice",
                "--logo-url",
                "https://testsubnet.com/logo.png",
                "--no-prompt",
                "--json-output",
            ],
        )
        create_subnet_payload = json.loads(create_subnet_result.stdout)
        assert create_subnet_payload["success"] is True
        assert create_subnet_payload["netuid"] == netuid

    # Start emission schedule for subnets (including root netuid 0)
    for netuid in [0] + subnets_to_create:
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

    # Alice is already registered - register Bob on the two non-root subnets
    for netuid in [2, 3]:
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
        assert "Your extrinsic has been included" in register_bob_result.stdout, (
            register_bob_result.stdout
        )

    # Add initial stake to enable V3 (1 TAO) on all created subnets
    for netuid in [2, 3, 4]:
        add_initial_stake_result = exec_command_alice(
            command="stake",
            sub_command="add",
            extra_args=[
                "--netuid",
                str(netuid),
                "--wallet-path",
                wallet_path_alice,
                "--wallet-name",
                wallet_alice.name,
                "--hotkey",
                wallet_alice.hotkey_str,
                "--chain",
                "ws://127.0.0.1:9945",
                "--amount",
                "1",
                "--unsafe",
                "--no-prompt",
                "--era",
                "144",
                "--no-mev-protection",
            ],
        )
        assert "✅ Finalized" in add_initial_stake_result.stdout, (
            add_initial_stake_result.stderr
        )

    ############################
    # TEST 1: Move stake command
    # Move stake between hotkeys while keeping the same coldkey
    ############################

    # Add 25 TAO stake for move test for Alice
    add_move_stake_result = exec_command_alice(
        command="stake",
        sub_command="add",
        extra_args=[
            "--netuid",
            "2",
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--hotkey",
            wallet_alice.hotkey_str,
            "--chain",
            "ws://127.0.0.1:9945",
            "--amount",
            "25",
            "--no-prompt",
            "--era",
            "144",
            "--unsafe",
            "--no-mev-protection",
        ],
    )
    assert "✅ Finalized" in add_move_stake_result.stdout, add_move_stake_result.stderr

    # List Alice's stakes prior to the move
    alice_stake_before_move = exec_command_alice(
        command="stake",
        sub_command="list",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--verbose",
            "--json-output",
        ],
    )

    # Check Alice's stakes before move to ensure sufficient stake on netuid 2
    alice_stake_list_before_move = json.loads(alice_stake_before_move.stdout)
    alice_stakes_before_move = find_stake_entries(
        alice_stake_list_before_move,
        netuid=2,
        hotkey_ss58=wallet_alice.hotkey.ss58_address,
    )
    for stake in alice_stakes_before_move:
        assert stake["stake_value"] >= int(20)

    # Move stake from Alice's hotkey on netuid 2 -> Bob's hotkey on netuid 3
    move_amount = 20
    move_result = exec_command_alice(
        command="stake",
        sub_command="move",
        extra_args=[
            "--origin-netuid",
            "2",
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--dest-netuid",
            "3",
            "--dest",
            wallet_bob.hotkey.ss58_address,
            "--amount",
            move_amount,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
        ],
    )
    assert "✅ Sent" in move_result.stdout

    # Check Alice's stakes after move
    alice_stake_after_move = exec_command_alice(
        command="stake",
        sub_command="list",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--verbose",
            "--json-output",
        ],
    )
    # Assert stake was moved from Alice's hotkey on netuid 2 -> Bob's hotkey on netuid 3
    alice_stake_list_after_move = json.loads(alice_stake_after_move.stdout)
    bob_stakes_after_move = find_stake_entries(
        alice_stake_list_after_move,
        netuid=3,
        hotkey_ss58=wallet_bob.hotkey.ss58_address,
    )
    for stake in bob_stakes_after_move:
        assert stake["stake_value"] >= move_amount

    ################################
    # TEST 2: Transfer stake command
    # Transfer stake between coldkeys while keeping the same hotkey
    ################################

    transfer_amount = 20
    transfer_fund_root_result = exec_command_alice(
        command="stake",
        sub_command="add",
        extra_args=[
            "--netuid",
            "0",
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--hotkey",
            wallet_alice.hotkey_str,
            "--chain",
            "ws://127.0.0.1:9945",
            "--amount",
            transfer_amount,
            "--no-prompt",
            "--era",
            "144",
            "--unsafe",
            "--no-mev-protection",
        ],
    )
    assert "✅ Finalized" in transfer_fund_root_result.stdout, (
        transfer_fund_root_result.stderr
    )

    # Ensure Bob doesn't have any stake in root netuid before transfer
    bob_stake_list_before_transfer = exec_command_bob(
        command="stake",
        sub_command="list",
        extra_args=[
            "--wallet-path",
            wallet_path_bob,
            "--wallet-name",
            wallet_bob.name,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--verbose",
            "--json-output",
        ],
    )
    assert bob_stake_list_before_transfer.stdout == ""

    # Transfer stake from Alice's coldkey on netuid 0 -> Bob's coldkey on netuid 0
    transfer_result = exec_command_alice(
        command="stake",
        sub_command="transfer",
        extra_args=[
            "--origin-netuid",
            "0",
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--dest-netuid",
            "0",
            "--dest",
            wallet_bob.coldkeypub.ss58_address,
            "--all",
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
        ],
    )
    assert "✅ Sent" in transfer_result.stdout

    # Check Bob's stakes after transfer
    bob_stake_list_after_transfer = exec_command_bob(
        command="stake",
        sub_command="list",
        extra_args=[
            "--wallet-path",
            wallet_path_bob,
            "--wallet-name",
            wallet_bob.name,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--verbose",
            "--json-output",
        ],
    )
    bob_stake_list_after_transfer = json.loads(bob_stake_list_after_transfer.stdout)
    bob_stakes_after_transfer = find_stake_entries(
        bob_stake_list_after_transfer,
        netuid=0,
    )
    for stake in bob_stakes_after_transfer:
        assert stake["stake_value"] >= transfer_amount

    # Check Alice's stakes after transfer
    alice_stake_list_after_transfer = exec_command_alice(
        command="stake",
        sub_command="list",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--verbose",
            "--json-output",
        ],
    )

    alice_stake_list_after_transfer = json.loads(alice_stake_list_after_transfer.stdout)
    alice_stakes_after_transfer = find_stake_entries(
        alice_stake_list_after_transfer,
        netuid=0,
    )
    if alice_stakes_after_transfer:
        pytest.fail("Stake found in root netuid after transfer")

    ################################
    # TEST 3: Swap stake command
    # Swap stake between subnets while keeping the same coldkey-hotkey pair
    ################################

    swap_seed_stake_result = exec_command_alice(
        command="stake",
        sub_command="add",
        extra_args=[
            "--netuid",
            "4",
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--hotkey",
            wallet_alice.hotkey_str,
            "--chain",
            "ws://127.0.0.1:9945",
            "--amount",
            "25",
            "--no-prompt",
            "--era",
            "144",
            "--unsafe",
            "--no-mev-protection",
        ],
    )
    assert "✅ Finalized" in swap_seed_stake_result.stdout, (
        swap_seed_stake_result.stderr
    )

    # Ensure stake was added to Alice's hotkey on netuid 4
    alice_stake_list_before_swap_cmd = exec_command_alice(
        command="stake",
        sub_command="list",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--verbose",
            "--json-output",
        ],
    )

    alice_stake_list_before_swap = json.loads(alice_stake_list_before_swap_cmd.stdout)
    alice_stakes_before_swap = find_stake_entries(
        alice_stake_list_before_swap,
        netuid=4,
    )
    if not alice_stakes_before_swap:
        pytest.fail("Stake not found in netuid 4 before swap")

    # Swap stake from Alice's hotkey on netuid 4 -> netuid 0
    # Without safe staking
    swap_result = exec_command_alice(
        command="stake",
        sub_command="swap",
        extra_args=[
            "--origin-netuid",
            "4",
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--dest-netuid",
            "0",
            "--all",
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--unsafe",
        ],
    )
    assert "✅ Sent" in swap_result.stdout, swap_result.stderr

    # Check Alice's stakes after swap
    alice_stake_list_after_swap_cmd = exec_command_alice(
        command="stake",
        sub_command="list",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--verbose",
            "--json-output",
        ],
    )

    alice_stake_list_after_swap = json.loads(alice_stake_list_after_swap_cmd.stdout)
    alice_stakes_after_swap = find_stake_entries(
        alice_stake_list_after_swap,
        netuid=4,
    )
    if alice_stakes_after_swap:
        pytest.fail("Stake found in netuid 4 after swap")

    # Swap stake from Alice's hotkey back on netuid 0 -> netuid 4
    # With safe staking
    swap_with_limit_result = exec_command_alice(
        command="stake",
        sub_command="swap",
        extra_args=[
            "--origin-netuid",
            "0",
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--dest-netuid",
            "4",
            "--all",
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--unsafe",
        ],
    )
    assert "✅ Sent" in swap_with_limit_result.stdout, swap_with_limit_result.stderr

    # Check Alice's stakes after swap with limit
    alice_stake_list_after_swap_limit_cmd = exec_command_alice(
        command="stake",
        sub_command="list",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--verbose",
            "--json-output",
        ],
    )

    alice_stake_list_after_swap_with_limit = json.loads(
        alice_stake_list_after_swap_limit_cmd.stdout
    )
    alice_stakes_after_swap_with_limit = find_stake_entries(
        alice_stake_list_after_swap_with_limit,
        netuid=0,
    )
    if alice_stakes_after_swap_with_limit:
        pytest.fail("Stake found in netuid 0 after swap with limit")

    print("Passed stake movement commands")
