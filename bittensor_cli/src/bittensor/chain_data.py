from abc import abstractmethod
from dataclasses import dataclass
from collections.abc import Sequence
from enum import Enum
from typing import Optional, Any, Union, Callable, Hashable

import netaddr
from scalecodec.utils.ss58 import ss58_encode

from bittensor_cli.src.bittensor.balances import Balance, fixed_to_float
from bittensor_cli.src.bittensor.networking import int_to_ip
from bittensor_cli.src.bittensor.utils import (
    SS58_FORMAT,
    u16_normalized_float as u16tf,
    u64_normalized_float as u64tf,
    decode_account_id,
    get_netuid_and_subuid_by_storage_index,
)

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self


class ChainDataType(Enum):
    NeuronInfo = 1
    DelegateInfo = 2
    NeuronInfoLite = 3
    StakeInfo = 4
    SubnetHyperparameters = 5
    DelegateInfoLite = 6
    DynamicInfo = 7
    ScheduledColdkeySwapInfo = 8
    SubnetInfo = 9
    SubnetState = 10
    SubnetIdentity = 11


def process_stake_data(stake_data, netuid):
    decoded_stake_data = {}
    for account_id_bytes, stake_ in stake_data:
        account_id = decode_account_id(account_id_bytes)
        decoded_stake_data.update(
            {account_id: Balance.from_rao(stake_).set_unit(netuid)}
        )
    return decoded_stake_data


def _tbwu(val: int, netuid: Optional[int] = 0) -> Balance:
    """Returns a Balance object from a value and unit."""
    return Balance.from_rao(val).set_unit(netuid)


@dataclass
class AxonInfo:
    version: int
    ip: str
    port: int
    ip_type: int
    hotkey: str
    coldkey: str
    protocol: int = 4
    placeholder1: int = 0
    placeholder2: int = 0

    @property
    def is_serving(self) -> bool:
        """True if the endpoint is serving."""
        return self.ip != "0.0.0.0"

    @classmethod
    def from_neuron_info(cls, neuron_info: dict) -> "AxonInfo":
        """
        Converts a dictionary to an AxonInfo object.

        Args:
            neuron_info (dict): A dictionary containing the neuron information.

        Returns:
            instance (AxonInfo): An instance of AxonInfo created from the dictionary.
        """
        return cls(
            version=neuron_info["axon_info"]["version"],
            ip=int_to_ip(int(neuron_info["axon_info"]["ip"])),
            port=neuron_info["axon_info"]["port"],
            ip_type=neuron_info["axon_info"]["ip_type"],
            hotkey=neuron_info["hotkey"],
            coldkey=neuron_info["coldkey"],
        )


@dataclass
class InfoBase:
    """Base dataclass for info objects."""

    @abstractmethod
    def _fix_decoded(self, decoded: Any) -> Self:
        raise NotImplementedError(
            "This is an abstract method and must be implemented in a subclass."
        )

    @classmethod
    def from_any(cls, data: Any) -> Self:
        return cls._fix_decoded(data)

    @classmethod
    def list_from_any(cls, data_list: list[Any]) -> list[Self]:
        return [cls.from_any(data) for data in data_list]

    def __getitem__(self, item):
        return getattr(self, item)

    def get(self, item, default=None):
        return getattr(self, item, default)


