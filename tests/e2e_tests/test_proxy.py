import json
import os
import time

from bittensor_cli.src.bittensor.utils import ProxyAnnouncements

"""
Verify commands:

* btcli proxy create
* btcli proxy add
* btcli proxy remove
* btcli proxy kill
* btcli proxy execute
"""


def test_proxy_create(local_chain, wallet_setup):
    """
    Tests the pure proxy logic (create/kill)

    Steps:
    1. Creates pure proxy (with delay)
    2. Fund pure proxy
    3. Verifies pure proxy balance
    4. Ensures unannounced call fails (bc of delay at creation)
    4. Makes announcement of pure proxy's intent to transfer to Bob
    5. Executes previous announcement of transfer to Bob
    6. Ensures Bob has received the funds
    7. Makes announcement of pure proxy's intent to kill
    8. Kills pure proxy

    """
    testing_db_loc = "/tmp/btcli-test.db"
    os.environ["BTCLI_PROXIES_PATH"] = testing_db_loc
    wallet_path_alice = "//Alice"
    wallet_path_bob = "//Bob"

    # Create wallets for Alice and Bob
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup(
        wallet_path_bob
    )
    proxy_type = "Any"
    delay = 1

    try:
        # create a pure proxy
        create_result = exec_command_alice(
            command="proxy",
            sub_command="create",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--proxy-type",
                proxy_type,
                "--delay",
                str(delay),
                "--period",
                "128",
                "--no-prompt",
                "--json-output",
            ],
        )
        create_result_output = json.loads(create_result.stdout)
        assert create_result_output["success"] is True
        assert create_result_output["message"] is not None
        assert create_result_output["extrinsic_identifier"] is not None
        created_extrinsic_id = create_result_output["extrinsic_identifier"].split("-")
        created_block = int(created_extrinsic_id[0])
        created_extrinsic_idx = int(created_extrinsic_id[1])
        created_pure = create_result_output["data"]["pure"]
        spawner = create_result_output["data"]["spawner"]
        created_proxy_type = create_result_output["data"]["proxy_type"]
        created_delay = create_result_output["data"]["delay"]
        assert isinstance(created_pure, str)
        assert isinstance(spawner, str)
        assert spawner == wallet_alice.coldkeypub.ss58_address
        assert created_proxy_type == proxy_type
        assert created_delay == delay
        print("Passed pure creation.")

        # transfer some funds from alice to the pure proxy
        amount_to_transfer = 1_000
        transfer_result = exec_command_alice(
            command="wallet",
            sub_command="transfer",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--dest",
                created_pure,
                "--amount",
                str(amount_to_transfer),
                "--no-prompt",
                "--json-output",
            ],
        )
        transfer_result_output = json.loads(transfer_result.stdout)
        assert transfer_result_output["success"] is True

        # ensure the proxy has the transferred funds
        balance_result = exec_command_alice(
            command="wallet",
            sub_command="balance",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--ss58",
                created_pure,
                "--json-output",
            ],
        )
        balance_result_output = json.loads(balance_result.stdout)
        assert (
            balance_result_output["balances"]["Provided Address 1"]["coldkey"]
            == created_pure
        )
        assert balance_result_output["balances"]["Provided Address 1"]["free"] == float(
            amount_to_transfer
        )

        # transfer some of the pure proxy's funds to bob, but don't announce it
        amount_to_transfer_proxy = 100
        transfer_result_proxy = exec_command_alice(
            command="wallet",
            sub_command="transfer",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--proxy",
                created_pure,
                "--dest",
                keypair_bob.ss58_address,
                "--amount",
                str(amount_to_transfer_proxy),
                "--no-prompt",
                "--json-output",
            ],
        )
        transfer_result_proxy_output = json.loads(transfer_result_proxy.stdout)
        # should fail, because it wasn't announced
        assert transfer_result_proxy_output["success"] is False

        # announce the same extrinsic
        transfer_result_proxy = exec_command_alice(
            command="wallet",
            sub_command="transfer",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--proxy",
                created_pure,
                "--dest",
                keypair_bob.ss58_address,
                "--amount",
                str(amount_to_transfer_proxy),
                "--no-prompt",
                "--json-output",
                "--announce-only",
            ],
        )
        print(transfer_result_proxy.stdout, transfer_result_proxy.stderr)
        transfer_result_proxy_output = json.loads(transfer_result_proxy.stdout)
        assert transfer_result_proxy_output["success"] is True
        with ProxyAnnouncements.get_db() as (conn, cursor):
            rows = ProxyAnnouncements.read_rows(conn, cursor, include_header=False)
        latest_announcement = next(
            iter(sorted(rows, key=lambda row: row[2], reverse=True))
        )  # sort by epoch time
        (
            idx,
            address,
            epoch_time,
            block,
            call_hash,
            call,
            call_serialized,
            executed_int,
        ) = latest_announcement
        assert address == created_pure
        assert executed_int == 0

        # wait for delay (probably already happened if fastblocks is on)
        time.sleep(3)

        # get Bob's initial balance
        balance_result = exec_command_bob(
            command="wallet",
            sub_command="balance",
            extra_args=[
                "--wallet-path",
                wallet_path_bob,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--json-output",
            ],
        )
        balance_result_output = json.loads(balance_result.stdout)
        assert (
            balance_result_output["balances"]["default"]["coldkey"]
            == wallet_bob.coldkeypub.ss58_address
        )
        bob_init_balance = balance_result_output["balances"]["default"]["free"]

        announce_execution_result = exec_command_alice(
            command="proxy",
            sub_command="execute",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--proxy",
                created_pure,
                "--call-hash",
                call_hash,
                "--no-prompt",
                "--json-output",
            ],
        )
        announce_execution_result_output = json.loads(announce_execution_result.stdout)
        assert announce_execution_result_output["success"] is True
        assert announce_execution_result_output["message"] == ""

        # ensure bob has the transferred funds
        balance_result = exec_command_bob(
            command="wallet",
            sub_command="balance",
            extra_args=[
                "--wallet-path",
                wallet_path_bob,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--json-output",
            ],
        )
        balance_result_output = json.loads(balance_result.stdout)
        assert (
            balance_result_output["balances"]["default"]["coldkey"]
            == wallet_bob.coldkeypub.ss58_address
        )
        assert (
            balance_result_output["balances"]["default"]["free"]
            == float(amount_to_transfer_proxy) + bob_init_balance
        )
        print("Passed transfer with announcement")

        # announce kill of the created pure proxy
        announce_kill_result = exec_command_alice(
            command="proxy",
            sub_command="kill",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--height",
                str(created_block),
                "--ext-index",
                str(created_extrinsic_idx),
                "--spawner",
                spawner,
                "--proxy-type",
                created_proxy_type,
                "--proxy",
                created_pure,
                "--json-output",
                "--no-prompt",
                "--announce-only",
            ],
        )
        print(announce_kill_result.stdout, announce_kill_result.stderr)
        kill_result_output = json.loads(announce_kill_result.stdout)
        assert kill_result_output["success"] is True
        assert kill_result_output["message"] == ""
        assert isinstance(kill_result_output["extrinsic_identifier"], str)
        print("Passed kill announcement")

        with ProxyAnnouncements.get_db() as (conn, cursor):
            rows = ProxyAnnouncements.read_rows(conn, cursor, include_header=False)
        latest_announcement = next(
            iter(sorted(rows, key=lambda row: row[2], reverse=True))
        )  # sort by epoch time
        (
            idx,
            address,
            epoch_time,
            block,
            call_hash,
            call,
            call_serialized,
            executed_int,
        ) = latest_announcement
        assert address == created_pure
        assert executed_int == 0
        # wait for delay (probably already happened if fastblocks is on)
        time.sleep(3)

        kill_announce_execution_result = exec_command_alice(
            command="proxy",
            sub_command="execute",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--proxy",
                created_pure,
                "--call-hash",
                call_hash,
                "--no-prompt",
                "--json-output",
            ],
        )
        kill_announce_execution_result_output = json.loads(
            kill_announce_execution_result.stdout
        )
        assert kill_announce_execution_result_output["success"] is True
        assert kill_announce_execution_result_output["message"] == ""
    finally:
        os.environ["BTCLI_PROXIES_PATH"] = ""
        if os.path.exists(testing_db_loc):
            os.remove(testing_db_loc)


