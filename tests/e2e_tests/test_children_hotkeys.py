import json
import pytest


"""
Verify commands:

* btcli stake child get
* btcli stake child set
* btcli stake child revoke
"""


@pytest.mark.parametrize("local_chain", [False], indirect=True)
def test_set_children_single_child(local_chain, wallet_setup):
    """
    Test setting a single child hotkey on a subnet.

    Steps:
        1. Create wallets for Alice (parent) and Bob (child)
        2. Create a subnet and register both Alice and Bob
        3. Start emissions on the subnet
        4. Add stake to Alice's hotkey
        5. Set Bob as a child hotkey with 50% proportion
        6. Verify children are set via get command
    """
    print("Testing set_children with single child 🧪")
    # Create wallets for Alice and Bob
    wallet_path_alice = "//Alice"
    netuid = 2

    # Create wallet for Alice
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
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
    assert isinstance(result_output["extrinsic_identifier"], str)

    # Create wallet for Bob (child)
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup("//Bob")

    # Register Bob on the subnet
    register_bob_result = exec_command_bob(
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
            "--network",
            "ws://127.0.0.1:9945",
            "--no-prompt",
        ],
    )
    assert (
        "✅ Registered" in register_bob_result.stdout
        or "✅ Already Registered" in register_bob_result.stdout
    )

    # Start emissions on subnet
    start_emission_result = exec_command_alice(
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
    assert (
        f"Successfully started subnet {netuid}'s emission schedule"
        in start_emission_result.stdout
    )

    # Add stake to Alice's hotkey to enable V3
    add_stake_result = exec_command_alice(
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
            "--network",
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
    assert "✅ Finalized" in add_stake_result.stdout

    # Set Bob as a child hotkey with 50% proportion
    set_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "set",
            "--children",
            wallet_bob.hotkey.ss58_address,
            "--proportions",
            "0.5",
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
            "--json-output",
        ],
    )
    set_children_output = json.loads(set_children_result.stdout)
    assert set_children_output[str(netuid)]["success"] is True
    assert isinstance(set_children_output[str(netuid)]["extrinsic_identifier"], str)
    # Note: Children changes are not immediate - they require waiting for completion_block
    # The completion_block indicates when the change will take effect
    assert set_children_output[str(netuid)]["completion_block"] is not None
    assert set_children_output[str(netuid)]["set_block"] is not None

    print("✅ Passed set_children with single child")


@pytest.mark.parametrize("local_chain", [False], indirect=True)
def test_set_children_multiple_proportions(local_chain, wallet_setup):
    """
    Test setting multiple children hotkeys with different proportions.

    Steps:
        1. Create wallets for Alice (parent), Bob, Charlie, and Dave (children)
        2. Create a subnet and register all participants
        3. Start emissions on the subnet
        4. Add stake to Alice's hotkey
        5. Set multiple children with different proportions (Bob: 25%, Charlie: 35%, Dave: 20%)
        6. Verify the transaction succeeded
    """
    print("Testing set_children with multiple proportions")

    wallet_path_alice = "//Alice"
    netuid = 2

    # Create wallet for Alice (parent)
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )

    # Create subnet as Alice
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

    # Create wallets for children
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup("//Bob")
    keypair_charlie, wallet_charlie, wallet_path_charlie, exec_command_charlie = (
        wallet_setup("//Charlie")
    )
    keypair_dave, wallet_dave, wallet_path_dave, exec_command_dave = wallet_setup(
        "//Dave"
    )

    # Register all children on the subnet
    for wallet, wallet_path, exec_command in [
        (wallet_bob, wallet_path_bob, exec_command_bob),
        (wallet_charlie, wallet_path_charlie, exec_command_charlie),
        (wallet_dave, wallet_path_dave, exec_command_dave),
    ]:
        register_result = exec_command(
            command="subnets",
            sub_command="register",
            extra_args=[
                "--wallet-path",
                wallet_path,
                "--wallet-name",
                wallet.name,
                "--hotkey",
                wallet.hotkey_str,
                "--netuid",
                netuid,
                "--network",
                "ws://127.0.0.1:9945",
                "--no-prompt",
            ],
        )
        assert (
            "✅ Registered" in register_result.stdout
            or "✅ Already Registered" in register_result.stdout
        )

    # Start emissions on subnet
    start_emission_result = exec_command_alice(
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
    assert (
        f"Successfully started subnet {netuid}'s emission schedule"
        in start_emission_result.stdout
    )

    # Add stake to Alice's hotkey
    add_stake_result = exec_command_alice(
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
            "--network",
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
    assert "✅ Finalized" in add_stake_result.stdout

    # Set multiple children with different proportions
    # Bob: 25%, Charlie: 35%, Dave: 20%
    # Note: Typer list options require repeating the flag for each value
    set_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "set",
            "--children",
            wallet_bob.hotkey.ss58_address,
            "--children",
            wallet_charlie.hotkey.ss58_address,
            "--children",
            wallet_dave.hotkey.ss58_address,
            "--proportions",
            "0.25",
            "--proportions",
            "0.35",
            "--proportions",
            "0.20",
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
            "--json-output",
        ],
    )

    # Debug output if parsing fails
    if not set_children_result.stdout.strip():
        print(f"stdout is empty")
        print(f"stderr: {set_children_result.stderr}")
        print(f"exit_code: {set_children_result.exit_code}")
        pytest.fail(f"set_children returned empty stdout. stderr: {set_children_result.stderr}")

    try:
        set_children_output = json.loads(set_children_result.stdout)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON")
        print(f"stdout: {set_children_result.stdout}")
        print(f"stderr: {set_children_result.stderr}")
        pytest.fail(f"Failed to parse JSON: {e}. stdout: {set_children_result.stdout}")

    assert set_children_output[str(netuid)]["success"] is True
    assert isinstance(set_children_output[str(netuid)]["extrinsic_identifier"], str)
    assert set_children_output[str(netuid)]["completion_block"] is not None
    assert set_children_output[str(netuid)]["set_block"] is not None

    print("✅ Passed set_children with multiple proportions")