@dataclass
class SubnetHyperparameters(InfoBase):
    """
    This class represents the hyperparameters for a subnet.
    Attributes:
        rho (int): The rate of decay of some value.
        kappa (int): A constant multiplier used in calculations.
        immunity_period (int): The period during which immunity is active.
        min_allowed_weights (int): Minimum allowed weights.
        max_weight_limit (float): Maximum weight limit.
        tempo (int): The tempo or rate of operation.
        min_difficulty (int): Minimum difficulty for some operations.
        max_difficulty (int): Maximum difficulty for some operations.
        weights_version (int): The version number of the weights used.
        weights_rate_limit (int): Rate limit for processing weights.
        adjustment_interval (int): Interval at which adjustments are made.
        activity_cutoff (int): Activity cutoff threshold.
        registration_allowed (bool): Indicates if registration is allowed.
        target_regs_per_interval (int): Target number of registrations per interval.
        min_burn (int): Minimum burn value.
        max_burn (int): Maximum burn value.
        bonds_moving_avg (int): Moving average of bonds.
        max_regs_per_block (int): Maximum number of registrations per block.
        serving_rate_limit (int): Limit on the rate of service.
        max_validators (int): Maximum number of validators.
        adjustment_alpha (int): Alpha value for adjustments.
        difficulty (int): Difficulty level.
        commit_reveal_period (int): Interval for commit-reveal weights.
        commit_reveal_weights_enabled (bool): Flag indicating if commit-reveal weights are enabled.
        alpha_high (int): High value of alpha.
        alpha_low (int): Low value of alpha.
        liquid_alpha_enabled (bool): Flag indicating if liquid alpha is enabled.
        alpha_sigmoid_steepness (float):
        yuma_version (int): Version of yuma.
        subnet_is_active (bool): Indicates if subnet is active after START CALL.
        transfers_enabled (bool): Flag indicating if transfers are enabled.
        bonds_reset_enabled (bool): Flag indicating if bonds are reset enabled.
        user_liquidity_enabled (bool): Flag indicating if user liquidity is enabled.
    """

    rho: int
    kappa: int
    immunity_period: int
    min_allowed_weights: int
    max_weight_limit: float
    tempo: int
    min_difficulty: int
    max_difficulty: int
    weights_version: int
    weights_rate_limit: int
    adjustment_interval: int
    activity_cutoff: int
    registration_allowed: bool
    target_regs_per_interval: int
    min_burn: int
    max_burn: int
    bonds_moving_avg: int
    max_regs_per_block: int
    serving_rate_limit: int
    max_validators: int
    adjustment_alpha: int
    difficulty: int
    commit_reveal_period: int
    commit_reveal_weights_enabled: bool
    alpha_high: int
    alpha_low: int
    liquid_alpha_enabled: bool
    alpha_sigmoid_steepness: float
    yuma_version: int
    subnet_is_active: bool
    transfers_enabled: bool
    bonds_reset_enabled: bool
    user_liquidity_enabled: bool

    @classmethod
    def _fix_decoded(
        cls, decoded: Union[dict, "SubnetHyperparameters"]
    ) -> "SubnetHyperparameters":
        return cls(
            activity_cutoff=decoded["activity_cutoff"],
            adjustment_alpha=decoded["adjustment_alpha"],
            adjustment_interval=decoded["adjustment_interval"],
            alpha_high=decoded["alpha_high"],
            alpha_low=decoded["alpha_low"],
            alpha_sigmoid_steepness=fixed_to_float(
                decoded["alpha_sigmoid_steepness"], frac_bits=32
            ),
            bonds_moving_avg=decoded["bonds_moving_avg"],
            bonds_reset_enabled=decoded["bonds_reset_enabled"],
            commit_reveal_weights_enabled=decoded["commit_reveal_weights_enabled"],
            commit_reveal_period=decoded["commit_reveal_period"],
            difficulty=decoded["difficulty"],
            immunity_period=decoded["immunity_period"],
            kappa=decoded["kappa"],
            liquid_alpha_enabled=decoded["liquid_alpha_enabled"],
            max_burn=decoded["max_burn"],
            max_difficulty=decoded["max_difficulty"],
            max_regs_per_block=decoded["max_regs_per_block"],
            max_validators=decoded["max_validators"],
            max_weight_limit=decoded["max_weights_limit"],
            min_allowed_weights=decoded["min_allowed_weights"],
            min_burn=decoded["min_burn"],
            min_difficulty=decoded["min_difficulty"],
            registration_allowed=decoded["registration_allowed"],
            rho=decoded["rho"],
            serving_rate_limit=decoded["serving_rate_limit"],
            subnet_is_active=decoded["subnet_is_active"],
            target_regs_per_interval=decoded["target_regs_per_interval"],
            tempo=decoded["tempo"],
            transfers_enabled=decoded["transfers_enabled"],
            user_liquidity_enabled=decoded["user_liquidity_enabled"],
            weights_rate_limit=decoded["weights_rate_limit"],
            weights_version=decoded["weights_version"],
            yuma_version=decoded["yuma_version"],
        )


@dataclass
class StakeInfo(InfoBase):
    """Dataclass for stake info."""

    hotkey_ss58: str  # Hotkey address
    coldkey_ss58: str  # Coldkey address
    netuid: int
    stake: Balance  # Stake for the hotkey-coldkey pair
    locked: Balance  # Stake which is locked.
    emission: Balance  # Emission for the hotkey-coldkey pair
    tao_emission: Balance  # TAO emission for the hotkey-coldkey pair
    drain: int
    is_registered: bool

    @classmethod
    def _fix_decoded(cls, decoded: Any) -> "StakeInfo":
        hotkey = decode_account_id(decoded.get("hotkey"))
        coldkey = decode_account_id(decoded.get("coldkey"))
        netuid = int(decoded.get("netuid"))
        stake = Balance.from_rao(decoded.get("stake")).set_unit(netuid)
        locked = Balance.from_rao(decoded.get("locked")).set_unit(netuid)
        emission = Balance.from_rao(decoded.get("emission")).set_unit(netuid)
        tao_emission = Balance.from_rao(decoded.get("tao_emission"))
        drain = int(decoded.get("drain"))
        is_registered = bool(decoded.get("is_registered"))

        return cls(
            hotkey,
            coldkey,
            netuid,
            stake,
            locked,
            emission,
            tao_emission,
            drain,
            is_registered,
        )