def test_add_proxy(local_chain, wallet_setup):
    """
    Tests the non-pure (delegated) proxy logic (add/remove)

    Steps:
    1. Add Dave as a proxy of Alice (with delay)
    2. Attempt proxy transfer without announcement (it should fail)
    3. Make proxy transfer to Bob
    4. Ensure Bob got the funds, the funds were deducted from Alice, and that Dave paid the ext fee
    5. Remove Dave as a proxy of Alice
    """
    testing_db_loc = "/tmp/btcli-test.db"
    os.environ["BTCLI_PROXIES_PATH"] = testing_db_loc
    wallet_path_alice = "//Alice"
    wallet_path_bob = "//Bob"
    wallet_path_dave = "//Dave"

    # Create wallets for Alice and Bob
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup(
        wallet_path_bob
    )
    keypair_dave, wallet_dave, wallet_path_dave, exec_command_dave = wallet_setup(
        wallet_path_dave
    )
    proxy_type = "Any"
    delay = 1

    try:
        # add Dave as a proxy of Alice
        add_result = exec_command_alice(
            command="proxy",
            sub_command="add",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--delegate",
                wallet_dave.coldkeypub.ss58_address,
                "--proxy-type",
                proxy_type,
                "--delay",
                str(delay),
                "--period",
                "128",
                "--no-prompt",
                "--json-output",
            ],
        )
        add_result_output = json.loads(add_result.stdout)
        assert add_result_output["success"] is True
        assert "Added proxy delegatee" in add_result_output["message"]
        assert (
            add_result_output["data"]["delegatee"]
            == wallet_dave.coldkeypub.ss58_address
        )
        assert (
            add_result_output["data"]["delegator"]
            == wallet_alice.coldkeypub.ss58_address
        )
        assert add_result_output["data"]["proxy_type"] == proxy_type
        assert add_result_output["data"]["delay"] == delay
        print("Proxy Add successful")

        # Check dave's init balance
        dave_balance_result = exec_command_dave(
            command="wallet",
            sub_command="balance",
            extra_args=[
                "--wallet-path",
                wallet_path_dave,
                "--wallet-name",
                "default",
                "--json-output",
                "--chain",
                "ws://127.0.0.1:9945",
            ],
        )
        dave_balance_output = json.loads(dave_balance_result.stdout)
        assert (
            dave_balance_output["balances"]["default"]["coldkey"]
            == wallet_dave.coldkeypub.ss58_address
        )
        dave_init_balance = dave_balance_output["balances"]["default"]["free"]

        # Check Bob's init balance
        balance_result = exec_command_bob(
            command="wallet",
            sub_command="balance",
            extra_args=[
                "--wallet-path",
                wallet_path_bob,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--json-output",
            ],
        )
        balance_result_output = json.loads(balance_result.stdout)
        assert (
            balance_result_output["balances"]["default"]["coldkey"]
            == wallet_bob.coldkeypub.ss58_address
        )
        bob_init_balance = balance_result_output["balances"]["default"]["free"]

        # check alice's init balance
        balance_result = exec_command_alice(
            command="wallet",
            sub_command="balance",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--json-output",
            ],
        )
        balance_result_output = json.loads(balance_result.stdout)
        assert (
            balance_result_output["balances"]["default"]["coldkey"]
            == wallet_alice.coldkeypub.ss58_address
        )
        alice_init_balance = balance_result_output["balances"]["default"]["free"]

        # transfer some of alice's funds to bob through the proxy, but don't announce it
        amount_to_transfer_proxy = 100
        transfer_result_proxy = exec_command_dave(
            command="wallet",
            sub_command="transfer",
            extra_args=[
                "--wallet-path",
                wallet_path_dave,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--proxy",
                wallet_alice.coldkeypub.ss58_address,
                "--dest",
                keypair_bob.ss58_address,
                "--amount",
                str(amount_to_transfer_proxy),
                "--no-prompt",
                "--json-output",
            ],
        )
        transfer_result_proxy_output = json.loads(transfer_result_proxy.stdout)
        # should fail, because it wasn't announced
        assert transfer_result_proxy_output["success"] is False

        # announce the same extrinsic
        transfer_result_proxy = exec_command_dave(
            command="wallet",
            sub_command="transfer",
            extra_args=[
                "--wallet-path",
                wallet_path_dave,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--proxy",
                wallet_alice.coldkeypub.ss58_address,
                "--dest",
                keypair_bob.ss58_address,
                "--amount",
                str(amount_to_transfer_proxy),
                "--no-prompt",
                "--json-output",
                "--announce-only",
            ],
        )
        print(transfer_result_proxy.stdout, transfer_result_proxy.stderr)
        transfer_result_proxy_output = json.loads(transfer_result_proxy.stdout)
        assert transfer_result_proxy_output["success"] is True
        with ProxyAnnouncements.get_db() as (conn, cursor):
            rows = ProxyAnnouncements.read_rows(conn, cursor, include_header=False)
        latest_announcement = next(
            iter(sorted(rows, key=lambda row: row[2], reverse=True))
        )  # sort by epoch time
        (
            idx,
            address,
            epoch_time,
            block,
            call_hash,
            call,
            call_serialized,
            executed_int,
        ) = latest_announcement
        assert address == wallet_alice.coldkeypub.ss58_address
        assert executed_int == 0

        # wait for delay (probably already happened if fastblocks is on)
        time.sleep(3)

        announce_execution_result = exec_command_dave(
            command="proxy",
            sub_command="execute",
            extra_args=[
                "--wallet-path",
                wallet_path_dave,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--proxy",
                wallet_alice.coldkeypub.ss58_address,
                "--call-hash",
                call_hash,
                "--no-prompt",
                "--json-output",
            ],
        )
        announce_execution_result_output = json.loads(announce_execution_result.stdout)
        assert announce_execution_result_output["success"] is True
        assert announce_execution_result_output["message"] == ""

        # ensure bob has the transferred funds
        balance_result = exec_command_bob(
            command="wallet",
            sub_command="balance",
            extra_args=[
                "--wallet-path",
                wallet_path_bob,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--json-output",
            ],
        )
        balance_result_output = json.loads(balance_result.stdout)
        assert (
            balance_result_output["balances"]["default"]["coldkey"]
            == wallet_bob.coldkeypub.ss58_address
        )
        assert (
            balance_result_output["balances"]["default"]["free"]
            == float(amount_to_transfer_proxy) + bob_init_balance
        )

        # ensure the amount was subtracted from alice's balance, not dave's
        balance_result = exec_command_alice(
            command="wallet",
            sub_command="balance",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--json-output",
            ],
        )
        balance_result_output = json.loads(balance_result.stdout)
        assert (
            balance_result_output["balances"]["default"]["coldkey"]
            == wallet_alice.coldkeypub.ss58_address
        )
        assert balance_result_output["balances"]["default"][
            "free"
        ] == alice_init_balance - float(amount_to_transfer_proxy)

        # ensure dave paid the extrinsic fee
        balance_result = exec_command_dave(
            command="wallet",
            sub_command="balance",
            extra_args=[
                "--wallet-path",
                wallet_path_dave,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--json-output",
            ],
        )
        balance_result_output = json.loads(balance_result.stdout)
        assert (
            balance_result_output["balances"]["default"]["coldkey"]
            == wallet_dave.coldkeypub.ss58_address
        )
        assert balance_result_output["balances"]["default"]["free"] < dave_init_balance

        print("Passed transfer with announcement")

        # remove the proxy
        remove_result = exec_command_alice(
            command="proxy",
            sub_command="remove",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--delegate",
                wallet_dave.coldkeypub.ss58_address,
                "--proxy-type",
                proxy_type,
                "--delay",
                str(delay),
                "--period",
                "128",
                "--no-prompt",
                "--json-output",
            ],
        )
        remove_result_output = json.loads(remove_result.stdout)
        assert remove_result_output["success"] is True
        assert remove_result_output["message"] == ""
        assert isinstance(remove_result_output["extrinsic_identifier"], str)
        print("Passed proxy removal")
    finally:
        os.environ["BTCLI_PROXIES_PATH"] = ""
        if os.path.exists(testing_db_loc):
            os.remove(testing_db_loc)


