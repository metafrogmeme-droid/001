# Seal anchors

What a day's seal root is written to, how to check it without this server, and
what it costs. The bytes live in `app/lib/root_anchor.js`,
`app/lib/dogecoin_anchor.js`, `app/lib/solana_anchor.js`, `app/lib/root_ots.js`
and `tools/rcverify.js`. This page describes them. It is not a second copy of
the rules.

## Signing

On mainnet the server never holds a key and never signs. No anchor in this
list adds a server-held mainnet key.

A wallet anchor is a plan (`GET /api/roots/anchor-plan/:day?chain=`). The
wallet's owner signs it and pays the fee. The server reads the transaction
back from two readers that must agree (`app/lib/reader_agreement.js`) and
records it only after that. A reader that does not answer, or two readers
that disagree, is `unknown`, never a pass and never a fail.

Bitcoin is not a transaction from this server. `app/lib/root_ots.js` POSTs a
32-byte digest to public OpenTimestamps calendars. The calendars aggregate
many digests and pay for the Bitcoin transaction.

`tools/rcverify.js` shares no code with the server. It names its own
endpoints and its own independence groups. A bundle's labels are not trusted.

## Independence groups

A day is multi-anchored when at least two of these groups verify the root
live (`app/lib/anchor_quorum.js`). A group is what a witness is independent
of. Two stamps that settle to the same chain are one witness.

| # | group | what counts |
|---|---|---|
| 1 | `ethereum-l2` | Base calldata, and an EAS stamp on Base, Arbitrum One, Scroll or Linea. All four settle to Ethereum. |
| 2 | `bitcoin` | An OpenTimestamps proof that leads to a Bitcoin block. Not in the chain registry. |
| 3 | `dogecoin` | A Dogecoin mainnet OP_RETURN. |
| 4 | `solana` | A Solana Memo instruction, read at `finalized`. |
| 5 | `avalanche` | Avalanche C-Chain calldata. Snowman is its own consensus, so this does not join `ethereum-l2`. |

Avalanche is the fifth independence group. The DogeOS testnet has a group
name of its own and does not count: a testnet can be reset.

## How often

Wallet anchors (Base, each EAS stamp, Dogecoin, Solana, Avalanche, the DogeOS
testnet): at most one recorded transaction per chain per UTC day. The first
verified submission wins. Nothing here sends them on a schedule. A day with
no wallet anchor on a chain is unanchored there, which is an absence, not a
failure.

Bitcoin: the job in `app/lib/root_ots.js` runs every ten minutes. For each of
the last seven completed days that has a root and no proof yet, it submits
one digest, then asks the calendars to finish proofs that are not in a
Bitcoin block yet. One digest per day. Not one Bitcoin transaction from this
server per day.

## Rough cost

Read 23 October 2026. The server pays none of these. A wallet cost is what
the sender pays if that chain is anchored once that day. Fiat is a CoinGecko
spot reading from that day (AVAX 9.17 USD, ETH 2411.19, SOL 104.93, DOGE
0.08033) times the gas below. It is a reading, not a quote.

The calldata payload `RCROOT1:<day>:<64 hex>` is 83 bytes. `eth_estimateGas`
of a zero-value self-send of that calldata answered 25669 gas on both
Avalanche readers and both Base readers.

| anchor | rough cost, once a day | who pays |
|---|---|---|
| Base calldata | 25669 gas at about 0.0155 gwei, about 0.0000004 ETH, under a tenth of a US cent | the wallet |
| EAS on Base | about 51000 gas, under a tenth of a US cent | the wallet |
| EAS on Arbitrum One | under a tenth of a US cent at the same day's reading | the wallet |
| EAS on Scroll | far under a tenth of a US cent | the wallet |
| EAS on Linea | about one US cent at 0.2 gwei | the wallet |
| Dogecoin | a few thousandths of a DOGE (relay floor 0.01 DOGE per kB, `app/lib/dogecoin_anchor.js`), under a tenth of a US cent | the wallet |
| Solana | the 5000-lamport signature fee (0.000005 SOL), plus any priority fee the wallet adds; about a twentieth of a US cent before priority | the wallet |
| Bitcoin | nothing from this server; the calendars pay the Bitcoin fee | the calendars |
| DogeOS testnet | testnet DOGE, from the faucet the registry names | the wallet |
| Avalanche C-Chain | 25669 gas at 2.5 gwei (both readers agreed, 2500000150 wei), about 0.000064 AVAX, under a tenth of a US cent | the wallet |