@dataclass
class NeuronInfo(InfoBase):
    """Dataclass for neuron metadata."""

    hotkey: str
    coldkey: str
    uid: int
    netuid: int
    active: int
    stake: Balance
    # mapping of coldkey to amount staked to this Neuron
    stake_dict: dict[str, Balance]
    total_stake: Balance
    rank: float
    emission: float
    incentive: float
    consensus: float
    trust: float
    validator_trust: float
    dividends: float
    last_update: int
    validator_permit: bool
    weights: list[list[int]]
    bonds: list[list[int]]
    pruning_score: int
    axon_info: Optional[AxonInfo] = None
    is_null: bool = False

    @classmethod
    def from_weights_bonds_and_neuron_lite(
        cls,
        neuron_lite: "NeuronInfoLite",
        weights_as_dict: dict[int, list[tuple[int, int]]],
        bonds_as_dict: dict[int, list[tuple[int, int]]],
    ) -> "NeuronInfo":
        n_dict = neuron_lite.__dict__
        n_dict["weights"] = weights_as_dict.get(neuron_lite.uid, [])
        n_dict["bonds"] = bonds_as_dict.get(neuron_lite.uid, [])

        return cls(**n_dict)

    @staticmethod
    def get_null_neuron() -> "NeuronInfo":
        neuron = NeuronInfo(
            uid=0,
            netuid=0,
            active=0,
            stake=Balance.from_rao(0),
            stake_dict={},
            total_stake=Balance.from_rao(0),
            rank=0,
            emission=0,
            incentive=0,
            consensus=0,
            trust=0,
            validator_trust=0,
            dividends=0,
            last_update=0,
            validator_permit=False,
            weights=[],
            bonds=[],
            axon_info=None,
            is_null=True,
            coldkey="000000000000000000000000000000000000000000000000",
            hotkey="000000000000000000000000000000000000000000000000",
            pruning_score=0,
        )
        return neuron

    @classmethod
    def _fix_decoded(cls, decoded: Any) -> "NeuronInfo":
        netuid = decoded.get("netuid")
        stake_dict = process_stake_data(decoded.get("stake"), netuid=netuid)
        total_stake = sum(stake_dict.values()) if stake_dict else Balance(0)
        axon_info = decoded.get("axon_info", {})
        coldkey = decode_account_id(decoded.get("coldkey"))
        hotkey = decode_account_id(decoded.get("hotkey"))
        return cls(
            hotkey=hotkey,
            coldkey=coldkey,
            uid=decoded.get("uid"),
            netuid=netuid,
            active=decoded.get("active"),
            stake=total_stake,
            stake_dict=stake_dict,
            total_stake=total_stake,
            rank=u16tf(decoded.get("rank")),
            emission=decoded.get("emission") / 1e9,
            incentive=u16tf(decoded.get("incentive")),
            consensus=u16tf(decoded.get("consensus")),
            trust=u16tf(decoded.get("trust")),
            validator_trust=u16tf(decoded.get("validator_trust")),
            dividends=u16tf(decoded.get("dividends")),
            last_update=decoded.get("last_update"),
            validator_permit=decoded.get("validator_permit"),
            weights=[[e[0], e[1]] for e in decoded.get("weights")],
            bonds=[[e[0], e[1]] for e in decoded.get("bonds")],
            pruning_score=decoded.get("pruning_score"),
            axon_info=AxonInfo(
                version=axon_info.get("version"),
                ip=str(netaddr.IPAddress(axon_info.get("ip"))),
                port=axon_info.get("port"),
                ip_type=axon_info.get("ip_type"),
                placeholder1=axon_info.get("placeholder1"),
                placeholder2=axon_info.get("placeholder2"),
                protocol=axon_info.get("protocol"),
                hotkey=hotkey,
                coldkey=coldkey,
            ),
            is_null=False,
        )


@dataclass
class NeuronInfoLite(InfoBase):
    """Dataclass for neuron metadata, but without the weights and bonds."""

    hotkey: str
    coldkey: str
    uid: int
    netuid: int
    active: int
    stake: Balance
    # mapping of coldkey to amount staked to this Neuron
    stake_dict: dict[str, Balance]
    total_stake: Balance
    rank: float
    emission: float
    incentive: float
    consensus: float
    trust: float
    validator_trust: float
    dividends: float
    last_update: int
    validator_permit: bool
    axon_info: AxonInfo
    pruning_score: int
    is_null: bool = False

    @staticmethod
    def get_null_neuron() -> "NeuronInfoLite":
        neuron = NeuronInfoLite(
            uid=0,
            netuid=0,
            active=0,
            stake=Balance.from_rao(0),
            stake_dict={},
            total_stake=Balance.from_rao(0),
            rank=0,
            emission=0,
            incentive=0,
            consensus=0,
            trust=0,
            validator_trust=0,
            dividends=0,
            last_update=0,
            validator_permit=False,
            axon_info=None,
            is_null=True,
            coldkey="000000000000000000000000000000000000000000000000",
            hotkey="000000000000000000000000000000000000000000000000",
            pruning_score=0,
        )
        return neuron

    @classmethod
    def _fix_decoded(cls, decoded: Union[dict, "NeuronInfoLite"]) -> "NeuronInfoLite":
        active = decoded.get("active")
        axon_info = decoded.get("axon_info", {})
        coldkey = decode_account_id(decoded.get("coldkey"))
        consensus = decoded.get("consensus")
        dividends = decoded.get("dividends")
        emission = decoded.get("emission")
        hotkey = decode_account_id(decoded.get("hotkey"))
        incentive = decoded.get("incentive")
        last_update = decoded.get("last_update")
        netuid = decoded.get("netuid")
        pruning_score = decoded.get("pruning_score")
        rank = decoded.get("rank")
        stake_dict = process_stake_data(decoded.get("stake"), netuid)
        stake = sum(stake_dict.values()) if stake_dict else Balance(0)
        trust = decoded.get("trust")
        uid = decoded.get("uid")
        validator_permit = decoded.get("validator_permit")
        validator_trust = decoded.get("validator_trust")

        neuron = cls(
            active=active,
            axon_info=AxonInfo(
                version=axon_info.get("version"),
                ip=str(netaddr.IPAddress(axon_info.get("ip"))),
                port=axon_info.get("port"),
                ip_type=axon_info.get("ip_type"),
                placeholder1=axon_info.get("placeholder1"),
                placeholder2=axon_info.get("placeholder2"),
                protocol=axon_info.get("protocol"),
                hotkey=hotkey,
                coldkey=coldkey,
            ),
            coldkey=coldkey,
            consensus=u16tf(consensus),
            dividends=u16tf(dividends),
            emission=emission / 1e9,
            hotkey=hotkey,
            incentive=u16tf(incentive),
            last_update=last_update,
            netuid=netuid,
            pruning_score=pruning_score,
            rank=u16tf(rank),
            stake_dict=stake_dict,
            stake=stake,
            total_stake=stake,
            trust=u16tf(trust),
            uid=uid,
            validator_permit=validator_permit,
            validator_trust=u16tf(validator_trust),
        )

        return neuron