def test_remove_all_proxies(local_chain, wallet_setup):
    """
    Tests removing all proxies using the --all flag

    Steps:
    1. Add multiple proxies (Dave and Bob as proxies of Alice)
    2. Verify proxies are added
    3. Remove all proxies using --all flag
    4. Verify all proxies are removed
    5. Attempt to use one of the proxies (should fail)
    """
    testing_db_loc = "/tmp/btcli-test.db"
    os.environ["BTCLI_PROXIES_PATH"] = testing_db_loc
    wallet_path_alice = "//Alice"
    wallet_path_bob = "//Bob"
    wallet_path_dave = "//Dave"

    # Create wallets for Alice, Bob, and Dave
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup(
        wallet_path_bob
    )
    keypair_dave, wallet_dave, wallet_path_dave, exec_command_dave = wallet_setup(
        wallet_path_dave
    )
    proxy_type_1 = "Transfer"
    proxy_type_2 = "Staking"
    delay = 0

    try:
        # Add Dave as a proxy of Alice (Transfer type)
        add_result_1 = exec_command_alice(
            command="proxy",
            sub_command="add",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--delegate",
                wallet_dave.coldkeypub.ss58_address,
                "--proxy-type",
                proxy_type_1,
                "--delay",
                str(delay),
                "--period",
                "128",
                "--no-prompt",
                "--json-output",
            ],
        )
        add_result_1_output = json.loads(add_result_1.stdout)
        assert add_result_1_output["success"] is True
        assert (
            add_result_1_output["data"]["delegatee"]
            == wallet_dave.coldkeypub.ss58_address
        )
        assert add_result_1_output["data"]["proxy_type"] == proxy_type_1
        print("Proxy 1 (Dave - Transfer) added successfully")

        # Add Bob as a proxy of Alice (Staking type)
        add_result_2 = exec_command_alice(
            command="proxy",
            sub_command="add",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--delegate",
                wallet_bob.coldkeypub.ss58_address,
                "--proxy-type",
                proxy_type_2,
                "--delay",
                str(delay),
                "--period",
                "128",
                "--no-prompt",
                "--json-output",
            ],
        )
        add_result_2_output = json.loads(add_result_2.stdout)
        assert add_result_2_output["success"] is True
        assert (
            add_result_2_output["data"]["delegatee"]
            == wallet_bob.coldkeypub.ss58_address
        )
        assert add_result_2_output["data"]["proxy_type"] == proxy_type_2
        print("Proxy 2 (Bob - Staking) added successfully")

        # Remove all proxies using --all flag
        # Note: proxy_type is still required by CLI even with --all due to prompt=True
        # so we provide it with a default value to avoid prompting
        remove_all_result = exec_command_alice(
            command="proxy",
            sub_command="remove",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--all",
                "--proxy-type",
                "Any",  # Required to avoid prompt even with --all
                "--period",
                "128",
                "--no-prompt",
                "--json-output",
            ],
        )
        remove_all_result_output = json.loads(remove_all_result.stdout)
        assert remove_all_result_output["success"] is True
        assert remove_all_result_output["message"] == ""
        assert isinstance(remove_all_result_output["extrinsic_identifier"], str)
        print("All proxies removed successfully")

        # Verify proxies are removed by attempting to use one (should fail)
        # Try to use Dave as proxy (should fail since proxy was removed)
        amount_to_transfer_proxy = 100
        transfer_result_proxy = exec_command_dave(
            command="wallet",
            sub_command="transfer",
            extra_args=[
                "--wallet-path",
                wallet_path_dave,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--proxy",
                wallet_alice.coldkeypub.ss58_address,
                "--dest",
                keypair_bob.ss58_address,
                "--amount",
                str(amount_to_transfer_proxy),
                "--no-prompt",
                "--json-output",
            ],
        )
        transfer_result_proxy_output = json.loads(transfer_result_proxy.stdout)
        # Should fail because proxy was removed
        assert transfer_result_proxy_output["success"] is False
        print("Verified proxy removal - transfer via removed proxy failed as expected")

    finally:
        os.environ["BTCLI_PROXIES_PATH"] = ""
        if os.path.exists(testing_db_loc):
            os.remove(testing_db_loc)


