from time import sleep

from bittensor_cli.src.bittensor.balances import Balance
from .utils import (
    extract_coldkey_balance,
    validate_wallet_inspect,
    validate_wallet_overview,
    verify_subnet_entry,
)

"""
Verify commands:

* btcli subnets create
* btcli subnets register
* btcli subnets list
* btcli w inspect
* btcli w overview
* btcli w balance
* btcli w transfer
* btcli w set-identity
* btcli w get-identity
* btcli w sign
"""


def test_wallet_overview_inspect(local_chain, wallet_setup):
    """
    Test the overview and inspect commands of the wallet by interaction with subnets

    Steps:
        1. Create wallet for Alice
        2. Create a subnet, execute subnet list and verify subnet creation
        3. Register Alice in the subnet and extract her balance
        4. Execute wallet overview, inspect and assert correct data is displayed

    Raises:
        AssertionError: If any of the checks or verifications fail
    """
    print("Testing wallet overview, inspect command 🧪")
    netuid = 2
    wallet_path_name = "//Alice"

    # Create wallet for Alice
    keypair, wallet, wallet_path, exec_command = wallet_setup(wallet_path_name)

    # Register a subnet with sudo as Alice
    result = exec_command(
        command="subnets",
        sub_command="create",
        extra_args=[
            "--wallet-path",
            wallet_path,
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            wallet.name,
            "--wallet-hotkey",
            wallet.hotkey_str,
            "--subnet-name",
            "Test Subnet",
            "--repo",
            "https://github.com/username/repo",
            "--contact",
            "test@opentensor.dev",
            "--url",
            "https://testsubnet.com",
            "--discord",
            "test#1234",
            "--description",
            "A test subnet for e2e testing",
            "--logo-url",
            "https://testsubnet.com/logo.png",
            "--additional-info",
            "Test subnet",
            "--no-prompt",
            "--no-mev-protection",
        ],
    )
    assert f"✅ Registered subnetwork with netuid: {netuid}" in result.stdout

    sleep(3)

    # List all the subnets in the network
    subnets_list = exec_command(
        command="subnets",
        sub_command="list",
        extra_args=["--chain", "ws://127.0.0.1:9945"],
    )

    sleep(3)

    # Assert using regex that the subnet is visible in subnets list
    assert verify_subnet_entry(subnets_list.stdout, netuid, keypair.ss58_address)

    # Check balance of Alice after registering to the subnet
    wallet_balance = exec_command(
        command="wallet",
        sub_command="balance",
        extra_args=[
            "--wallet-path",
            wallet_path,
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            wallet.name,
        ],
    )

    # Assert correct address is displayed
    assert keypair.ss58_address in wallet_balance.stdout

    # Extract balance left after creating and registering into the subnet
    balance = extract_coldkey_balance(
        wallet_balance.stdout,
        wallet_name=wallet.name,
        coldkey_address=wallet.coldkey.ss58_address,
    )

    # Execute wallet overview command.
    wallet_overview = exec_command(
        command="wallet",
        sub_command="overview",
        extra_args=[
            "--wallet-path",
            wallet_path,
            "--wallet-name",
            wallet.name,
            "--chain",
            "ws://127.0.0.1:9945",
        ],
    )

    # Assert correct entry is present in wallet overview
    assert validate_wallet_overview(
        output=wallet_overview.stdout,
        uid=0,  # Since Alice was the first one, she has uid = 0
        coldkey=wallet.name,
        hotkey=wallet.hotkey_str,
        hotkey_ss58=keypair.ss58_address,
        axon_active=False,  # Axon is not active until we run validator/miner
    )

    # TODO: Re-enable this once inspect is ported over
    if False:
        # Execute wallet inspect command
        inspect = exec_command(
            command="wallet",
            sub_command="inspect",
            extra_args=[
                "--wallet-path",
                wallet_path,
                "--wallet-name",
                wallet.name,
                "--chain",
                "ws://127.0.0.1:9945",
            ],
        )

        # Assert correct entry is present in wallet inspect
        assert validate_wallet_inspect(
            inspect.stdout,
            coldkey=wallet.name,
            balance=Balance.from_tao(balance["free_balance"]),
            delegates=None,  # We have not delegated anywhere yet
            hotkeys_netuid=[
                (1, f"default-{wallet.hotkey.ss58_address}", 0, False)
            ],  # (netuid, hotkey-display, stake, check_emissions)
        )
    print("Passed wallet overview command ✅")