@dataclass
class DelegateInfo(InfoBase):
    """
    Dataclass for delegate information. For a lighter version of this class, see :func:`DelegateInfoLite`.

    :param hotkey_ss58: Hotkey of the delegate for which the information is being fetched.
    :param total_stake: Total stake of the delegate.
    :param nominators: list of nominators of the delegate and their stake.
    :param take: Take of the delegate as a percentage.
    :param owner_ss58: Coldkey of the owner.
    :param registrations: list of subnets that the delegate is registered on.
    :param validator_permits: list of subnets that the delegate is allowed to validate on.
    :param return_per_1000: Return per 1000 TAO, for the delegate over a day.
    :param total_daily_return: Total daily return of the delegate.

    """

    hotkey_ss58: str  # Hotkey of delegate
    total_stake: Balance  # Total stake of the delegate
    nominators: list[
        tuple[str, Balance]
    ]  # list of nominators of the delegate and their stake
    owner_ss58: str  # Coldkey of owner
    take: float  # Take of the delegate as a percentage
    validator_permits: list[
        int
    ]  # list of subnets that the delegate is allowed to validate on
    registrations: list[int]  # list of subnets that the delegate is registered on
    return_per_1000: Balance  # Return per 1000 tao of the delegate over a day
    total_daily_return: Balance  # Total daily return of the delegate

    @classmethod
    def _fix_decoded(cls, decoded: "DelegateInfo") -> "DelegateInfo":
        hotkey = decode_account_id(decoded.get("hotkey_ss58"))
        owner = decode_account_id(decoded.get("owner_ss58"))
        nominators = [
            (decode_account_id(x), Balance.from_rao(y))
            for x, y in decoded.get("nominators")
        ]
        total_stake = sum((x[1] for x in nominators)) if nominators else Balance(0)
        return cls(
            hotkey_ss58=hotkey,
            total_stake=total_stake,
            nominators=nominators,
            owner_ss58=owner,
            take=u16tf(decoded.get("take")),
            validator_permits=decoded.get("validator_permits"),
            registrations=decoded.get("registrations"),
            return_per_1000=Balance.from_rao(decoded.get("return_per_1000")),
            total_daily_return=Balance.from_rao(decoded.get("total_daily_return")),
        )


@dataclass
class DelegateInfoLite(InfoBase):
    """
    Dataclass for light delegate information.

    Args:
        hotkey_ss58 (str): Hotkey of the delegate for which the information is being fetched.
        owner_ss58 (str): Coldkey of the owner.
        total_stake (int): Total stake of the delegate.
        owner_stake (int): Own stake of the delegate.
        take (float): Take of the delegate as a percentage. None if custom
    """

    hotkey_ss58: str  # Hotkey of delegate
    owner_ss58: str  # Coldkey of owner
    take: Optional[float]
    total_stake: Balance  # Total stake of the delegate
    previous_total_stake: Optional[Balance]  # Total stake of the delegate
    owner_stake: Balance  # Own stake of the delegate

    @classmethod
    def _fix_decoded(cls, decoded: Any) -> "DelegateInfoLite":
        """Fixes the decoded values."""
        decoded_take = decoded.get("take")

        if decoded_take == 65535:
            fixed_take = None
        else:
            fixed_take = u16tf(decoded_take)

        return cls(
            hotkey_ss58=ss58_encode(decoded.get("delegate_ss58"), SS58_FORMAT),
            owner_ss58=ss58_encode(decoded.get("owner_ss58"), SS58_FORMAT),
            take=fixed_take,
            total_stake=Balance.from_rao(decoded.get("total_stake")),
            owner_stake=Balance.from_rao(decoded.get("owner_stake")),
            previous_total_stake=None,
        )


@dataclass
class SubnetInfo(InfoBase):
    """Dataclass for subnet info."""

    netuid: int
    rho: int
    kappa: int
    difficulty: int
    immunity_period: int
    max_allowed_validators: int
    min_allowed_weights: int
    max_weights_limit: float
    scaling_law_power: float
    subnetwork_n: int
    max_n: int
    blocks_since_epoch: int
    tempo: int
    modality: int
    connection_requirements: dict[str, float]
    emission_value: float
    burn: Balance
    owner_ss58: str

    @classmethod
    def _fix_decoded(cls, decoded: "SubnetInfo") -> "SubnetInfo":
        return cls(
            netuid=decoded.get("netuid"),
            rho=decoded.get("rho"),
            kappa=decoded.get("kappa"),
            difficulty=decoded.get("difficulty"),
            immunity_period=decoded.get("immunity_period"),
            max_allowed_validators=decoded.get("max_allowed_validators"),
            min_allowed_weights=decoded.get("min_allowed_weights"),
            max_weights_limit=decoded.get("max_weights_limit"),
            scaling_law_power=decoded.get("scaling_law_power"),
            subnetwork_n=decoded.get("subnetwork_n"),
            max_n=decoded.get("max_allowed_uids"),
            blocks_since_epoch=decoded.get("blocks_since_last_step"),
            tempo=decoded.get("tempo"),
            modality=decoded.get("network_modality"),
            connection_requirements={
                str(int(netuid)): u16tf(int(req))
                for (netuid, req) in decoded.get("network_connect")
            },
            emission_value=decoded.get("emission_value"),
            burn=Balance.from_rao(decoded.get("burn")),
            owner_ss58=decode_account_id(decoded.get("owner")),
        )


@dataclass
class SubnetIdentity(InfoBase):
    """Dataclass for subnet identity information."""

    subnet_name: str
    github_repo: str
    subnet_contact: str
    subnet_url: str
    discord: str
    description: str
    logo_url: str
    additional: str

    @classmethod
    def _fix_decoded(cls, decoded: dict) -> "SubnetIdentity":
        return cls(
            subnet_name=decoded["subnet_name"],
            github_repo=decoded["github_repo"],
            subnet_contact=decoded["subnet_contact"],
            subnet_url=decoded["subnet_url"],
            discord=decoded["discord"],
            description=decoded["description"],
            logo_url=decoded["logo_url"],
            additional=decoded["additional"],
        )