def test_remove_proxy_validation(local_chain, wallet_setup):
    """
    Tests validation for proxy remove command

    Steps:
    1. Attempt to remove proxy without --delegate or --all (should fail)
    2. Attempt to remove with --all flag (should succeed if no proxies exist)
    """
    testing_db_loc = "/tmp/btcli-test.db"
    os.environ["BTCLI_PROXIES_PATH"] = testing_db_loc
    wallet_path_alice = "//Alice"

    # Create wallet for Alice
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )

    try:
        # Attempt to remove proxy without --delegate or --all (should fail)
        # Note: proxy_type must be provided to avoid Typer prompt, but validation
        # should still fail because delegate is missing
        remove_result = exec_command_alice(
            command="proxy",
            sub_command="remove",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--proxy-type",
                "Any",  # Required to avoid prompt, but delegate still missing
                "--period",
                "128",
                "--no-prompt",
                "--json-output",
            ],
        )
        remove_result_output = json.loads(remove_result.stdout)
        assert remove_result_output["success"] is False
        assert (
            "Either --delegate must be provided or --all flag must be used"
            in remove_result_output["message"]
        )
        print("Validation test passed - correctly rejected missing delegate/--all")

        # Attempt to remove all proxies when none exist (should succeed)
        # Note: proxy_type is still required by CLI even with --all due to prompt=True
        # so we provide it with a default value to avoid prompting
        remove_all_result = exec_command_alice(
            command="proxy",
            sub_command="remove",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--all",
                "--proxy-type",
                "Any",  # Required to avoid prompt even with --all
                "--period",
                "128",
                "--no-prompt",
                "--json-output",
            ],
        )
        remove_all_result_output = json.loads(remove_all_result.stdout)
        # Should succeed even if no proxies exist (remove_proxies extrinsic handles this)
        assert remove_all_result_output["success"] is True
        print("Validation test passed - --all works even when no proxies exist")

    finally:
        os.environ["BTCLI_PROXIES_PATH"] = ""
        if os.path.exists(testing_db_loc):
            os.remove(testing_db_loc)


