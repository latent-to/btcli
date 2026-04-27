import asyncio

import numpy as np
from numpy.typing import NDArray

from bittensor_cli.src.bittensor.chain_data import NeuronInfo, SubnetState
from bittensor_cli.src.bittensor.subtensor_interface import SubtensorInterface
from bittensor_cli.src.bittensor.utils import (
    convert_root_weight_uids_and_vals_to_tensor,
    convert_weight_uids_and_vals_to_tensor,
    convert_bond_uids_and_vals_to_tensor,
)


class MiniGraph:
    def __init__(
        self,
        netuid: int,
        neurons: list[NeuronInfo],
        subtensor: "SubtensorInterface",
        subnet_state: "SubnetState",
        block: int,
    ):
        self.neurons = neurons
        self.netuid = netuid
        self.weights = None
        self.subtensor = subtensor
        self.network = subtensor.network

        self.axons = [n.axon_info for n in self.neurons]
        self.n = self._create_tensor(len(self.neurons), dtype=np.int64)
        self.block = self._create_tensor(block, dtype=np.int64)
        self.uids = self._create_tensor(
            [neuron.uid for neuron in self.neurons], dtype=np.int64
        )
        self.trust = self._create_tensor(
            [neuron.trust for neuron in self.neurons], dtype=np.float32
        )
        self.consensus = self._create_tensor(
            [neuron.consensus for neuron in self.neurons], dtype=np.float32
        )
        self.incentive = self._create_tensor(
            [neuron.incentive for neuron in self.neurons], dtype=np.float32
        )
        self.dividends = self._create_tensor(
            [neuron.dividends for neuron in self.neurons], dtype=np.float32
        )
        self.ranks = self._create_tensor(
            [neuron.rank for neuron in self.neurons], dtype=np.float32
        )
        self.emission = self._create_tensor(
            [neuron.emission for neuron in self.neurons], dtype=np.float32
        )
        self.active = self._create_tensor(
            [neuron.active for neuron in self.neurons], dtype=np.int64
        )
        self.last_update = self._create_tensor(
            [neuron.last_update for neuron in self.neurons], dtype=np.int64
        )
        self.validator_permit = self._create_tensor(
            [neuron.validator_permit for neuron in self.neurons], dtype=bool
        )
        self.validator_trust = self._create_tensor(
            [neuron.validator_trust for neuron in self.neurons], dtype=np.float32
        )

        # Fetch stakes from subnet_state until we get updated data in NeuronInfo
        global_stake_list, local_stake_list, stake_weights_list = self._process_stakes(
            neurons, subnet_state
        )
        self.global_stake = self._create_tensor(global_stake_list, dtype=np.float32)
        self.local_stake = self._create_tensor(local_stake_list, dtype=np.float32)
        self.stake_weights = self._create_tensor(stake_weights_list, dtype=np.float32)

    async def __aenter__(self):
        if not self.weights:
            await self._set_weights_and_bonds()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    @property
    def hotkeys(self):
        return [axon.hotkey for axon in self.axons]

    @staticmethod
    def _create_tensor(data, dtype) -> NDArray:
        """
        Creates a numpy array with the given data and data type. This method is a utility function used internally to encapsulate data into a np.array, making it compatible with the metagraph's numpy model structure.

        Args:
            data: The data to be included in the tensor. This could be any numeric data, like stakes, ranks, etc.
            dtype: The data type for the tensor, typically a numpy data type like ``np.float32`` or ``np.int64``.

        Returns:
            A tensor parameter encapsulating the provided data.

        Internal Usage:
            Used internally to create tensor parameters for various metagraph attributes::

                self.stake = self._create_tensor(neuron_stakes, dtype=np.float32)
        """
        # TODO: Check and test the creation of tensor
        return np.array(data, dtype=dtype)

    async def _set_weights_and_bonds(self):
        """
        Computes and sets the weights and bonds for each neuron in the metagraph. This method is responsible for
        processing the raw weight and bond data obtained from the network and converting it into a structured format
        suitable for the metagraph model.
        """
        # TODO: Check and test the computation of weights and bonds
        if self.netuid == 0:
            self.weights = await self._process_root_weights(
                [neuron.weights for neuron in self.neurons], "weights"
            )
        else:
            self.weights = self._process_weights_or_bonds(
                [neuron.weights for neuron in self.neurons], "weights"
            )
            self.bonds = self._process_weights_or_bonds(
                [neuron.bonds for neuron in self.neurons], "bonds"
            )

    def _process_stakes(
        self,
        neurons: list[NeuronInfo],
        subnet_state: SubnetState,
    ) -> tuple[list[float], list[float], list[float]]:
        """
        Processes the global_stake, local_stake, and stake_weights based on the neuron's hotkey.

        Args:
            neurons (List[NeuronInfo]): List of neurons.
            subnet_state (SubnetState): The subnet state containing stake information.

        Returns:
            tuple[list[float], list[float], list[float]]: Lists of global_stake, local_stake, and stake_weights.
        """
        global_stake_list = []
        local_stake_list = []
        stake_weights_list = []
        hotkey_to_index = {
            hotkey: idx for idx, hotkey in enumerate(subnet_state.hotkeys)
        }

        for neuron in neurons:
            idx = hotkey_to_index.get(neuron.hotkey)
            if idx is not None:
                global_stake_list.append(subnet_state.global_stake[idx].tao)
                local_stake_list.append(subnet_state.local_stake[idx].tao)
                stake_weights_list.append(subnet_state.stake_weight[idx])
            else:
                global_stake_list.append(0.0)
                local_stake_list.append(0.0)
                stake_weights_list.append(0.0)

        return global_stake_list, local_stake_list, stake_weights_list

    def _process_weights_or_bonds(self, data, attribute: str) -> NDArray:
        """
        Processes the raw weights or bonds data and converts it into a structured tensor format. This method handles
        the transformation of neuron connection data (`weights` or `bonds`) from a list or other unstructured
        format into a tensor that can be utilized within the metagraph model.

        :param data: The raw weights or bonds data to be processed. This data typically comes from the subtensor.
                     attribute: A string indicating whether the data is `weights` or `bonds`, which determines the
                     specific processing steps to be applied.

        :return: A tensor parameter encapsulating the processed weights or bonds data.
        """
        data_array = []
        for item in data:
            if len(item) == 0:
                data_array.append(np.zeros(len(self.neurons), dtype=np.float32))  # type: ignore
            else:
                uids, values = zip(*item)
                # TODO: Validate and test the conversion of uids and values to tensor
                if attribute == "weights":
                    data_array.append(
                        convert_weight_uids_and_vals_to_tensor(
                            len(self.neurons),
                            list(uids),
                            list(values),  # type: ignore
                        )
                    )
                else:
                    data_array.append(
                        convert_bond_uids_and_vals_to_tensor(  # type: ignore
                            len(self.neurons), list(uids), list(values)
                        ).astype(np.float32)
                    )
        tensor_param: NDArray = (
            np.stack(data_array) if len(data_array) else np.array([], dtype=np.float32)
        )
        if len(data_array) == 0:
            # bittensor.logging.warning(
            #     f"Empty {attribute}_array on metagraph.sync(). The '{attribute}' tensor is empty."
            # )
            pass
        return tensor_param

    async def _process_root_weights(self, data, attribute: str) -> NDArray:
        """
        Specifically processes the root weights data for the metagraph. This method is similar to
        `_process_weights_or_bonds` but is tailored for processing root weights, which have a different structure and
        significance in the network.

        Args:
        :param data: The raw root weights data to be processed.
        :param attribute: A string indicating the attribute type, here it's typically `weights`.

        :return: A tensor parameter encapsulating the processed root weights data.
        """

        async def get_total_subnets():
            _result = await self.subtensor.query(
                module="SubtensorModule",
                storage_function="TotalNetworks",
                params=[],
            )
            return _result

        async def get_subnets():
            _result = await self.subtensor.query(
                module="SubtensorModule",
                storage_function="TotalNetworks",
            )
            return [i for i in range(_result)]

        data_array = []
        n_subnets, subnets = await asyncio.gather(get_total_subnets(), get_subnets())
        for item in data:
            if len(item) == 0:
                data_array.append(np.zeros(n_subnets, dtype=np.float32))  # type: ignore
            else:
                uids, values = zip(*item)
                data_array.append(
                    convert_root_weight_uids_and_vals_to_tensor(  # type: ignore
                        n_subnets, list(uids), list(values), subnets
                    )
                )

        tensor_param: NDArray = (
            np.stack(data_array) if len(data_array) else np.array([], dtype=np.float32)
        )
        if len(data_array) == 0:
            pass
            # bittensor.logging.warning(
            #     f"Empty {attribute}_array on metagraph.sync(). The '{attribute}' tensor is empty."
            # )
        return tensor_param
