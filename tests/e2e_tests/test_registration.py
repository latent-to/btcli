import asyncio

from .utils import set_storage_extrinsic


# @pytest.mark.parametrize("local_chain", [False], indirect=True)
def test_registration(local_chain, wallet_setup):
    """
    Test registration both with and without limits

    Steps:
        1. Create wallets for Alice and Bob
        2. Create 2 subnets with Alice
        3. Register Bob in one subnet with no limit
        4. Register Bob in one subnet with limit
    """
    print("Testing registration scenarios 🧪")

    # Create wallets for Alice and Bob
    wallet_path_alice = "//Alice"
    wallet_path_bob = "//Bob"

    # Setup Alice's wallet
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )

    # Setup Bob's wallet
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup(
        wallet_path_bob
    )

    # # Call to make Alice root owner
    items = [
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
            items=items,
        )
    )

    # Create first subnet (netuid = 2)
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
            "Test Subnet 2",
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
            "--additional-info",
            "Test subnet",
            "--logo-url",
            "https://testsubnet.com/logo.png",
            "--no-prompt",
            "--no-mev-protection",
        ],
    )
    assert "✅ Registered subnetwork with netuid: 2" in result.stdout, result.stderr
    assert "Your extrinsic has been included" in result.stdout, result.stdout

    # Create second subnet (netuid = 3)
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
            "Test Subnet 3",
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
            "--additional-info",
            "Test subnet",
            "--logo-url",
            "https://testsubnet.com/logo.png",
            "--no-prompt",
            "--no-mev-protection",
        ],
    )
    assert "✅ Registered subnetwork with netuid: 3" in result.stdout, result.stderr
    assert "Your extrinsic has been included" in result.stdout, result.stdout

    # Start emission schedule for subnets
    start_call_netuid_0 = exec_command_alice(
        command="subnets",
        sub_command="start",
        extra_args=[
            "--netuid",
            "0",
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
        "Successfully started subnet 0's emission schedule."
        in start_call_netuid_0.stdout
    )
    assert "Your extrinsic has been included" in start_call_netuid_0.stdout, (
        start_call_netuid_0.stdout
    )
    start_call_netuid_2 = exec_command_alice(
        command="subnets",
        sub_command="start",
        extra_args=[
            "--netuid",
            "2",
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
        "Successfully started subnet 2's emission schedule."
        in start_call_netuid_2.stdout
    )
    assert "Your extrinsic has been included" in start_call_netuid_2.stdout

    start_call_netuid_3 = exec_command_alice(
        command="subnets",
        sub_command="start",
        extra_args=[
            "--netuid",
            "3",
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
        "Successfully started subnet 3's emission schedule."
        in start_call_netuid_3.stdout
    )
    assert "Your extrinsic has been included" in start_call_netuid_3.stdout
    # Register Bob in one subnet (no limit)
    register_result = exec_command_bob(
        command="subnets",
        sub_command="register",
        extra_args=[
            "--netuid",
            "2",
            "--wallet-path",
            wallet_path_bob,
            "--wallet-name",
            wallet_bob.name,
            "--hotkey",
            wallet_bob.hotkey_str,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--era",
            "30",
        ],
    )
    assert "✅ Registered" in register_result.stdout, register_result.stderr
    assert "Your extrinsic has been included" in register_result.stdout, (
        register_result.stdout
    )

    assert "Your extrinsic has been included" in start_call_netuid_3.stdout

    # use limit of 10%
    register_result = exec_command_bob(
        command="subnets",
        sub_command="register",
        extra_args=[
            "--netuid",
            "3",
            "--wallet-path",
            wallet_path_bob,
            "--wallet-name",
            wallet_bob.name,
            "--hotkey",
            wallet_bob.hotkey_str,
            "--chain",
            "ws://127.0.0.1:9945",
            "--no-prompt",
            "--era",
            "30",
            "--rate-tolerance",
            "0.10",
            "--verbose",
        ],
    )
    assert "Rate tolerance: 0.1 (10.0%)." in register_result.stdout, (
        register_result.stderr
    )
    assert "✅ Registered" in register_result.stdout, register_result.stderr
    assert "Your extrinsic has been included" in register_result.stdout, (
        register_result.stdout
    )