def test_remove_proxy(local_chain, wallet_setup):
    """
    Tests removing a single proxy (without --all flag)

    Steps:
    1. Add Dave as a proxy of Alice
    2. Verify proxy is added and can be used
    3. Remove the specific proxy using --delegate flag
    4. Verify proxy is removed by attempting to use it (should fail)
    """
    testing_db_loc = "/tmp/btcli-test.db"
    os.environ["BTCLI_PROXIES_PATH"] = testing_db_loc
    wallet_path_alice = "//Alice"
    wallet_path_bob = "//Bob"
    wallet_path_dave = "//Dave"

    # Create wallets for Alice, Bob, and Dave
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup(
        wallet_path_bob
    )
    keypair_dave, wallet_dave, wallet_path_dave, exec_command_dave = wallet_setup(
        wallet_path_dave
    )
    proxy_type = "Transfer"
    delay = 0

    try:
        # Add Dave as a proxy of Alice
        add_result = exec_command_alice(
            command="proxy",
            sub_command="add",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--delegate",
                wallet_dave.coldkeypub.ss58_address,
                "--proxy-type",
                proxy_type,
                "--delay",
                str(delay),
                "--period",
                "128",
                "--no-prompt",
                "--json-output",
            ],
        )
        add_result_output = json.loads(add_result.stdout)
        assert add_result_output["success"] is True
        assert (
            add_result_output["data"]["delegatee"]
            == wallet_dave.coldkeypub.ss58_address
        )
        assert (
            add_result_output["data"]["delegator"]
            == wallet_alice.coldkeypub.ss58_address
        )
        assert add_result_output["data"]["proxy_type"] == proxy_type
        assert add_result_output["data"]["delay"] == delay
        print("Proxy added successfully")

        # Verify proxy works by checking Bob's initial balance
        balance_result = exec_command_bob(
            command="wallet",
            sub_command="balance",
            extra_args=[
                "--wallet-path",
                wallet_path_bob,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--json-output",
            ],
        )
        balance_result_output = json.loads(balance_result.stdout)
        assert (
            balance_result_output["balances"]["default"]["coldkey"]
            == wallet_bob.coldkeypub.ss58_address
        )
        bob_init_balance = balance_result_output["balances"]["default"]["free"]

        # Check Alice's initial balance
        balance_result = exec_command_alice(
            command="wallet",
            sub_command="balance",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--json-output",
            ],
        )
        balance_result_output = json.loads(balance_result.stdout)
        assert (
            balance_result_output["balances"]["default"]["coldkey"]
            == wallet_alice.coldkeypub.ss58_address
        )

        # Use the proxy to transfer from Alice to Bob
        amount_to_transfer_proxy = 100
        transfer_result_proxy = exec_command_dave(
            command="wallet",
            sub_command="transfer",
            extra_args=[
                "--wallet-path",
                wallet_path_dave,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--proxy",
                wallet_alice.coldkeypub.ss58_address,
                "--dest",
                keypair_bob.ss58_address,
                "--amount",
                str(amount_to_transfer_proxy),
                "--no-prompt",
                "--json-output",
            ],
        )
        transfer_result_proxy_output = json.loads(transfer_result_proxy.stdout)
        assert transfer_result_proxy_output["success"] is True
        print("Proxy transfer successful - proxy is working")

        # Verify Bob received the funds
        balance_result = exec_command_bob(
            command="wallet",
            sub_command="balance",
            extra_args=[
                "--wallet-path",
                wallet_path_bob,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--json-output",
            ],
        )
        balance_result_output = json.loads(balance_result.stdout)
        assert (
            balance_result_output["balances"]["default"]["free"]
            == float(amount_to_transfer_proxy) + bob_init_balance
        )

        # Now remove the proxy using single proxy removal (without --all)
        remove_result = exec_command_alice(
            command="proxy",
            sub_command="remove",
            extra_args=[
                "--wallet-path",
                wallet_path_alice,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--delegate",
                wallet_dave.coldkeypub.ss58_address,
                "--proxy-type",
                proxy_type,
                "--delay",
                str(delay),
                "--period",
                "128",
                "--no-prompt",
                "--json-output",
            ],
        )
        remove_result_output = json.loads(remove_result.stdout)
        assert remove_result_output["success"] is True
        assert remove_result_output["message"] == ""
        assert isinstance(remove_result_output["extrinsic_identifier"], str)
        print("Single proxy removal successful")

        # Verify proxy is removed by attempting to use it (should fail)
        transfer_result_proxy_2 = exec_command_dave(
            command="wallet",
            sub_command="transfer",
            extra_args=[
                "--wallet-path",
                wallet_path_dave,
                "--chain",
                "ws://127.0.0.1:9945",
                "--wallet-name",
                "default",
                "--proxy",
                wallet_alice.coldkeypub.ss58_address,
                "--dest",
                keypair_bob.ss58_address,
                "--amount",
                str(amount_to_transfer_proxy),
                "--no-prompt",
                "--json-output",
            ],
        )
        transfer_result_proxy_2_output = json.loads(transfer_result_proxy_2.stdout)
        # Should fail because proxy was removed
        assert transfer_result_proxy_2_output["success"] is False
        print("Verified proxy removal - transfer via removed proxy failed as expected")

    finally:
        os.environ["BTCLI_PROXIES_PATH"] = ""
        if os.path.exists(testing_db_loc):
            os.remove(testing_db_loc)


