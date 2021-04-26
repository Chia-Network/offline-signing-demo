import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List, Tuple

from blspy import G1Element, AugSchemeMPL, G2Element

from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_record import CoinRecord
from chia.types.coin_solution import CoinSolution
from chia.types.spend_bundle import SpendBundle
from chia.util.bech32m import encode_puzzle_hash, decode_puzzle_hash
from chia.util.config import load_config
from chia.util.ints import uint16, uint64
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import puzzle_for_pk
from chia.wallet.wallet import Wallet


async def generate_address(master_pk: G1Element, derivation_index: int, prefix="xch") -> str:
    """
    This derives a child address from a master (root) public key, given a derivation index between 0 and 2**32 - 1
    Use 'txch' prefix for testnet.
    """

    intermediate_pk: G1Element = AugSchemeMPL.derive_child_pk_unhardened(master_pk, 12381)
    intermediate_pk = AugSchemeMPL.derive_child_pk_unhardened(intermediate_pk, 8444)
    intermediate_pk = AugSchemeMPL.derive_child_pk_unhardened(intermediate_pk, 2)
    child_pk: G1Element = AugSchemeMPL.derive_child_pk_unhardened(intermediate_pk, derivation_index)
    puzzle = puzzle_for_pk(child_pk)
    puzzle_hash = puzzle.get_tree_hash()
    return encode_puzzle_hash(puzzle_hash, prefix)


async def create_transaction(master_pk: G1Element, outputs: List[Tuple[str, uint64]], fee: uint64, prefix="xch"):
    """
    This searches for all coins controlled by the master public key, by deriving child pks in batches of 1000,
    and then searching the blockchain for coins. This requires the full node to be running and synced. Please keep
    the master public key SECRET, since if someone controls the master public key, and one of the child private keys,
    they can derive any other child private key.

    This method creates a spend bundle (transaction) with the given outputs and fees, in MOJO (chia trillionths).
    It is an unsigned transaction so it must be passed to an offline signer to sign, in JSON.
    """

    root_path = Path("/testnet5")
    config = load_config(root_path, "config.yaml")
    client: FullNodeRpcClient = await FullNodeRpcClient.create("127.0.0.1", uint16(8555), root_path, config)
    try:
        state: Dict = await client.get_blockchain_state()

        if not state["sync"]["synced"]:
            print(f"Not synced. Please wait for the node to sync and try again.")
            return

        intermediate_pk: G1Element = AugSchemeMPL.derive_child_pk_unhardened(master_pk, 12381)
        intermediate_pk = AugSchemeMPL.derive_child_pk_unhardened(intermediate_pk, 8444)
        intermediate_pk = AugSchemeMPL.derive_child_pk_unhardened(intermediate_pk, 2)

        start = time.time()
        puzzle_hashes: List[bytes32] = []
        puzzle_hash_to_pk: Dict[bytes32, G1Element] = {}
        records: List[CoinRecord] = []
        for batch in range(100000000):
            new_puzzle_hashes: List[bytes32] = []
            for i in range(1000):
                child_pk: G1Element = AugSchemeMPL.derive_child_pk_unhardened(intermediate_pk, batch * 1000 + i)
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

        first_spend: bool = True
        spends: List[CoinSolution] = []
        for coin in selected_coins:
            # get PK
            puzzle = puzzle_for_pk(puzzle_hash_to_pk[coin.puzzle_hash])
            if first_spend:
                solution: Program = Wallet.make_solution(primaries=primaries)
            else:
                solution = Wallet.make_solution()
            spends.append(CoinSolution(coin, puzzle, solution))

        spend_bundle: SpendBundle = SpendBundle(spends, G2Element())

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

    # The master public key can be obtained by doing `chia keys show`. Please keep this value SECRET!
    # It can also be obtained from the 24 word menmonic, as shown in the sign_tx script
    master_pk_hex = "8252b15998c16ce42b69ceb5cf3161cdcbc22574d50b68711e432a8c1f18bdfbaf1a60ed3cdb8bf46f7f5387b6cdf29d"
    master_pk: G1Element = G1Element.from_bytes(bytes.fromhex(master_pk_hex))
    print(await generate_address(master_pk, 0))
    print(await generate_address(master_pk, 100))
    print(await generate_address(master_pk, 110))
    print(await generate_address(master_pk, 1400))

    # These are your payees, a list of tuples of address, and amount, in mojos (trillionths of a chia), and the fees.
    await create_transaction(
        master_pk,
        [
            ("txch1jnrdkqqcdyqyrrhhc8c9uyn7uxny0jlxu52wcw790t9hh0ndlvgqed8545", uint64(300000)),
            ("txch1c2cguswhvmdyz9hr3q6hak2h6p9dw4rz82g4707k2xy2sarv705qcce4pn", uint64(400000)),
        ],
        uint64(10000000),
    )


asyncio.run(main())