Expected daily cost of the Avalanche anchor, once the flag is on and a wallet
sends it every day: about 0.000064 AVAX, paid by that wallet. The server's
cost stays zero.

## Base (calldata)

- **Chain.** Base mainnet, chain id 8453. Group `ethereum-l2`.
- **What is anchored.** The day's root, as the calldata of a zero-value
  transaction the sender addresses to their own address. The bytes are
  `utf8("RCROOT1:<day>:<root>")`.
- **How often.** At most once per UTC day, when a wallet owner sends it.
- **Verify independently.** `eth_getTransactionByHash` on
  `https://mainnet.base.org` or `https://base-rpc.publicnode.com`. The
  calldata must equal that payload, and the transaction must be in a block.
  `node tools/rcverify.js <bundle.json>` asks `https://mainnet.base.org` and
  also requires the receipt status `0x1`. The server asks both readers and
  records the anchor only when they agree, including on the block hash.
  `WEB3_RPC_URL_BASE_ANCHOR` can replace the server's readers. It is never
  served to a page.
- **Cost.** About 0.0000004 ETH per day at the reading above.

## EAS stamps (Base, Arbitrum One, Scroll, Linea)

- **Chain.** The same group as Base: `ethereum-l2`. Keys `base-eas`,
  `arbitrum-eas`, `scroll-eas`, `linea-eas`.
- **What is anchored.** `timestamp(bytes32 root)` on that chain's Ethereum
  Attestation Service. The root is the 32 raw bytes, not the tagged text.
  Only the first stamp of a root lands; a second call reverts.
- **How often.** Once per root per chain, when a wallet owner sends it.
- **Verify independently.** `eth_call` `getTimestamp(bytes32)` on the
  contract the verifier names (not the one a bundle names). A non-zero
  answer is the stamp time. The addresses and the default readers are
  `EAS_L2` and `EAS_BASE` in `app/lib/root_anchor.js`. `rcverify` carries
  its own copy, and a test holds the two copies to the same address and
  group.
- **Cost.** Under a tenth of a US cent on Base, Arbitrum and Scroll at the
  23 October 2026 reading. About one US cent on Linea.

## Bitcoin

- **Chain.** Bitcoin, via OpenTimestamps. Group `bitcoin`.
- **What is anchored.** SHA-256 of the line `RCROOT1:<day>:<root>`. The proof
  file is a standard detached `.ots` for that line.
- **How often.** One digest per completed day, submitted by the ten-minute
  job until a proof exists. Older than seven days, a day is left as it is.
- **Verify independently.** Download
  `/api/roots/ots/<day>/statement` and `/api/roots/ots/<day>/proof` and run
  `ots verify` against a Bitcoin node you trust. The server's own check
  reads the block header from `https://blockstream.info/api` and
  `https://mempool.space/api` and accepts it when both agree and the block
  is six deep. `rcverify` checks that the statement and digest are this
  day's root; `ots verify` checks the Bitcoin path (`--save-ots` writes the
  file).
- **Cost.** Nothing from this server.

## Dogecoin

- **Chain.** Dogecoin mainnet. Group `dogecoin`. Not an EVM chain.
- **What is anchored.** One OP_RETURN output:
  `utf8("RCD1:<day>:")` followed by the root's 32 bytes (48 bytes). The
  tagged text used on the EVM chains is 83 bytes, and Dogecoin Core relays
  at most 80, so this tag is its own.
- **How often.** At most once per UTC day, when a wallet owner sends it from
  Dogecoin Core. The plan prints the console commands. The server does not
  run them.
- **Verify independently.** Read the transaction from BlockCypher
  (`https://api.blockcypher.com/v1/doge/main/txs/<txid>`) or SoChain
  (`https://sochain.com/api/v3/transaction/DOGE/<txid>`). One null-data
  output must be exactly those 48 bytes, and the transaction must be six
  blocks deep. `rcverify` asks BlockCypher. The server asks both and
  requires them to agree. Explorer:
  `https://blockchair.com/dogecoin/transaction/<txid>`.
- **Cost.** A few thousandths of a DOGE.

## Solana

- **Chain.** Solana mainnet. Group `solana`. Not an EVM chain. A signature
  is base58: case is the value, and the submit route does not lower-case it.
- **What is anchored.** One Memo-program instruction whose text is
  `RCROOT1:<day>:<root>`, the same text as the EVM calldata.