def test_wallet_transfer(local_chain, wallet_setup):
    """
    Test the transfer and balance functionality in the Bittensor network.

    Steps:
        1. Create wallets for Alice and Bob with initial balance already
        2. Ensure initial balance is displayed correctly
        3. Transfer 100 TAO from Alice to Bob
        4. Assert amount was transferred along with transfer tolerance
        5. Assert transfer fails with no balance for Anakin

    Raises:
        AssertionError: If any of the checks or verifications fail
    """
    print("Testing wallet transfer, balance command 🧪")
    wallet_path_alice = "//Alice"
    wallet_path_bob = "//Bob"

    # Create wallets for Alice and Bob
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup(
        wallet_path_bob
    )

    # Both Alice and Bob have initial balance through the local chain
    alice_bob_initial_balance = Balance.from_tao(1_000_000)

    # Check balance of Alice
    result = exec_command_alice(
        command="wallet",
        sub_command="balance",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            "default",
        ],
    )

    # Assert correct address is displayed
    assert keypair_alice.ss58_address in result.stdout

    # Assert correct initial balance is shown
    initial_balance = Balance.from_tao(
        extract_coldkey_balance(
            result.stdout,
            wallet_name=wallet_alice.name,
            coldkey_address=wallet_alice.coldkey.ss58_address,
        )["free_balance"]
    )
    assert initial_balance == alice_bob_initial_balance

    # Transfer of 100 tao for this test
    expected_transfer = Balance.from_tao(100)

    # Execute the transfer command, with Bob's address as destination
    result = exec_command_alice(
        command="wallet",
        sub_command="transfer",
        extra_args=[
            "--dest",
            keypair_bob.ss58_address,
            "--wallet-path",
            wallet_path_alice,
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            "default",
            "--amount",
            "100",
            "--no-prompt",
            "--era",
            "144",
        ],
    )

    # To-do: Assert correct output once transfer is fixed

    # Check balance of Alice after transfer
    result = exec_command_alice(
        command="wallet",
        sub_command="balance",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            "default",
        ],
    )

    # Extract balance after the transfer
    balance_remaining = Balance.from_tao(
        extract_coldkey_balance(
            result.stdout,
            wallet_name=wallet_alice.name,
            coldkey_address=wallet_alice.coldkey.ss58_address,
        )["free_balance"]
    )

    tolerance = Balance.from_rao(600_000)  # Tolerance for transaction fee
    balance_difference = initial_balance - balance_remaining

    # Assert transfer was successful w.r.t tolerance
    assert expected_transfer <= balance_difference <= expected_transfer + tolerance

    # Check balance of Bob after transfer
    result = exec_command_bob(
        command="wallet",
        sub_command="balance",
        extra_args=[
            "--wallet-path",
            wallet_path_bob,
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            "default",
        ],
    )

    # Extract Bob's balance from output
    new_balance_bob = Balance.from_tao(
        extract_coldkey_balance(
            result.stdout,
            wallet_name=wallet_bob.name,
            coldkey_address=wallet_bob.coldkey.ss58_address,
        )["free_balance"]
    )

    # Assert correct balance was transferred from Bob
    assert alice_bob_initial_balance + expected_transfer == new_balance_bob

    wallet_path_anakin = "//Anakin"
    keypair_anakin, wallet_anakin, wallet_path_anakin, exec_command_anakin = (
        wallet_setup(wallet_path_anakin)
    )

    # Attempt transferring to Alice
    result = exec_command_anakin(
        command="wallet",
        sub_command="transfer",
        extra_args=[
            "--dest",
            keypair_alice.ss58_address,
            "--wallet-path",
            wallet_path_anakin,
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            "default",
            "--amount",
            "100",
            "--no-prompt",
        ],
    )

    # This transfer is expected to fail due to low balance
    assert "❌ Not enough balance" in result.stderr
    print("✅Passed wallet transfer, balance command")


