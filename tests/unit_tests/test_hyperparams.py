"""Unit tests for HYPERPARAMS and HYPERPARAMS_METADATA (issue #826)."""

from bittensor_cli.src import HYPERPARAMS, HYPERPARAMS_METADATA, RootSudoOnly


NEW_HYPERPARAMS_826 = {
    "sn_owner_hotkey",
    "subnet_owner_hotkey",
    "recycle_or_burn",
}


def test_new_hyperparams_in_hyperparams():
    for key in NEW_HYPERPARAMS_826:
        assert key in HYPERPARAMS, f"{key} should be in HYPERPARAMS"
        extrinsic, root_only = HYPERPARAMS[key]
        assert extrinsic, f"{key} must have non-empty extrinsic name"
        assert root_only is RootSudoOnly.FALSE


def test_subnet_owner_hotkey_alias_maps_to_same_extrinsic():
    ext_sn, _ = HYPERPARAMS["sn_owner_hotkey"]
    ext_subnet, _ = HYPERPARAMS["subnet_owner_hotkey"]
    assert ext_sn == ext_subnet == "sudo_set_sn_owner_hotkey"


def test_new_hyperparams_have_metadata():
    required = {"description", "side_effects", "owner_settable", "docs_link"}
    for key in NEW_HYPERPARAMS_826:
        assert key in HYPERPARAMS_METADATA, f"{key} should be in HYPERPARAMS_METADATA"
        meta = HYPERPARAMS_METADATA[key]
        for field in required:
            assert field in meta, f"{key} metadata missing '{field}'"
        assert isinstance(meta["description"], str)
        assert isinstance(meta["owner_settable"], bool)


def test_new_hyperparams_owner_settable_true():
    for key in NEW_HYPERPARAMS_826:
        assert HYPERPARAMS_METADATA[key]["owner_settable"] is True


def test_max_burn_is_owner_or_root_settable():
    _, root_only = HYPERPARAMS["max_burn"]
    assert root_only is RootSudoOnly.COMPLICATED


def test_max_burn_metadata_owner_settable_true():
    assert HYPERPARAMS_METADATA["max_burn"]["owner_settable"] is True


# --- Dynamic tempo / owner-triggered epochs (subtensor issue #2633) ---


def test_tempo_is_owner_or_root_settable():
    extrinsic, root_only = HYPERPARAMS["tempo"]
    assert extrinsic == "set_tempo"
    assert root_only is RootSudoOnly.COMPLICATED


def test_tempo_owner_path_uses_subtensor_module():
    from bittensor_cli.src import HYPERPARAMS_MODULE

    assert HYPERPARAMS_MODULE["tempo"] == "SubtensorModule"


def test_tempo_root_path_uses_sudo_set_tempo():
    from bittensor_cli.src import HYPERPARAMS_ROOT_EXTRINSIC

    assert HYPERPARAMS_ROOT_EXTRINSIC["tempo"] == ("AdminUtils", "sudo_set_tempo")


def test_activity_cutoff_factor_settable_via_subtensor_module():
    from bittensor_cli.src import HYPERPARAMS_MODULE

    extrinsic, root_only = HYPERPARAMS["activity_cutoff_factor"]
    assert extrinsic == "set_activity_cutoff_factor"
    assert root_only is RootSudoOnly.COMPLICATED
    assert HYPERPARAMS_MODULE["activity_cutoff_factor"] == "SubtensorModule"


def test_activity_cutoff_no_longer_directly_settable():
    extrinsic, _ = HYPERPARAMS["activity_cutoff"]
    assert extrinsic == ""
    assert HYPERPARAMS_METADATA["activity_cutoff"]["owner_settable"] is False


def test_dynamic_tempo_params_have_metadata():
    required = {"description", "side_effects", "owner_settable", "docs_link"}
    for key in ("tempo", "activity_cutoff", "activity_cutoff_factor"):
        assert key in HYPERPARAMS_METADATA, f"{key} should be in HYPERPARAMS_METADATA"
        meta = HYPERPARAMS_METADATA[key]
        for field in required:
            assert field in meta, f"{key} metadata missing '{field}'"
    assert HYPERPARAMS_METADATA["tempo"]["owner_settable"] is True
    assert HYPERPARAMS_METADATA["activity_cutoff_factor"]["owner_settable"] is True


def test_activity_cutoff_factor_allowed_value_bounds():
    from bittensor_cli.src.commands.sudo import allowed_value

    ok, val = allowed_value("activity_cutoff_factor", "13889", normalize=False)
    assert ok is True
    assert val == 13889

    ok, _ = allowed_value("activity_cutoff_factor", "999", normalize=False)
    assert ok is False

    ok, _ = allowed_value("activity_cutoff_factor", "50001", normalize=False)
    assert ok is False

    ok, _ = allowed_value("activity_cutoff_factor", "not_a_number", normalize=False)
    assert ok is False


def test_min_childkey_take_in_hyperparams():
    extrinsic, root_only = HYPERPARAMS["min_childkey_take"]
    assert extrinsic == "sudo_set_min_childkey_take_per_subnet"
    assert root_only is RootSudoOnly.FALSE


def test_min_childkey_take_has_metadata():
    required = {"description", "side_effects", "owner_settable", "docs_link"}
    assert "min_childkey_take" in HYPERPARAMS_METADATA
    meta = HYPERPARAMS_METADATA["min_childkey_take"]
    for field in required:
        assert field in meta, f"min_childkey_take metadata missing '{field}'"
    assert meta["owner_settable"] is True
    assert "#minchildkeytake" in meta["docs_link"]