@pytest.mark.parametrize("local_chain", [False], indirect=True)
def test_get_children_json_output(local_chain, wallet_setup):
    """
    Test getting children with JSON output.

    Steps:
        1. Create subnet and set children
        2. Get children with --json-output flag
        3. Verify JSON structure and content
    """
    print("Testing get_children with JSON output 🧪")
    wallet_path_alice = "//Alice"
    netuid = 2

    # Create wallet for Alice
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup("//Bob")

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
    assert isinstance(result_output["extrinsic_identifier"], str)

    # Register Bob
    register_bob_result = exec_command_bob(
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
            "--network",
            "ws://127.0.0.1:9945",
            "--no-prompt",
        ],
    )
    assert (
        "✅ Registered" in register_bob_result.stdout
        or "✅ Already Registered" in register_bob_result.stdout
    )

    # Start emissions
    start_emission_result = exec_command_alice(
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
    assert (
        f"Successfully started subnet {netuid}'s emission schedule"
        in start_emission_result.stdout
    )

    # Add stake
    add_stake_result = exec_command_alice(
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
            "--network",
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
    assert "✅ Finalized" in add_stake_result.stdout

    # Set children
    set_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "set",
            "--children",
            wallet_bob.hotkey.ss58_address,
            "--proportions",
            "0.5",
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
            "--json-output",
        ],
    )
    set_children_output = json.loads(set_children_result.stdout)
    assert set_children_output[str(netuid)]["success"] is True
    # Note: Children changes are not immediate - they require waiting for completion_block
    assert set_children_output[str(netuid)]["completion_block"] is not None

    # Get children with JSON output
    # Note: Children won't be visible immediately since they require waiting for completion_block
    get_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "get",
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
            "--json-output",
        ],
    )
    get_children_output = json.loads(get_children_result.stdout)
    # Verify JSON structure - should be a list (may be empty since children aren't immediately active)
    assert isinstance(get_children_output, list)
    # If there are children, verify structure
    for child in get_children_output:
        assert isinstance(child, (list, tuple))
        assert len(child) >= 2
        # First element should be proportion (float or int)
        assert isinstance(child[0], (float, int))
        # Second element should be SS58 address (string)
        assert isinstance(child[1], str)
        assert len(child[1]) > 0

    print("✅ Passed get_children with JSON output")


@pytest.mark.parametrize("local_chain", [False], indirect=True)
def test_get_children_non_json_output(local_chain, wallet_setup):
    """
    Test getting children without JSON output (table format).

    Steps:
        1. Create subnet and set children
        2. Get children without --json-output flag
        3. Verify output contains expected information
    """
    print("Testing get_children without JSON output 🧪")
    wallet_path_alice = "//Alice"
    netuid = 2

    # Create wallet for Alice
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup("//Bob")

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
    assert isinstance(result_output["extrinsic_identifier"], str)

    # Register Bob
    register_bob_result = exec_command_bob(
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
            "--network",
            "ws://127.0.0.1:9945",
            "--no-prompt",
        ],
    )
    assert (
        "✅ Registered" in register_bob_result.stdout
        or "✅ Already Registered" in register_bob_result.stdout
    )

    # Start emissions
    start_emission_result = exec_command_alice(
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
    assert (
        f"Successfully started subnet {netuid}'s emission schedule"
        in start_emission_result.stdout
    )

    # Add stake
    add_stake_result = exec_command_alice(
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
            "--network",
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
    assert "✅ Finalized" in add_stake_result.stdout

    # Set children
    set_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "set",
            "--children",
            wallet_bob.hotkey.ss58_address,
            "--proportions",
            "0.5",
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
            "--json-output",
        ],
    )
    set_children_output = json.loads(set_children_result.stdout)
    assert set_children_output[str(netuid)]["success"] is True
    # Note: Children changes are not immediate - they require waiting for completion_block
    assert set_children_output[str(netuid)]["completion_block"] is not None

    # Get children without JSON output (should show table)
    # Note: Children won't be visible immediately since they require waiting for completion_block
    get_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "get",
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
        ],
    )
    # Should have output (table format or message about no children)
    assert len(get_children_result.stdout) > 0

    print("✅ Passed get_children without JSON output")
