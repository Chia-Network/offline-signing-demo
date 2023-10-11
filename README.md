## Introduction

This demo has 2 components: `create_unsigned_tx.py` and `sign_tx.py`.  

`create_unsigned_tx.py`is used for creating transactions and requires having a master public key (to create addresses), 
and a full node that is connected and synced to the blockchain.  

`sign_tx.py` is used for signing transactions, and requires a spend bundle JSON, and a master private key mnemonic (24 words), 
but it requires no servers, ports, or internet connection.  

## How to use the demo

These steps are for unhardened keys. For hardened keys it's slightly different.  

1. Get two machines, an online and offline machine, and run a node on the online one (testnet or mainnet).  

Both machines:  
2. Install python 3 and create a venv `python3 -m venv  ./venv` in the directory where this README is.  
3. Run `source ./venv/bin/activate` to activate the venv.  
4. Run `pip install -r requirements.txt` for this project.  

Offline machine:  
5. Create a private key using `chia keys generate` or `chia keys generate_and_print`.  
Note: generate_and_print will not add the keys to the devices keyring, they will only be displayed on-screen.  
6. Put the 24 words into `sign_tx.py` file.  
7. Run the sign_tx script to get the parent public key, copy the parent public key from the offline machine paste it into `create_unsigned_tx.py` on the online machine replacing the value in line 202 `parent_pk_hex = "b9a124531d5528a2760afc6444c4c877cefdb1b6eeaee32f6929ee08..."`.  
Note: I used a new and fully formatted USB drive to copy this public key.  

Online machine:  
8. Use the `generate_address` function in `create_unsigned_tx.py` to create new addresses for a specific index, and send some chia (or testnet chia) to these addresses. Note that for testnet, you should use the `txch` prefix instead of `xch` when calling this function. The default calls to this function are lines 204-207, example of amending one of these for testnet `print(await generate_address_unhardened(parent_pk, 1400, "txch"))`.  
Note: the second parameter (1400) is the index used for generating the key,  
more information on bls keys can be found [here](https://docs.chia.net/bls-keys/).  
9. Run the `create_unsigned_tx.py` script and save the JSON spend bundle. Note: I used a new and fully formatted USB drive to copy this spend bundle.  

Offline machine:  
10. Run the `sign_tx.py` script with your 24 words, and your transactions spend bundle.  
11. Copy the signed tx. Note: I used a new and fully formatted USB drive to copy this signed transaction.  

Online machine:  
12. Send the transaction to the full node using the `push_tx` [RPC call](https://docs.chia.net/full-node-rpc#push_tx).  

## Hardened vs Unhardened Keys
This demo uses bip32 public key derivation, with the option to use hardened or unhardened keys.  
With unhardened keys, new public keys / addresses can be created using a master
public key, which can be on an online machine. Even if the master pk is revealed, the secret key (sk or private key),
is not revealed. Note however, that if one of the child private keys is revealed, all of the other child private keys
are also revealed. Please see https://github.com/Chia-Network/chia-blockchain/wiki/Chia-Keys-Architecture for information
about keys.

Child public keys derived in this form (called unhardened keys) are NOT part of the EIP-2333 spec, since they are not secure against a quantum
attack. If you want more security against quantum attacks, we recommend not using this feature, and instead generating
many public keys from your offline machine, and exporting all of them into an online machine. Furthermore, hardened
keys do not have the security issue where revealing a child key and the public parent reveals all of the other childs.

If you would like to use hardened private keys, which don't allow deriving new public keys from a master public key,
you can also do this. In the `sign_tx.py` file, there is method which can create many public keys and export them to
a file. You can import this file using the `create_unsigned_tx.py` script. Please read both files to understand how
they work.


## Transactions
Chia has no concept of transactions in the blockchain. There are spend bundles which can represent an individual
transaction, but when a block is created, all spend bundles are combined into one, and all the signatures are 
aggregated into one. Each block in chia only has one signature. Therefore, each block does not have a list
of transactions, but rather a list of added coins and a list of removed coins. You cannot query for transactions on
the blockchain, you can only get coins. Use the `get_coin_records_for_puzzle_hash` to see all spent or unspent
coins for a specific puzzle hash. You can find more information about the RPCs here: https://github.com/Chia-Network/chia-blockchain/wiki/RPCExamples.

## Changelog

### 1.3
- Added announcements to ensure the transaction cannot be unbundled
- Added the RESERVE_FEE condition to ensure no stealing of fees
- Show the conditions created by the transaction

### 1.2
- Change format of unhardened keys, note, the master PK is no longer used. (hardened is used for the top 3 levels)
- Add setup.py so you can do `pip install -r requirements.txt`, and update instructions.
- Fix a bug with spending multiple coins (the outputs were being duplicated for each input)
- Add checking of cost, so that tx fits in the blockchain

### 1.1
- Adds support for hardened keys.

### 1.0
- Initial commit with unhardened keys support.
