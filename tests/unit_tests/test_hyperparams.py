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