@dataclass
class DynamicInfo(InfoBase):
    netuid: int
    owner_hotkey: str
    owner_coldkey: str
    subnet_name: str
    symbol: str
    tempo: int
    last_step: int
    blocks_since_last_step: int
    emission: Balance
    alpha_in: Balance
    alpha_out: Balance
    tao_in: Balance
    price: Balance
    k: float
    is_dynamic: bool
    alpha_out_emission: Balance
    alpha_in_emission: Balance
    tao_in_emission: Balance
    pending_alpha_emission: Balance
    pending_root_emission: Balance
    network_registered_at: int
    subnet_identity: Optional[SubnetIdentity]
    subnet_volume: Balance
    moving_price: float

    @classmethod
    def _fix_decoded(cls, decoded: Any) -> "DynamicInfo":
        """Returns a DynamicInfo object from a decoded DynamicInfo dictionary."""

        netuid = int(decoded.get("netuid"))
        symbol = bytes([int(b) for b in decoded.get("token_symbol")]).decode()
        subnet_name = bytes([int(b) for b in decoded.get("subnet_name")]).decode()
        is_dynamic = True if netuid > 0 else False  # Patching for netuid 0

        owner_hotkey = decode_account_id(decoded.get("owner_hotkey"))
        owner_coldkey = decode_account_id(decoded.get("owner_coldkey"))

        emission = Balance.from_rao(decoded.get("emission")).set_unit(0)
        alpha_in = Balance.from_rao(decoded.get("alpha_in")).set_unit(netuid)
        alpha_out = Balance.from_rao(decoded.get("alpha_out")).set_unit(netuid)
        tao_in = Balance.from_rao(decoded.get("tao_in")).set_unit(0)
        alpha_out_emission = Balance.from_rao(
            decoded.get("alpha_out_emission")
        ).set_unit(netuid)
        alpha_in_emission = Balance.from_rao(decoded.get("alpha_in_emission")).set_unit(
            netuid
        )
        subnet_volume = Balance.from_rao(decoded.get("subnet_volume")).set_unit(netuid)
        tao_in_emission = Balance.from_rao(decoded.get("tao_in_emission")).set_unit(0)
        pending_alpha_emission = Balance.from_rao(
            decoded.get("pending_alpha_emission")
        ).set_unit(netuid)
        pending_root_emission = Balance.from_rao(
            decoded.get("pending_root_emission")
        ).set_unit(0)
        price = (
            Balance.from_tao(1.0)
            if netuid == 0
            else Balance.from_tao(tao_in.tao / alpha_in.tao)
            if alpha_in.tao > 0
            else Balance.from_tao(1)
        )  # TODO: Patching this temporarily for netuid 0

        if decoded.get("subnet_identity"):
            subnet_identity = SubnetIdentity.from_any(decoded.get("subnet_identity"))
        else:
            subnet_identity = None

        return cls(
            netuid=netuid,
            owner_hotkey=owner_hotkey,
            owner_coldkey=owner_coldkey,
            subnet_name=subnet_name,
            symbol=symbol,
            tempo=int(decoded.get("tempo")),
            last_step=int(decoded.get("last_step")),
            blocks_since_last_step=int(decoded.get("blocks_since_last_step")),
            emission=emission,
            alpha_in=alpha_in,
            alpha_out=alpha_out,
            tao_in=tao_in,
            k=tao_in.rao * alpha_in.rao,
            is_dynamic=is_dynamic,
            price=price,
            alpha_out_emission=alpha_out_emission,
            alpha_in_emission=alpha_in_emission,
            tao_in_emission=tao_in_emission,
            pending_alpha_emission=pending_alpha_emission,
            pending_root_emission=pending_root_emission,
            network_registered_at=int(decoded.get("network_registered_at")),
            subnet_identity=subnet_identity,
            subnet_volume=subnet_volume,
            moving_price=fixed_to_float(decoded["moving_price"], 32),
        )

    def tao_to_alpha(self, tao: Balance) -> Balance:
        if self.price.tao != 0:
            return Balance.from_tao(tao.tao / self.price.tao).set_unit(self.netuid)
        else:
            return Balance.from_tao(0)

    def alpha_to_tao(self, alpha: Balance) -> Balance:
        return Balance.from_tao(alpha.tao * self.price.tao)

    def tao_to_alpha_with_slippage(
        self, tao: Balance
    ) -> tuple[Balance, Balance, float]:
        """
        Returns an estimate of how much Alpha a staker would receive if they stake their tao using the current pool
            state.

        Args:
            tao: Amount of TAO to stake.
        Returns:
            Tuple of balances where the first part is the amount of Alpha received, and the
            second part (slippage) is the difference between the estimated amount and ideal
            amount as if there was no slippage
        """
        if self.is_dynamic:
            new_tao_in = self.tao_in + tao
            if new_tao_in == 0:
                return tao, Balance.from_rao(0)
            new_alpha_in = self.k / new_tao_in

            # Amount of alpha given to the staker
            alpha_returned = Balance.from_rao(
                self.alpha_in.rao - new_alpha_in.rao
            ).set_unit(self.netuid)

            # Ideal conversion as if there is no slippage, just price
            alpha_ideal = self.tao_to_alpha(tao)

            if alpha_ideal.tao > alpha_returned.tao:
                slippage = Balance.from_tao(
                    alpha_ideal.tao - alpha_returned.tao
                ).set_unit(self.netuid)
            else:
                slippage = Balance.from_tao(0)
        else:
            alpha_returned = tao.set_unit(self.netuid)
            slippage = Balance.from_tao(0)

        slippage_pct_float = (
            100 * float(slippage) / float(slippage + alpha_returned)
            if slippage + alpha_returned != 0
            else 0
        )
        return alpha_returned, slippage, slippage_pct_float

    def alpha_to_tao_with_slippage(
        self, alpha: Balance
    ) -> tuple[Balance, Balance, float]:
        """
        Returns an estimate of how much TAO a staker would receive if they unstake their alpha using the current pool
            state.

        Args:
            alpha: Amount of Alpha to stake.
        Returns:
            Tuple of balances where the first part is the amount of TAO received, and the
            second part (slippage) is the difference between the estimated amount and ideal
            amount as if there was no slippage
        """
        if self.is_dynamic:
            new_alpha_in = self.alpha_in + alpha
            new_tao_reserve = self.k / new_alpha_in
            # Amount of TAO given to the unstaker
            tao_returned = Balance.from_rao(self.tao_in - new_tao_reserve)

            # Ideal conversion as if there is no slippage, just price
            tao_ideal = self.alpha_to_tao(alpha)

            if tao_ideal > tao_returned:
                slippage = Balance.from_tao(tao_ideal.tao - tao_returned.tao)
            else:
                slippage = Balance.from_tao(0)
        else:
            tao_returned = alpha.set_unit(0)
            slippage = Balance.from_tao(0)
        slippage_pct_float = (
            100 * float(slippage) / float(slippage + tao_returned)
            if slippage + tao_returned != 0
            else 0
        )
        return tao_returned, slippage, slippage_pct_float


