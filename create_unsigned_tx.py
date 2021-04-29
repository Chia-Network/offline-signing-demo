import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from blspy import G1Element, AugSchemeMPL, G2Element
from chia.consensus.cost_calculator import calculate_cost_of_program
from chia.full_node.bundle_tools import simple_solution_generator
from chia.full_node.mempool_check_conditions import get_name_puzzle_conditions

from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.types.announcement import Announcement
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import Program, SerializedProgram
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_record import CoinRecord
from chia.types.coin_solution import CoinSolution
from chia.types.condition_opcodes import ConditionOpcode
from chia.types.spend_bundle import SpendBundle
from chia.util.bech32m import encode_puzzle_hash, decode_puzzle_hash
from chia.util.condition_tools import parse_sexp_to_conditions
from chia.util.config import load_config
from chia.util.ints import uint16, uint64
from chia.util.hash import std_hash
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import puzzle_for_pk
from chia.wallet.wallet import Wallet
from chia.consensus.default_constants import DEFAULT_CONSTANTS
from clvm.casts import int_from_bytes


async def generate_address_unhardened(parent_pk: G1Element, derivation_index: int, prefix="xch") -> str:
    """
    This derives a child address from a parent public key, given a derivation index between 0 and 2**32 - 1
    Use 'txch' prefix for testnet.
    """

    child_pk: G1Element = AugSchemeMPL.derive_child_pk_unhardened(parent_pk, derivation_index)
    puzzle = puzzle_for_pk(child_pk)
    puzzle_hash = puzzle.get_tree_hash()
    return encode_puzzle_hash(puzzle_hash, prefix)


async def generate_address_from_child_pk(child_pk: G1Element, prefix="xch") -> str:
    puzzle = puzzle_for_pk(child_pk)
    puzzle_hash = puzzle.get_tree_hash()
    return encode_puzzle_hash(puzzle_hash, prefix)


async def check_cost(bundle: SpendBundle) -> None:
    """
    Checks that the cost of the transaction does not exceed blockchain limits. As of version 1.1.2, the mempool limits
    transactions to 50% of the block limit, or 0.5 * 11000000000 = 5.5 billion cost.
    """
    program = simple_solution_generator(bundle)
    npc_result = get_name_puzzle_conditions(program, DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM * 0.5, True)
    cost = calculate_cost_of_program(SerializedProgram.from_bytes(bytes(program)), npc_result,
                                     DEFAULT_CONSTANTS.COST_PER_BYTE)
    print(f"Transaction cost: {cost}")
    assert cost < (0.5 * DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM)


def print_conditions(spend_bundle: SpendBundle):
    print("\nConditions:")
    for coin_solution in spend_bundle.coin_solutions:
        result = Program.from_bytes(bytes(coin_solution.puzzle_reveal)).run(
            Program.from_bytes(bytes(coin_solution.solution)))
        error, result_human = parse_sexp_to_conditions(result)
        assert error is None
        for cvp in result_human:
            print(f"{ConditionOpcode(cvp.opcode).name}: {[var.hex() for var in cvp.vars]}")
    print("")


