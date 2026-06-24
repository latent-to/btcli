import json
import math
import time

from .utils import find_stake_entries

CHAIN = "ws://127.0.0.1:9945"
NETUID = 2


def _close(a, b):
    """Equal within tolerance (both values in rao)"""
    return math.isclose(a, b, rel_tol=0.0001, abs_tol=500)


def test_lock_roll_forward_comparison(local_chain, wallet_setup):
    """
    Test the accuracy of the lock roll-forward math between the chain and Btcli.

    Steps:
        0. Alice creates SN2 and starts its emission.
        1. Bob registers, stakes, then adds a DECAYING lock to his own hotkey.
           Compare stake list(Bob) vs conviction[Bob].
        2. Eve stakes to Bob's hotkey and adds a PERPETUAL lock to it.
           Compare stake list(Bob) + stake list(Eve) vs conviction[Bob].
    Note:
        - Stake list uses the chain's roll-forward math.
        - Subnets conviction uses Btcli's roll-forward math.
    """
    print("Testing lock roll-forward: chain vs client 🧪")

    _, wallet_alice, wallet_path_alice, exec_alice = wallet_setup("//Alice")
    _, wallet_bob, wallet_path_bob, exec_bob = wallet_setup("//Bob")
    _, wallet_eve, wallet_path_eve, exec_eve = wallet_setup("//Eve")

    bob_hotkey_ss58 = wallet_bob.hotkey.ss58_address

    # Create SN2 (Alice becomes its owner)
    create_subnet_result = exec_alice(
        command="subnets",
        sub_command="create",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--chain",
            CHAIN,
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
    assert create_subnet_payload["netuid"] == NETUID

    # Start SN2's emission schedule
    start_emission_result = exec_alice(
        command="subnets",
        sub_command="start",
        extra_args=[
            "--netuid",
            str(NETUID),
            "--wallet-name",
            wallet_alice.name,
            "--no-prompt",
            "--chain",
            CHAIN,
            "--wallet-path",
            wallet_path_alice,
        ],
    )
    assert (
        f"Successfully started subnet {NETUID}'s emission schedule."
        in start_emission_result.stdout
    )

    # Add initial stake on SN2
    initial_stake_result = exec_alice(
        command="stake",
        sub_command="add",
        extra_args=[
            "--netuid",
            str(NETUID),
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--hotkey",
            wallet_alice.hotkey_str,
            "--chain",
            CHAIN,
            "--amount",
            "1",
            "--unsafe",
            "--no-prompt",
            "--era",
            "144",
            "--no-mev-protection",
        ],
    )
    assert "✅ Finalized" in initial_stake_result.stdout, initial_stake_result.stderr

    # Register Bob on SN2
    register_bob_result = exec_bob(
        command="subnets",
        sub_command="register",
        extra_args=[
            "--netuid",
            str(NETUID),
            "--wallet-path",
            wallet_path_bob,
            "--wallet-name",
            wallet_bob.name,
            "--hotkey",
            wallet_bob.hotkey_str,
            "--chain",
            CHAIN,
            "--no-prompt",
        ],
    )
    assert "✅ Registered" in register_bob_result.stdout, register_bob_result.stderr

    # Bob stakes on SN2 so he has alpha to lock
    bob_stake_result = exec_bob(
        command="stake",
        sub_command="add",
        extra_args=[
            "--netuid",
            str(NETUID),
            "--wallet-path",
            wallet_path_bob,
            "--wallet-name",
            wallet_bob.name,
            "--hotkey",
            wallet_bob.hotkey_str,
            "--chain",
            CHAIN,
            "--amount",
            "100",
            "--unsafe",
            "--no-prompt",
            "--era",
            "144",
            "--no-mev-protection",
        ],
    )
    assert "✅ Finalized" in bob_stake_result.stdout, bob_stake_result.stderr

    # Case 1: Test Decaying lock accuracy

    # Read Bob's actual alpha on SN2 and lock half of it (price-independent)
    bob_stake_list_result = exec_bob(
        command="stake",
        sub_command="list",
        extra_args=[
            "--wallet-name",
            wallet_bob.name,
            "--wallet-path",
            wallet_path_bob,
            "--chain",
            CHAIN,
            "--no-prompt",
            "--verbose",
            "--json-output",
        ],
    )

    bob_stakes = find_stake_entries(
        json.loads(bob_stake_list_result.stdout),
        netuid=NETUID,
        hotkey_ss58=bob_hotkey_ss58,
    )
    bob_alpha = sum(entry["stake_value"] for entry in bob_stakes)
    assert bob_alpha > 0, "Bob has no alpha on SN2 to lock"

    bob_lock_amount = round(bob_alpha * 0.5, 4)
    bob_lock_result = exec_bob(
        command="lock",
        sub_command="add",
        extra_args=[
            "--netuid",
            str(NETUID),
            "--hotkey",
            bob_hotkey_ss58,
            "--amount",
            str(bob_lock_amount),
            "--mode",
            "decaying",
            "--wallet-name",
            wallet_bob.name,
            "--wallet-path",
            wallet_path_bob,
            "--chain",
            CHAIN,
            "--no-prompt",
            "--no-graph",
        ],
    )
    assert "Lock add succeeded" in bob_lock_result.stdout, bob_lock_result.stderr

    time.sleep(30)

    # Chain-rolled lock
    bob_locked_list_result = exec_bob(
        command="stake",
        sub_command="list",
        extra_args=[
            "--wallet-name",
            wallet_bob.name,
            "--wallet-path",
            wallet_path_bob,
            "--chain",
            CHAIN,
            "--no-prompt",
            "--verbose",
            "--json-output",
        ],
    )
    bob_chain_locked = max(
        (
            entry["locked_rao"]
            for entry in find_stake_entries(
                json.loads(bob_locked_list_result.stdout), netuid=NETUID
            )
        ),
        default=0,
    )

    # Btcli-rolled lock
    conviction_result = exec_bob(
        command="subnets",
        sub_command="conviction",
        extra_args=[
            "--netuid",
            str(NETUID),
            "--chain",
            CHAIN,
            "--verbose",
            "--json-output",
        ],
    )
    conviction_by_hotkey = {
        row["hotkey"]: row for row in json.loads(conviction_result.stdout)["rows"]
    }

    assert bob_hotkey_ss58 in conviction_by_hotkey, (
        "Bob missing from `subnets conviction`"
    )
    bob_conviction = conviction_by_hotkey[bob_hotkey_ss58]
    bob_client_locked = (
        bob_conviction["perpetual_alpha_rao"] + bob_conviction["decaying_alpha_rao"]
    )

    assert bob_chain_locked > 0, "Bob's chain-rolled lock should be non-zero"

    print(f"Bob's chain-rolled lock: {bob_chain_locked}")
    print(f"Bob's Btcli-rolled lock: {bob_client_locked}")
    diff_rao = abs(bob_chain_locked - bob_client_locked)
    diff_alpha = diff_rao / 1e9
    diff_pct = diff_rao / max(bob_chain_locked, bob_client_locked, 1) * 100
    print(f"Difference: {diff_rao} rao ≈ {diff_alpha:.8f} alpha ≈ {diff_pct:.6f}%")

    # Assert the chain's roll is equal or close to Btcli's roll
    assert _close(bob_chain_locked, bob_client_locked), (
        bob_chain_locked,
        bob_client_locked,
    )
    assert _close(bob_chain_locked, bob_conviction["decaying_alpha_rao"]), (
        bob_chain_locked,
        bob_conviction["decaying_alpha_rao"],
    )

    # Case 2: Test Perpetual lock accuracy

    # Eve delegates stake to Bob's hotkey, then perpetual-locks it to Bob's hotkey
    eve_stake_result = exec_eve(
        command="stake",
        sub_command="add",
        extra_args=[
            "--netuid",
            str(NETUID),
            "--wallet-path",
            wallet_path_eve,
            "--wallet-name",
            wallet_eve.name,
            "--include-hotkeys",
            bob_hotkey_ss58,
            "--chain",
            CHAIN,
            "--amount",
            "100",
            "--unsafe",
            "--no-prompt",
            "--era",
            "144",
            "--no-mev-protection",
        ],
    )
    assert "✅ Finalized" in eve_stake_result.stdout, eve_stake_result.stderr

    eve_stake_list_result = exec_eve(
        command="stake",
        sub_command="list",
        extra_args=[
            "--wallet-name",
            wallet_eve.name,
            "--wallet-path",
            wallet_path_eve,
            "--chain",
            CHAIN,
            "--no-prompt",
            "--verbose",
            "--json-output",
        ],
    )

    eve_stakes = find_stake_entries(
        json.loads(eve_stake_list_result.stdout),
        netuid=NETUID,
        hotkey_ss58=bob_hotkey_ss58,
    )
    eve_alpha = sum(entry["stake_value"] for entry in eve_stakes)
    assert eve_alpha > 0, "Eve has no alpha on Bob's hotkey to lock"

    # Lock half of Eve's alpha
    eve_lock_amount = round(eve_alpha * 0.5, 4)
    eve_lock_result = exec_eve(
        command="lock",
        sub_command="add",
        extra_args=[
            "--netuid",
            str(NETUID),
            "--hotkey",
            bob_hotkey_ss58,
            "--amount",
            str(eve_lock_amount),
            "--mode",
            "perpetual",
            "--wallet-name",
            wallet_eve.name,
            "--wallet-path",
            wallet_path_eve,
            "--chain",
            CHAIN,
            "--no-prompt",
            "--no-graph",
        ],
    )
    assert "Lock add succeeded" in eve_lock_result.stdout, eve_lock_result.stderr

    # Chain-rolled lock for Bob
    bob_locked_list_result = exec_bob(
        command="stake",
        sub_command="list",
        extra_args=[
            "--wallet-name",
            wallet_bob.name,
            "--wallet-path",
            wallet_path_bob,
            "--chain",
            CHAIN,
            "--no-prompt",
            "--verbose",
            "--json-output",
        ],
    )
    bob_chain_locked = max(
        (
            entry["locked_rao"]
            for entry in find_stake_entries(
                json.loads(bob_locked_list_result.stdout), netuid=NETUID
            )
        ),
        default=0,
    )

    eve_locked_list_result = exec_eve(
        command="stake",
        sub_command="list",
        extra_args=[
            "--wallet-name",
            wallet_eve.name,
            "--wallet-path",
            wallet_path_eve,
            "--chain",
            CHAIN,
            "--no-prompt",
            "--verbose",
            "--json-output",
        ],
    )
    eve_chain_locked = max(
        (
            entry["locked_rao"]
            for entry in find_stake_entries(
                json.loads(eve_locked_list_result.stdout), netuid=NETUID
            )
        ),
        default=0,
    )
    assert eve_chain_locked > 0, "Eve's chain-rolled lock should be non-zero"

    # Btcli-rolled lock for Bob's hotkey
    conviction_result = exec_bob(
        command="subnets",
        sub_command="conviction",
        extra_args=[
            "--netuid",
            str(NETUID),
            "--chain",
            CHAIN,
            "--verbose",
            "--json-output",
        ],
    )
    conviction_by_hotkey = {
        row["hotkey"]: row for row in json.loads(conviction_result.stdout)["rows"]
    }
    bob_conviction = conviction_by_hotkey[bob_hotkey_ss58]

    # Eve's perpetual mass lands in the perpetual bucket; Bob's in the decaying one
    assert _close(bob_conviction["perpetual_alpha_rao"], eve_chain_locked), (
        bob_conviction["perpetual_alpha_rao"],
        eve_chain_locked,
    )
    assert _close(bob_conviction["decaying_alpha_rao"], bob_chain_locked), (
        bob_conviction["decaying_alpha_rao"],
        bob_chain_locked,
    )

    # The per-hotkey aggregate (Btcli roll) equals the sum of the two coldkeys'
    # chain-rolled locks
    assert _close(
        bob_conviction["perpetual_alpha_rao"] + bob_conviction["decaying_alpha_rao"],
        bob_chain_locked + eve_chain_locked,
    ), (bob_conviction, bob_chain_locked, eve_chain_locked)

    print("Passed lock roll-forward: chain vs client")