- **How often.** At most once per UTC day, when a wallet owner sends it.
- **Verify independently.** `getTransaction` at commitment `finalized` on
  `https://api.mainnet-beta.solana.com` or
  `https://solana-rpc.publicnode.com`. Exactly one Memo instruction, and its
  text equal to that line. A null answer is "not finalized yet", not "no
  such transaction". `rcverify` asks `api.mainnet-beta.solana.com`.
  `SOLANA_RPC_URLS` can replace the server's readers and is never served.
- **Cost.** 0.000005 SOL for the signature, plus any priority fee.

## DogeOS testnet

- **Chain.** DogeOS testnet, chain id 6281971. Shown, never counted
  (`counts_toward_quorum: false`).
- **What is anchored.** The same calldata self-send as Base.
- **How often.** At most once per UTC day, when a wallet owner sends it.
- **Verify independently.** The same calldata check as Base, and the block
  must be 17 deep (the reorg depth DogeOS documents). Readers:
  `https://rpc.testnet.dogeos.com/` and `https://dogeos-testnet.drpc.org`.
  `rcverify` asks the first and does not count the group.
- **Cost.** Testnet DOGE.

## Avalanche C-Chain

- **Chain.** Avalanche C-Chain, chain id 43114 (0xa86a). Group `avalanche`,
  the fifth independence group. Same calldata shape as Base, because it is
  an EVM chain and Base is the pattern: a zero-value self-send, no contract,
  no token.
- **What is anchored.** `utf8("RCROOT1:<day>:<root>")` as the transaction's
  calldata. Nothing else. No amount rides in the payload.
- **How often.** At most once per UTC day, when a wallet owner sends it, and
  only while the flag below is on. It is not on a schedule.
- **Flag.** `AVALANCHE_ANCHOR_ENABLED` must be `1`, `true`, `yes` or `on`.
  Unset, the chain is not in the registry: `GET /api/roots/chains` omits it,
  the plan route answers 400, and a submit is refused. The target for
  turning it on is 5 November 2026 (`AVALANCHE_ANCHOR_TARGET` in
  `app/lib/root_anchor.js`). The flag does not turn itself on that day.
  Leave it on once a day has been anchored here: a recorded anchor on a
  chain this build does not know is reported unknown, and it is not counted.
- **Readers.** `https://api.avax.network/ext/bc/C/rpc` and
  `https://avalanche-c-chain-rpc.publicnode.com`. Both answered chain id
  0xa86a on 23 October 2026. They are the same two `app/lib/erc8004.js`
  already uses for an Avalanche registration, held equal by a test.
  `AVALANCHE_RPC_URLS` (comma-separated) replaces them for the server's
  reads only. A page receives the two public URLs, never this list, so a
  keyed URL in the variable stays on the server and is named "configured
  rpc N" in a verdict. There is no QuickNode fallback: a chain without a
  confirmed name for one is not given a guessed name.
- **Verify independently.** The same check as Base.
  `eth_getTransactionByHash` on either reader: the calldata equals the
  payload, and the transaction is in a block. The server records it only
  when both readers agree, including on the block hash. No extra
  confirmation depth: Base's registry entry has none, and this one follows
  it. Avalanche's acceptance is that chain's finality; the code does not
  invent a depth Base does not use. `node tools/rcverify.js <bundle.json>`
  asks `https://api.avax.network/ext/bc/C/rpc`, requires receipt status
  `0x1`, and counts the group `avalanche` from its own table. It does not
  read `AVALANCHE_ANCHOR_ENABLED`. Explorer:
  `https://snowtrace.io/tx/<hash>`.
- **Signing.** The wallet's owner signs, the same way they sign a Base
  anchor. The plan's foundry line names `<YOUR_KEY_NEVER_SHARED_WITH_THE_SERVER>`.
  The server has no Avalanche key, no signer, and no send path.
- **Cost.** About 0.000064 AVAX per day at the reading above, paid by the
  wallet. Under a tenth of a US cent at 9.17 USD per AVAX.
- **Before the flag is set on the public site.** The privacy section "The
  daily proof, and the chains it is written to" was signed off on 22 October
  2026 and does not yet name Avalanche. Turning the flag on makes a new
  chain a wallet can write to, and that section has to name Avalanche
  C-Chain and these two readers before the owner records a new digest.
  Until then the flag stays unset and the public product matches the signed
  text.