@dataclass
class ColdkeySwapAnnouncementInfo(InfoBase):
    """
    Information about a coldkey swap announcement.

    Contains information about a pending coldkey swap announcement when a coldkey
    wants to declare its intent to swap to a new coldkey address.
    The announcement is made before the actual swap can be executed,
    allowing time for verification and security checks.

    The destination coldkey address is stored as a hash.
    This is to prevent the actual coldkey address from being exposed
    to the network. The hash is computed using the BlakeTwo256 hashing algorithm.
    """

    coldkey: str
    execution_block: int
    new_coldkey_hash: str

    @classmethod
    def _fix_decoded(
        cls, coldkey: str, decoded: tuple
    ) -> "ColdkeySwapAnnouncementInfo":
        execution_block, new_coldkey_hash = decoded
        hash_str = "0x" + bytes(new_coldkey_hash[0]).hex()
        return cls(
            coldkey=coldkey,
            execution_block=int(execution_block),
            new_coldkey_hash=hash_str,
        )


@dataclass
class SubnetState(InfoBase):
    netuid: int
    hotkeys: list[str]
    coldkeys: list[str]
    active: list[bool]
    validator_permit: list[bool]
    pruning_score: list[float]
    last_update: list[int]
    emission: list[Balance]
    dividends: list[float]
    incentives: list[float]
    consensus: list[float]
    trust: list[float]
    rank: list[float]
    block_at_registration: list[int]
    alpha_stake: list[Balance]
    tao_stake: list[Balance]
    total_stake: list[Balance]
    emission_history: list[list[int]]

    @classmethod
    def _fix_decoded(cls, decoded: Any) -> "SubnetState":
        netuid = decoded.get("netuid")
        return cls(
            netuid=netuid,
            hotkeys=[decode_account_id(val) for val in decoded.get("hotkeys")],
            coldkeys=[decode_account_id(val) for val in decoded.get("coldkeys")],
            active=decoded.get("active"),
            validator_permit=decoded.get("validator_permit"),
            pruning_score=[u16tf(val) for val in decoded.get("pruning_score")],
            last_update=decoded.get("last_update"),
            emission=[
                Balance.from_rao(val).set_unit(netuid)
                for val in decoded.get("emission")
            ],
            dividends=[u16tf(val) for val in decoded.get("dividends")],
            incentives=[u16tf(val) for val in decoded.get("incentives")],
            consensus=[u16tf(val) for val in decoded.get("consensus")],
            trust=[u16tf(val) for val in decoded.get("trust")],
            rank=[u16tf(val) for val in decoded.get("rank")],
            block_at_registration=decoded.get("block_at_registration"),
            alpha_stake=[
                Balance.from_rao(val).set_unit(netuid)
                for val in decoded.get("alpha_stake")
            ],
            tao_stake=[
                Balance.from_rao(val).set_unit(0) for val in decoded.get("tao_stake")
            ],
            total_stake=[
                Balance.from_rao(val).set_unit(netuid)
                for val in decoded.get("total_stake")
            ],
            emission_history=decoded.get("emission_history"),
        )


@dataclass
class ChainIdentity(InfoBase):
    """Dataclass for chain identity information."""

    name: str
    url: str
    github: str
    image: str
    discord: str
    description: str
    additional: str

    @classmethod
    def _from_dict(cls, decoded: dict) -> "ChainIdentity":
        """Returns a ChainIdentity object from decoded chain data."""
        return cls(
            name=decoded["name"],
            url=decoded["url"],
            github=decoded["github_repo"],
            image=decoded["image"],
            discord=decoded["discord"],
            description=decoded["description"],
            additional=decoded["additional"],
        )