async def create_transaction(
        parent_pk: G1Element,
        outputs: List[Tuple[str, uint64]],
        fee: uint64,
        prefix="xch",
        public_keys: Optional[List[G1Element]] = None,
):
    """
    This searches for all coins controlled by the master public key, by deriving child pks in batches of 1000,
    and then searching the blockchain for coins. This requires the full node to be running and synced. Please keep
    the master public key SECRET, since if someone controls the master public key, and one of the child private keys,
    they can derive any other child private key.

    This method creates a spend bundle (transaction) with the given outputs and fees, in MOJO (chia trillionths).
    It is an unsigned transaction so it must be passed to an offline signer to sign, in JSON.
    """

    root_path = Path("/home/mariano/.chia/testnet_7")
    config = load_config(root_path, "config.yaml")
    client: FullNodeRpcClient = await FullNodeRpcClient.create("127.0.0.1", uint16(8555), root_path, config)
    try:
        state: Dict = await client.get_blockchain_state()

        if not state["sync"]["synced"]:
            print(f"Not synced. Please wait for the node to sync and try again.")
            return

        puzzle_hashes: List[bytes32] = []
        puzzle_hash_to_pk: Dict[bytes32, G1Element] = {}
        records: List[CoinRecord] = []

        start = time.time()
        if public_keys is not None and len(public_keys) > 0:
            # Using hardened keys to create transaction
            for pk in public_keys:
                puzzle = puzzle_for_pk(pk)
                puzzle_hash = puzzle.get_tree_hash()
                puzzle_hashes.append(puzzle_hash)
                puzzle_hash_to_pk[puzzle_hash] = pk
            records = await client.get_coin_records_by_puzzle_hashes(puzzle_hashes, False)

        else:
            # Using unhardened keys to create transaction
            for batch in range(100000000):
                new_puzzle_hashes: List[bytes32] = []
                for i in range(1000):
                    child_pk: G1Element = AugSchemeMPL.derive_child_pk_unhardened(parent_pk, batch * 1000 + i)
                    puzzle = puzzle_for_pk(child_pk)
                    puzzle_hash = puzzle.get_tree_hash()
                    new_puzzle_hashes.append(puzzle_hash)
                    puzzle_hashes.append(puzzle_hash)
                    puzzle_hash_to_pk[puzzle_hash] = child_pk
                new_records: List[CoinRecord] = await client.get_coin_records_by_puzzle_hashes(new_puzzle_hashes, False)
                if len(new_records) == 0:
                    break
                records += new_records

        print(f"Total number of records: {len(records)}")
        print(f"Time taken: {time.time() - start}")
        print("")

        total_amount: uint64 = uint64(sum([t[1] for t in outputs]) + fee)

        # Use older coins first
        records.sort(key=lambda r: r.timestamp)

        selected_coins: List[Coin] = []
        total_selected_amount = 0
        for record in records:
            total_selected_amount += record.coin.amount
            assert record.coin not in selected_coins
            selected_coins.append(record.coin)

            if total_selected_amount >= total_amount:
                break
        if total_selected_amount < total_amount:
            print(f"Not enough coins, total value {total_selected_amount}, need {total_amount}")
            return

        change = total_selected_amount - total_amount

        primaries = []
        for address, amount in outputs:
            primaries.append({"puzzlehash": decode_puzzle_hash(address), "amount": amount})
        if change > 0:
            # The change is going to the 0th key
            primaries.append({"puzzlehash": puzzle_hashes[0], "amount": change})

        primary_announcement_hash: Optional[bytes32] = None
        spends: List[CoinSolution] = []
        for coin in selected_coins:
            # get PK
            puzzle = puzzle_for_pk(puzzle_hash_to_pk[coin.puzzle_hash])
            if primary_announcement_hash is None:
                message_list: List[bytes32] = [c.name() for c in selected_coins]
                for primary in primaries:
                    message_list.append(Coin(coin.name(), primary["puzzlehash"], primary["amount"]).name())
                message: bytes32 = std_hash(b"".join(message_list))
                solution: Program = Wallet().make_solution(primaries=primaries, fee=fee, coin_announcements=[message])
                primary_announcement_hash = Announcement(coin.name(), message).name()
            else:
                solution = Wallet().make_solution(coin_announcements_to_assert=[primary_announcement_hash])
            spends.append(CoinSolution(coin, puzzle, solution))

        spend_bundle: SpendBundle = SpendBundle(spends, G2Element())

        await check_cost(spend_bundle)
        assert spend_bundle.fees() == fee
        print_conditions(spend_bundle)

        print(f"Created transaction with fees: {spend_bundle.fees()} and outputs:")
        for addition in spend_bundle.additions():
            print(f"   {encode_puzzle_hash(addition.puzzle_hash, prefix)} {addition.amount}")
        print("")
        print("Spend bundle JSON: \n")

        print(json.dumps(spend_bundle.to_json_dict()))
        return spend_bundle
    finally:
        client.close()


async def main():
    # The parent public key is NOT the same as the master pk that can be obtained by doing `chia keys show`.
    # It can be obtained from the 24 word menmonic, as shown in the sign_tx script. This is a SECRET value, do not
    # reveal this.

    parent_pk_hex = "b9a124531d5528a2760afc6444c4c877cefdb1b6eeaee32f6929ee086f08bfd4b15828125c21c47a3ef7b11fab84ba42"
    parent_pk: G1Element = G1Element.from_bytes(bytes.fromhex(parent_pk_hex))
    print(await generate_address_unhardened(parent_pk, 0))
    print(await generate_address_unhardened(parent_pk, 100))
    print(await generate_address_unhardened(parent_pk, 110))
    print(await generate_address_unhardened(parent_pk, 1400))

    public_keys: Optional[List[G1Element]] = None
    use_hardened_keys = False

    # Hardened keys provide more security against quantum computers, but don't allow you to derive new adresses
    # using the master (BIP32) public key. Therefore you need to load a public key file, generated with the private key
    if use_hardened_keys:
        with open("child_public_keys.txt", "r") as f:
            public_keys = [G1Element.from_bytes(bytes.fromhex(line)) for line in f.readlines()]
            assert len(public_keys) > 0
            print(await generate_address_from_child_pk(public_keys[0]))
            print(await generate_address_from_child_pk(public_keys[500]))

    # These are your payees, a list of tuples of address, and amount, in mojos (trillionths of a chia), and the fees.
    await create_transaction(
        parent_pk,
        [
            ("txch1jnrdkqqcdyqyrrhhc8c9uyn7uxny0jlxu52wcw790t9hh0ndlvgqed8545", uint64(15 * 10 ** 12)),
            ("txch1c2cguswhvmdyz9hr3q6hak2h6p9dw4rz82g4707k2xy2sarv705qcce4pn", uint64(16 * 10 ** 12)),
        ],
        uint64(0.5 * 10 ** 12),
        public_keys=public_keys,
    )


asyncio.run(main())
