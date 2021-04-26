import asyncio
import json
from typing import Dict

from blspy import G1Element, AugSchemeMPL, PrivateKey, G2Element

from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.spend_bundle import SpendBundle
from chia.util.condition_tools import conditions_dict_for_solution, pkm_pairs_for_conditions_dict
from chia.util.keychain import mnemonic_to_seed
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import (
    puzzle_for_pk,
    calculate_synthetic_secret_key,
    DEFAULT_HIDDEN_PUZZLE_HASH,
)


async def sign_tx(mnemonic: str, spend_bundle: SpendBundle):
    """
    Takes in an unsigned transaction (called a spend bundle in chia), and a 24 word mnemonic (master sk)
    and generates the aggregate BLS signature for the transaction.
    """
    seed: bytes = mnemonic_to_seed(mnemonic, passphrase="")
    master_private_key: PrivateKey = AugSchemeMPL.key_gen(seed)
    print(f"Master public key: {master_private_key.get_g1()}")
    # This field is the ADDITIONAL_DATA found in the constants
    additional_data: bytes = bytes.fromhex("ccd5bb71183532bff220ba46c268991a3ff07eb358e8255a65c30a2dce0e5fbb")
    intermediate_sk: PrivateKey = AugSchemeMPL.derive_child_sk_unhardened(master_private_key, 12381)
    intermediate_sk = AugSchemeMPL.derive_child_sk_unhardened(intermediate_sk, 8444)
    intermediate_sk = AugSchemeMPL.derive_child_sk_unhardened(intermediate_sk, 2)

    puzzle_hash_to_sk: Dict[bytes32, PrivateKey] = {}

    # Change this loop to scan more keys if you have more
    for i in range(5000):
        child_sk: PrivateKey = AugSchemeMPL.derive_child_sk_unhardened(intermediate_sk, i)
        child_pk: G1Element = child_sk.get_g1()
        puzzle = puzzle_for_pk(child_pk)
        puzzle_hash = puzzle.get_tree_hash()
        puzzle_hash_to_sk[puzzle_hash] = child_sk

    aggregate_signature: G2Element = G2Element()
    for coin_solution in spend_bundle.coin_solutions:
        if coin_solution.coin.puzzle_hash not in puzzle_hash_to_sk:
            print(f"Puzzle hash {coin_solution.coin.puzzle_hash} not found for this key.")
            return
        sk: PrivateKey = puzzle_hash_to_sk[coin_solution.coin.puzzle_hash]
        synthetic_secret_key: PrivateKey = calculate_synthetic_secret_key(sk, DEFAULT_HIDDEN_PUZZLE_HASH)

        err, conditions_dict, cost = conditions_dict_for_solution(
            coin_solution.puzzle_reveal, coin_solution.solution, 11000000000
        )

        if err or conditions_dict is None:
            print(f"Sign transaction failed, con:{conditions_dict}, error: {err}")
            return

        pk_msgs = pkm_pairs_for_conditions_dict(conditions_dict, bytes(coin_solution.coin.name()), additional_data)
        assert len(pk_msgs) == 1
        _, msg = pk_msgs[0]
        signature = AugSchemeMPL.sign(synthetic_secret_key, msg)

        aggregate_signature = AugSchemeMPL.aggregate([aggregate_signature, signature])

    new_spend_bundle = SpendBundle(spend_bundle.coin_solutions, aggregate_signature)
    print("")
    print("Signed spend bundle JSON:\n")
    print(json.dumps(new_spend_bundle.to_json_dict()))

    # This transaction can be submitted to the blockchain using the RPC: push_tx


async def main():
    # Mnemonics can be generated using `chia keys generate_and_print`, or `chia keys generate`. The latter stored
    # the key in the OS keychain (unencrypted file if linux).
    mnemonic: str = "neither medal holiday echo link dog sleep idea turkey logic security sword save taxi chapter artwork toddler wealth local mind manual never unlock narrow"

    with open("../tx_0.json", "r") as f:
        spend_bundle_json = f.read()
    spend_bundle_json_dict: Dict = json.loads(spend_bundle_json)
    spend_bundle: SpendBundle = SpendBundle.from_json_dict(spend_bundle_json_dict)
    await sign_tx(mnemonic, spend_bundle)


asyncio.run(main())
