import asyncio
import json

from bittensor_cli.src import HYPERPARAMS, RootSudoOnly
from .utils import turn_off_hyperparam_freeze_window

"""
Verify commands:

* btcli subnets create
* btcli sudo set
* btcli sudo get
"""


def test_hyperparams_setting(local_chain, wallet_setup):
    netuid = 2
    wallet_path_alice = "//Alice"
    # Create wallet for Alice
    keypair_alice, wallet_alice, wallet_path_alice, exec_command_alice = wallet_setup(
        wallet_path_alice
    )
    try:
        asyncio.run(turn_off_hyperparam_freeze_window(local_chain, wallet_alice))
    except ValueError:
        print(
            "Skipping turning off hyperparams freeze window. This indicates the call does not exist on the chain you are testing."
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

    # Fetch the hyperparameters of the subnet
    hyperparams = exec_command_alice(
        command="sudo",
        sub_command="get",
        extra_args=[
            "--network",
            "ws://127.0.0.1:9945",
            "--netuid",
            netuid,
            "--json-out",
        ],
    )

    # Parse all hyperparameters and single out max_burn in TAO
    all_hyperparams = json.loads(hyperparams.stdout)
    hp = {}
    for hyperparam in all_hyperparams:
        hp[hyperparam["hyperparameter"]] = hyperparam["value"]
        # Verify new metadata fields are present in JSON output
        assert "description" in hyperparam, (
            f"Missing description for {hyperparam['hyperparameter']}"
        )
        assert "side_effects" in hyperparam, (
            f"Missing side_effects for {hyperparam['hyperparameter']}"
        )
        assert "owner_settable" in hyperparam, (
            f"Missing owner_settable for {hyperparam['hyperparameter']}"
        )
        assert "docs_link" in hyperparam, (
            f"Missing docs_link for {hyperparam['hyperparameter']}"
        )
        # Verify description is not empty (unless it's a parameter without metadata)
        assert isinstance(hyperparam["description"], str), (
            f"Description should be string for {hyperparam['hyperparameter']}"
        )

    # Skip parameters that cannot be set with --no-prompt
    SKIP_PARAMS = {
        "alpha_high",
        "alpha_low",
        "subnet_is_active",
        "yuma_version",
        "burn_increase_mult",
    }

    for key, (extrinsic, sudo_only) in HYPERPARAMS.items():
        print(f"key: {key}, sudo_only: {sudo_only}")
        if (
            extrinsic  # display-only params (e.g. activity_cutoff) are not settable
            and key in hp.keys()
            and sudo_only == RootSudoOnly.FALSE
            and key not in SKIP_PARAMS
        ):
            if isinstance(hp[key], bool):
                new_val = not hp[key]
            elif isinstance(hp[key], int):
                if hp[key] < 100:
                    new_val = hp[key] + 1
                else:
                    new_val = hp[key] - 1
            else:
                raise ValueError(
                    f"Unrecognized hyperparameter value type: {key}: {hp[key]}"
                )
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
                    key,
                    "--value",
                    new_val,
                ],
            )
            cmd_json = json.loads(cmd.stdout)
            assert cmd_json["success"] is True, (key, new_val, cmd.stdout, cmd_json)
            assert isinstance(cmd_json["extrinsic_identifier"], str)
            print(f"Successfully set hyperparameter {key} to value {new_val}")
    # activity_cutoff_factor is COMPLICATED (owner or root-sudo), so the loop above
    # skips it; as the subnet owner, Alice takes the owner path in no-prompt mode.
    # Only present on chains with the dynamic-tempo runtime.
    if "activity_cutoff_factor" in hp:
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
                "activity_cutoff_factor",
                "--value",
                "14000",
            ],
        )
        cmd_json = json.loads(cmd.stdout)
        assert cmd_json["success"] is True, (cmd.stdout, cmd_json)
        assert isinstance(cmd_json["extrinsic_identifier"], str)
        print("Successfully set hyperparameter activity_cutoff_factor to value 14000")
    # also test hidden hyperparam
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
            "min_allowed_uids",
            "--value",
            "110",
        ],
    )
    cmd_json = json.loads(cmd.stdout)
    assert cmd_json["success"] is True, (cmd.stdout, cmd_json)
    assert isinstance(cmd_json["extrinsic_identifier"], str)
    # also test normalization
    param_value_map = [
        ("max_burn", "110"),
        ("burn_half_life", "0.15"),
        ("burn_increase_mult", "1.26"),
        ("max_regs_per_block", "2"),  # already normalized
    ]
    for param, val in param_value_map:
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
                param,
                "--value",
                val,
                "--normalize",
            ],
        )
        cmd_json = json.loads(cmd.stdout)
        assert cmd_json["success"] is True, (cmd.stdout, cmd_json)
        assert isinstance(cmd_json["extrinsic_identifier"], str)
    print("Successfully set hyperparameters")
    print("Testing trimming UIDs")
    cmd = exec_command_alice(
        command="sudo",
        sub_command="trim",
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
            "--max",
            "120",
            "--json-out",
            "--no-prompt",
        ],
    )
    cmd_json = json.loads(cmd.stdout)
    assert cmd_json["success"] is True, (cmd.stdout, cmd_json)
    assert isinstance(cmd_json["extrinsic_identifier"], str)
    print("Successfully trimmed UIDs")