@dataclass
class MetagraphInfo(InfoBase):
    # Subnet index
    netuid: int

    # Name and symbol
    name: str
    symbol: str
    identity: Optional[SubnetIdentity]
    network_registered_at: int

    # Keys for owner.
    owner_hotkey: str  # hotkey
    owner_coldkey: str  # coldkey

    # Tempo terms.
    block: int  # block at call.
    tempo: int  # epoch tempo
    last_step: int
    blocks_since_last_step: int

    # Subnet emission terms
    subnet_emission: Balance  # subnet emission via tao
    alpha_in: Balance  # amount of alpha in reserve
    alpha_out: Balance  # amount of alpha outstanding
    tao_in: Balance  # amount of tao injected per block
    alpha_out_emission: Balance  # amount injected in alpha reserves per block
    alpha_in_emission: Balance  # amount injected outstanding per block
    tao_in_emission: Balance  # amount of tao injected per block
    pending_alpha_emission: Balance  # pending alpha to be distributed
    pending_root_emission: Balance  # pending tao for root divs to be distributed
    subnet_volume: Balance  # volume of the subnet in TAO
    moving_price: Balance  # subnet moving price.

    # Hparams for epoch
    rho: int  # subnet rho param
    kappa: float  # subnet kappa param

    # Validator params
    min_allowed_weights: float  # min allowed weights per val
    max_weights_limit: float  # max allowed weights per val
    weights_version: int  # allowed weights version
    weights_rate_limit: int  # rate limit on weights.
    activity_cutoff: int  # validator weights cut off period in blocks
    max_validators: int  # max allowed validators.

    # Registration
    num_uids: int
    max_uids: int
    burn: Balance  # current burn cost.
    difficulty: float  # current difficulty.
    registration_allowed: bool  # allows registrations.
    pow_registration_allowed: bool  # pow registration enabled.
    immunity_period: int  # subnet miner immunity period
    min_difficulty: float  # min pow difficulty
    max_difficulty: float  # max pow difficulty
    min_burn: Balance  # min tao burn
    max_burn: Balance  # max tao burn
    adjustment_alpha: float  # adjustment speed for registration params.
    adjustment_interval: int  # pow and burn adjustment interval
    target_regs_per_interval: int  # target registrations per interval
    max_regs_per_block: int  # max registrations per block.
    serving_rate_limit: int  # axon serving rate limit

    # CR
    commit_reveal_weights_enabled: bool  # Is CR enabled.
    commit_reveal_period: int  # Commit reveal interval

    # Bonds
    liquid_alpha_enabled: bool  # Bonds liquid enabled.
    alpha_high: float  # Alpha param high
    alpha_low: float  # Alpha param low
    bonds_moving_avg: float  # Bonds moving avg

    # Metagraph info.
    hotkeys: list[str]  # hotkey per UID
    coldkeys: list[str]  # coldkey per UID
    identities: list[Optional[ChainIdentity]]  # coldkeys identities
    axons: list[AxonInfo]  # UID axons.
    active: list[bool]  # Active per UID
    validator_permit: list[bool]  # Val permit per UID
    pruning_score: list[float]  # Pruning per UID
    last_update: list[int]  # Last update per UID
    emission: list[Balance]  # Emission per UID
    dividends: list[float]  # Dividends per UID
    incentives: list[float]  # Mining incentives per UID
    consensus: list[float]  # Consensus per UID
    trust: list[float]  # Trust per UID
    rank: list[float]  # Rank per UID
    block_at_registration: list[int]  # Reg block per UID
    alpha_stake: list[Balance]  # Alpha staked per UID
    tao_stake: list[Balance]  # TAO staked per UID
    total_stake: list[Balance]  # Total stake per UID

    # Dividend break down.
    tao_dividends_per_hotkey: list[
        tuple[str, Balance]
    ]  # List of dividend payouts in tao via root.
    alpha_dividends_per_hotkey: list[
        tuple[str, Balance]
    ]  # List of dividend payout in alpha via subnet.
    subuid: int = 0

    @classmethod
    def _fix_decoded(cls, decoded: dict) -> "MetagraphInfo":
        """Returns a MetagraphInfo object from decoded chain data."""
        # Subnet index
        _netuid, _subuid = get_netuid_and_subuid_by_storage_index(decoded["netuid"])

        # Name and symbol
        decoded.update({"name": bytes(decoded.get("name")).decode()})
        decoded.update({"symbol": bytes(decoded.get("symbol")).decode()})

        return cls(
            # Subnet index
            netuid=_netuid,
            subuid=_subuid,
            # Name and symbol
            name=decoded["name"],
            symbol=decoded["symbol"],
            identity=decoded["identity"],
            network_registered_at=decoded["network_registered_at"],
            # Keys for owner.
            owner_hotkey=decoded["owner_hotkey"],
            owner_coldkey=decoded["owner_coldkey"],
            # Tempo terms.
            block=decoded["block"],
            tempo=decoded["tempo"],
            last_step=decoded["last_step"],
            blocks_since_last_step=decoded["blocks_since_last_step"],
            # Subnet emission terms
            subnet_emission=_tbwu(decoded["subnet_emission"]),
            alpha_in=_tbwu(decoded["alpha_in"], _netuid),
            alpha_out=_tbwu(decoded["alpha_out"], _netuid),
            tao_in=_tbwu(decoded["tao_in"]),
            alpha_out_emission=_tbwu(decoded["alpha_out_emission"], _netuid),
            alpha_in_emission=_tbwu(decoded["alpha_in_emission"], _netuid),
            tao_in_emission=_tbwu(decoded["tao_in_emission"]),
            pending_alpha_emission=_tbwu(decoded["pending_alpha_emission"], _netuid),
            pending_root_emission=_tbwu(decoded["pending_root_emission"]),
            subnet_volume=_tbwu(decoded["subnet_volume"], _netuid),
            moving_price=Balance.from_tao(
                fixed_to_float(decoded.get("moving_price"), 32)
            ),
            # Hparams for epoch
            rho=decoded["rho"],
            kappa=decoded["kappa"],
            # Validator params
            min_allowed_weights=u16tf(decoded["min_allowed_weights"]),
            max_weights_limit=u16tf(decoded["max_weights_limit"]),
            weights_version=decoded["weights_version"],
            weights_rate_limit=decoded["weights_rate_limit"],
            activity_cutoff=decoded["activity_cutoff"],
            max_validators=decoded["max_validators"],
            # Registration
            num_uids=decoded["num_uids"],
            max_uids=decoded["max_uids"],
            burn=_tbwu(decoded["burn"]),
            difficulty=u64tf(decoded["difficulty"]),
            registration_allowed=decoded["registration_allowed"],
            pow_registration_allowed=decoded["pow_registration_allowed"],
            immunity_period=decoded["immunity_period"],
            min_difficulty=u64tf(decoded["min_difficulty"]),
            max_difficulty=u64tf(decoded["max_difficulty"]),
            min_burn=_tbwu(decoded["min_burn"]),
            max_burn=_tbwu(decoded["max_burn"]),
            adjustment_alpha=u64tf(decoded["adjustment_alpha"]),
            adjustment_interval=decoded["adjustment_interval"],
            target_regs_per_interval=decoded["target_regs_per_interval"],
            max_regs_per_block=decoded["max_regs_per_block"],
            serving_rate_limit=decoded["serving_rate_limit"],
            # CR
            commit_reveal_weights_enabled=decoded["commit_reveal_weights_enabled"],
            commit_reveal_period=decoded["commit_reveal_period"],
            # Bonds
            liquid_alpha_enabled=decoded["liquid_alpha_enabled"],
            alpha_high=u16tf(decoded["alpha_high"]),
            alpha_low=u16tf(decoded["alpha_low"]),
            bonds_moving_avg=u64tf(decoded["bonds_moving_avg"]),
            # Metagraph info.
            hotkeys=[decode_account_id(ck) for ck in decoded.get("hotkeys", [])],
            coldkeys=[decode_account_id(hk) for hk in decoded.get("coldkeys", [])],
            identities=decoded["identities"],
            axons=decoded.get("axons", []),
            active=decoded["active"],
            validator_permit=decoded["validator_permit"],
            pruning_score=[u16tf(ps) for ps in decoded.get("pruning_score", [])],
            last_update=decoded["last_update"],
            emission=[_tbwu(em, _netuid) for em in decoded.get("emission", [])],
            dividends=[u16tf(dv) for dv in decoded.get("dividends", [])],
            incentives=[u16tf(ic) for ic in decoded.get("incentives", [])],
            consensus=[u16tf(cs) for cs in decoded.get("consensus", [])],
            trust=[u16tf(tr) for tr in decoded.get("trust", [])],
            rank=[u16tf(rk) for rk in decoded.get("rank", [])],
            block_at_registration=decoded["block_at_registration"],
            alpha_stake=[_tbwu(ast, _netuid) for ast in decoded["alpha_stake"]],
            tao_stake=[_tbwu(ts) for ts in decoded["tao_stake"]],
            total_stake=[_tbwu(ts, _netuid) for ts in decoded["total_stake"]],
            # Dividend break down
            tao_dividends_per_hotkey=[
                (decode_account_id(alpha[0]), _tbwu(alpha[1]))
                for alpha in decoded["tao_dividends_per_hotkey"]
            ],
            alpha_dividends_per_hotkey=[
                (decode_account_id(adphk[0]), _tbwu(adphk[1], _netuid))
                for adphk in decoded["alpha_dividends_per_hotkey"]
            ],
        )