def test_wallet_identities(local_chain, wallet_setup):
    """
    Test setting ids & fetching ids in the network, and signing using wallets.

    Steps:
        1. Create a wallet for Alice
        2. Set the network identity for Alice using pre-defined values
        3. Assert id was set successfully and all values are correct
        4. Fetch the id using hotkey and assert all values are correct
        5. Sign a message using wallet's hotkey
        5. Sign a message using wallet's coldkey

    Raises:
        AssertionError: If any of the checks or verifications fail
    """
    print("Testing wallet set-id, get-id, sign command 🧪")

    netuid = 2
    wallet_path_alice = "//Alice"

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
            "--no-mev-protection",
        ],
    )
    assert f"✅ Registered subnetwork with netuid: {netuid}" in result.stdout

    # Define values for Alice's identity
    alice_identity = {
        "name": "Alice OTF",
        "url": "https://bittensor.com/",
        "image": "https://bittensor.com/img/dark-Bittensor.svg",
        "discord": "alice#1234",
        "description": "I am a tester for OTF",
        "additional": "Lead Developer",
        "github_repo": "https://github.com/opentensor/bittensor",
    }

    # Execute btcli set-identity command
    set_id = exec_command_alice(
        command="wallet",
        sub_command="set-identity",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--id-name",
            alice_identity["name"],
            "--web-url",
            alice_identity["url"],
            "--image-url",
            alice_identity["image"],
            "--discord",
            alice_identity["discord"],
            "--description",
            alice_identity["description"],
            "--additional",
            alice_identity["additional"],
            "--github",
            alice_identity["github_repo"],
            "--no-prompt",
        ],
    )

    # Assert all correct values are being set
    assert "✅ Success!" in set_id.stdout
    set_id_output = set_id.stdout.splitlines()

    assert "Your extrinsic has been included as" in set_id_output[1]

    assert alice_identity["name"] in set_id_output[8]
    assert alice_identity["url"] in set_id_output[9]
    assert alice_identity["github_repo"] in set_id_output[10]
    assert alice_identity["image"] in set_id_output[11]
    assert alice_identity["discord"] in set_id_output[12]
    assert alice_identity["description"] in set_id_output[13]
    assert alice_identity["additional"] in set_id_output[14]

    # TODO: Currently coldkey + hotkey are the same for test wallets.
    # Maybe we can add a new key to help in distinguishing
    assert wallet_alice.coldkeypub.ss58_address in set_id_output[7]

    # Execute btcli get-identity using hotkey
    get_identity = exec_command_alice(
        command="wallet",
        sub_command="get-identity",
        extra_args=[
            "--chain",
            "ws://127.0.0.1:9945",
            "--key",
            wallet_alice.coldkeypub.ss58_address,
        ],
    )

    # Assert all correct values are being fetched for the ID we just set
    get_identity_output = get_identity.stdout.splitlines()
    assert alice_identity["name"] in get_identity_output[5]
    assert alice_identity["url"] in get_identity_output[6]
    assert alice_identity["github_repo"] in get_identity_output[7]
    assert alice_identity["image"] in get_identity_output[8]
    assert alice_identity["discord"] in get_identity_output[9]
    assert alice_identity["description"] in get_identity_output[10]
    assert alice_identity["additional"] in get_identity_output[11]

    # Sign a message using hotkey
    sign_using_hotkey = exec_command_alice(
        command="wallet",
        sub_command="sign",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--use-hotkey",
            "--message",
            "Bittensor is evolving to be the world's greatest decentralized AI network",
        ],
    )

    assert "Message signed successfully" in sign_using_hotkey.stdout

    # Sign a message using coldkey
    sign_using_coldkey = exec_command_alice(
        command="wallet",
        sub_command="sign",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--use-hotkey",
            "--message",
            "Bittensor is evolving to be the world's greatest decentralized AI network",
        ],
    )

    assert "Message signed successfully" in sign_using_coldkey.stdout

    print("✅ Passed wallet set-id, get-id, sign command")


def test_wallet_associate_hotkey(local_chain, wallet_setup):
    """
    Test the associating hotkeys and their different cases.

    Steps:
        1. Create wallets for Alice, Bob, and Charlie
        2. Associate a hotkey with Alice's wallet using hotkey name
        3. Verify the association is successful
        4. Try to associate Alice's hotkey with Bob's wallet (should fail)
        5. Try to associate Alice's hotkey again (should show already associated)
        6. Associate Charlie's hotkey with Bob's wallet using SS58 address

    Raises:
        AssertionError: If any of the checks or verifications fail
    """
    print("Testing wallet associate-hotkey command 🧪")

    _, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup("//Alice")
    _, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup("//Bob")
    _, wallet_charlie, _, _ = wallet_setup("//Charlie")

    # Associate Alice's default hotkey with her wallet
    result = exec_command_alice(
        command="wallet",
        sub_command="associate-hotkey",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--no-prompt",
        ],
    )

    # Assert successful association
    assert "Successfully associated hotkey" in result.stdout
    assert wallet_alice.hotkey.ss58_address in result.stdout
    assert wallet_alice.coldkeypub.ss58_address in result.stdout
    assert wallet_alice.hotkey_str in result.stdout

    # Try to associate Alice's hotkey with Bob's wallet (should fail)
    result = exec_command_bob(
        command="wallet",
        sub_command="associate-hotkey",
        extra_args=[
            "--wallet-path",
            wallet_path_bob,
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            wallet_bob.name,
            "--hotkey-ss58",
            wallet_alice.hotkey.ss58_address,
            "--no-prompt",
        ],
    )

    assert "Warning" in result.stdout
    assert "is already associated with" in result.stdout

    # Try to associate Alice's hotkey again with Alice's wallet
    result = exec_command_alice(
        command="wallet",
        sub_command="associate-hotkey",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            wallet_alice.name,
            "--wallet-hotkey",
            wallet_alice.hotkey_str,
            "--no-prompt",
        ],
    )

    assert "is already associated with" in result.stdout
    assert "wallet" in result.stdout
    assert wallet_alice.name in result.stdout

    # Associate Charlie's hotkey with Bob's wallet using SS58 address
    result = exec_command_bob(
        command="wallet",
        sub_command="associate-hotkey",
        extra_args=[
            "--wallet-path",
            wallet_path_bob,
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            wallet_bob.name,
            "--hotkey-ss58",
            wallet_charlie.hotkey.ss58_address,
            "--no-prompt",
        ],
    )

    assert "Successfully associated hotkey" in result.stdout
    assert wallet_charlie.hotkey.ss58_address in result.stdout
    assert wallet_bob.coldkeypub.ss58_address in result.stdout

    print("✅ Passed wallet associate-hotkey command")
