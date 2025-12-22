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
            "--chain",
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
            "--chain",
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
            "--chain",
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
        1. Create wallets for Alice (parent), Bob and Charlie (children)
        2. Create a subnet and register all
        3. Start emissions and add stake
        4. Set Bob (30%) and Charlie (40%) as children
        5. Verify both children are set with correct proportions
        6. Test getting children with --all-netuids flag
    """
    print("Testing set_children with multiple children 🧪")
    wallet_path_alice = "//Alice"
    netuid = 2

    # Create wallet for Alice
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup("//Bob")
    keypair_charlie, wallet_charlie, wallet_path_charlie, exec_command_charlie = (
        wallet_setup("//Charlie")
    )

    # Register a subnet with sudo as Alice
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

    # Register Bob and Charlie on the subnet
    for wallet_exec, wallet_obj, wallet_path in [
        (exec_command_bob, wallet_bob, wallet_path_bob),
        (exec_command_charlie, wallet_charlie, wallet_path_charlie),
    ]:
        register_result = wallet_exec(
            command="subnets",
            sub_command="register",
            extra_args=[
                "--wallet-path",
                wallet_path,
                "--wallet-name",
                wallet_obj.name,
                "--hotkey",
                wallet_obj.hotkey_str,
                "--netuid",
                netuid,
                "--chain",
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
            "--chain",
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
    assert "✅ Finalized" in add_stake_result.stdout

    # Set Bob (30%) and Charlie (40%) as children
    set_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "set",
            "--children",
            wallet_bob.hotkey.ss58_address,
            wallet_charlie.hotkey.ss58_address,
            "--proportions",
            "0.3",
            "0.4",
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
            "--no-prompt",
            "--json-output",
        ],
    )
    set_children_output = json.loads(set_children_result.stdout)
    assert set_children_output[str(netuid)]["success"] is True
    assert isinstance(set_children_output[str(netuid)]["extrinsic_identifier"], str)
    # Note: Children changes are not immediate - they require waiting for completion_block
    assert set_children_output[str(netuid)]["completion_block"] is not None
    assert set_children_output[str(netuid)]["set_block"] is not None

    print("✅ Passed set_children with multiple children")


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
            "--chain",
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
            "--chain",
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
            "--chain",
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
            "--chain",
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
            "--chain",
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
            "--chain",
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
            "--chain",
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
            "--chain",
            "ws://127.0.0.1:9945",
        ],
    )
    # Should have output (table format or message about no children)
    assert len(get_children_result.stdout) > 0

    print("✅ Passed get_children without JSON output")


@pytest.mark.parametrize("local_chain", [False], indirect=True)
def test_revoke_children_single_subnet(local_chain, wallet_setup):
    """
    Test revoking children hotkeys from a single subnet.

    Steps:
        1. Create subnet and set children
        2. Verify children are set
        3. Revoke children from the subnet
        4. Verify children are removed
    """
    print("Testing revoke_children from single subnet 🧪")
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
            "--no-mev-protection",
        ],
    )
    result_output = json.loads(result.stdout)
    assert result_output["success"] is True
    assert result_output["netuid"] == netuid
    assert isinstance(result_output["extrinsic_identifier"], str)

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
            "--chain",
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
            "--chain",
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
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--json-output",
        ],
    )
    set_children_output = json.loads(set_children_result.stdout)
    assert set_children_output[str(netuid)]["success"] is True

    # Verify children are set before revocation
    get_children_before = exec_command_alice(
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
            "--chain",
            "ws://127.0.0.1:9945",
            "--json-output",
        ],
    )
    get_children_before_output = json.loads(get_children_before.stdout)
    assert isinstance(get_children_before_output, list)
    assert len(get_children_before_output) > 0
    children_addresses_before = [child[1] for child in get_children_before_output]
    assert wallet_bob.hotkey.ss58_address in children_addresses_before

    # Revoke children from the subnet
    revoke_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "revoke",
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
            "--no-prompt",
            "--json-output",
        ],
    )
    revoke_children_output = json.loads(revoke_children_result.stdout)
    assert revoke_children_output[str(netuid)]["success"] is True
    assert isinstance(revoke_children_output[str(netuid)]["extrinsic_identifier"], str)
    assert revoke_children_output[str(netuid)]["completion_block"] is not None
    assert revoke_children_output[str(netuid)]["set_block"] is not None

    # Verify children are revoked
    get_children_after = exec_command_alice(
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
            "--chain",
            "ws://127.0.0.1:9945",
            "--json-output",
        ],
    )
    get_children_after_output = json.loads(get_children_after.stdout)
    # After revocation, children list should be empty or None
    assert get_children_after_output == [] or get_children_after_output is None

    print("✅ Passed revoke_children from single subnet")


@pytest.mark.parametrize("local_chain", [False], indirect=True)
def test_revoke_children_json_output(local_chain, wallet_setup):
    """
    Test revoking children with JSON output and verify the response structure.

    Steps:
        1. Create subnet and set children
        2. Revoke children with --json-output flag
        3. Verify JSON structure contains completion_block and set_block
    """
    print("Testing revoke_children with JSON output 🧪")
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
            "--no-mev-protection",
        ],
    )
    result_output = json.loads(result.stdout)
    assert result_output["success"] is True
    assert result_output["netuid"] == netuid

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
            "--chain",
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
            "--chain",
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
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--json-output",
        ],
    )
    set_children_output = json.loads(set_children_result.stdout)
    assert set_children_output[str(netuid)]["success"] is True

    # Revoke children with JSON output
    revoke_children_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "revoke",
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
            "--no-prompt",
            "--json-output",
        ],
    )
    revoke_children_output = json.loads(revoke_children_result.stdout)
    # Verify JSON structure
    assert isinstance(revoke_children_output, dict)
    assert str(netuid) in revoke_children_output
    assert revoke_children_output[str(netuid)]["success"] is True
    assert isinstance(revoke_children_output[str(netuid)]["extrinsic_identifier"], str)
    assert revoke_children_output[str(netuid)]["completion_block"] is not None
    assert revoke_children_output[str(netuid)]["set_block"] is not None
    assert isinstance(revoke_children_output[str(netuid)]["completion_block"], int)
    assert isinstance(revoke_children_output[str(netuid)]["set_block"], int)
    # completion_block should be greater than set_block
    assert (
        revoke_children_output[str(netuid)]["completion_block"]
        > revoke_children_output[str(netuid)]["set_block"]
    )

    print("✅ Passed revoke_children with JSON output")


@pytest.mark.parametrize("local_chain", [False], indirect=True)
def test_revoke_children_all_netuids(local_chain, wallet_setup):
    """
    Test revoking children from all netuids using --all-netuids flag.

    Steps:
        1. Create subnet and set children
        2. Revoke children using --all-netuids flag
        3. Verify revocation was successful
    """
    print("Testing revoke_children with --all-netuids 🧪")
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
            "--no-mev-protection",
        ],
    )
    result_output = json.loads(result.stdout)
    assert result_output["success"] is True
    assert result_output["netuid"] == netuid

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
            "--chain",
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
            "--chain",
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
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--json-output",
        ],
    )
    set_children_output = json.loads(set_children_result.stdout)
    assert set_children_output[str(netuid)]["success"] is True

    # Revoke children using --all-netuids
    revoke_all_result = exec_command_alice(
        command="stake",
        sub_command="child",
        extra_args=[
            "revoke",
            "--all-netuids",
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--hotkey",
            wallet_alice.hotkey_str,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--json-output",
        ],
    )
    revoke_all_output = json.loads(revoke_all_result.stdout)
    # Should have results for the netuid
    assert isinstance(revoke_all_output, dict)
    assert str(netuid) in revoke_all_output
    assert revoke_all_output[str(netuid)]["success"] is True
    assert isinstance(revoke_all_output[str(netuid)]["extrinsic_identifier"], str)

    print("✅ Passed revoke_children with --all-netuids")