def test_proxy_list(local_chain, wallet_setup):
    """
    Tests the proxy list command.

    Steps:
    1. Add a proxy to Alice's account
    2. List proxies for Alice's account
    3. Verify the proxy is in the list
    4. Remove the proxy
    """
    wallet_path_alice = "//Alice"
    wallet_path_bob = "//Bob"

    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )
    keypair_bob, wallet_bob, wallet_path_bob, exec_command_bob = wallet_setup(
        wallet_path_bob
    )
    proxy_type = "Any"
    delay = 0

    # Add Bob as a proxy for Alice
    add_result = exec_command_alice(
        command="proxy",
        sub_command="add",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            "default",
            "--delegate",
            wallet_bob.coldkeypub.ss58_address,
            "--proxy-type",
            proxy_type,
            "--delay",
            str(delay),
            "--period",
            "128",
            "--no-prompt",
            "--json-output",
        ],
    )
    add_result_output = json.loads(add_result.stdout)
    assert add_result_output["success"] is True
    print("Passed proxy add for list test")

    # Wait for chain state to propagate
    time.sleep(2)

    # List proxies for Alice
    list_result = exec_command_alice(
        command="proxy",
        sub_command="list",
        extra_args=[
            "--address",
            wallet_alice.coldkeypub.ss58_address,
            "--chain",
            "ws://127.0.0.1:9945",
            "--json-output",
        ],
    )
    list_result_output = json.loads(list_result.stdout)
    assert list_result_output["success"] is True
    assert list_result_output["address"] == wallet_alice.coldkeypub.ss58_address
    assert len(list_result_output["proxies"]) >= 1

    # Verify Bob is in the proxy list
    found_bob = False
    for proxy in list_result_output["proxies"]:
        if proxy["delegate"] == wallet_bob.coldkeypub.ss58_address:
            found_bob = True
            assert proxy["proxy_type"] == proxy_type
            assert proxy["delay"] == delay
            break
    assert found_bob, "Bob should be in Alice's proxy list"
    print("Passed proxy list")

    # Clean up - remove the proxy
    remove_result = exec_command_alice(
        command="proxy",
        sub_command="remove",
        extra_args=[
            "--wallet-path",
            wallet_path_alice,
            "--chain",
            "ws://127.0.0.1:9945",
            "--wallet-name",
            "default",
            "--delegate",
            wallet_bob.coldkeypub.ss58_address,
            "--proxy-type",
            proxy_type,
            "--delay",
            str(delay),
            "--period",
            "128",
            "--no-prompt",
            "--json-output",
        ],
    )
    remove_result_output = json.loads(remove_result.stdout)
    assert remove_result_output["success"] is True
    print("Passed proxy removal cleanup")