@dataclass
class SimSwapResult:
    tao_amount: Balance
    alpha_amount: Balance
    tao_fee: Balance
    alpha_fee: Balance

    @classmethod
    def from_dict(cls, d: dict, netuid: int) -> "SimSwapResult":
        return cls(
            tao_amount=Balance.from_rao(d["tao_amount"]).set_unit(0),
            alpha_amount=Balance.from_rao(d["alpha_amount"]).set_unit(netuid),
            tao_fee=Balance.from_rao(d["tao_fee"]).set_unit(0),
            alpha_fee=Balance.from_rao(d["alpha_fee"]).set_unit(netuid),
        )


@dataclass
class CrowdloanData(InfoBase):
    creator: Optional[str]
    funds_account: Optional[str]
    deposit: Balance
    min_contribution: Balance
    cap: Balance
    raised: Balance
    end: int
    finalized: bool
    contributors_count: int
    target_address: Optional[str]
    has_call: bool
    call_details: Optional[dict] = None

    @classmethod
    def _fix_decoded(cls, decoded: dict[str, Any]) -> "CrowdloanData":
        creator = (
            decode_account_id(creator_raw)
            if (creator_raw := decoded.get("creator"))
            else None
        )
        funds_account = (
            decode_account_id(funds_raw)
            if (funds_raw := decoded.get("funds_account"))
            else None
        )
        target_address = (
            decode_account_id(target_raw)
            if (target_raw := decoded.get("target_address"))
            else None
        )
        return cls(
            creator=creator,
            funds_account=funds_account,
            deposit=Balance.from_rao(int(decoded["deposit"])),
            min_contribution=Balance.from_rao(int(decoded["min_contribution"])),
            cap=Balance.from_rao(int(decoded["cap"])),
            raised=Balance.from_rao(int(decoded["raised"])),
            end=int(decoded["end"]),
            finalized=bool(decoded["finalized"]),
            contributors_count=int(decoded["contributors_count"]),
            target_address=target_address,
            has_call=bool(decoded["call"]),
            call_details=decoded["call_details"],
        )
