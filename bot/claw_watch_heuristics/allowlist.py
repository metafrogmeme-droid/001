"""Spenders and recipients that keep the permit-then-pull rule quiet.

Routers are the ones the 25 Oct 2026 research listed from explorer titles.
The noisy permit-then-pull cases are added on top of that list:

* MetaMask Delegation Manager, from the delegation-framework deployments
* MetaMask Gas Station and Gas Station Swap, from the public name tags
* ParaSwap Delta V2, from the Velora contract list
* KuCoin and Bitvavo hot wallets, from the public exchange tags

A hot wallet quiets a pull whose recipients are all on that list. It is
not treated as a spender a person may approve without a signal.
"""

from __future__ import annotations

PERMIT2 = "0x000000000022d473030f116ddee9f6b43ac78ba3"

# ERC-4337 EntryPoints 0.6, 0.7 and 0.8. Used only for the self-execution
# skip, not as spenders.
ENTRYPOINTS = frozenset({
    "0x5ff137d4b0fdcd49dca30c7cf57e578a026d2789",
    "0x0000000071727de22e5e9d8baf0edac6f37da032",
    "0x4337084d9e255ff0702461cf8895ce9e3b5ff108",
})

# CrimeEnjoyor-family delegates, from the public name tags in the research.
BAD_7702 = frozenset({
    "0x6f7c7b0129afd6172dd36891b7048c6cf2d29c7f",
    "0x89046d34e70a65acab2152c26a0c8e493b5ba629",
    "0xb6785b782571980b3ddb5d40659f4861ff15aa02",
    "0x6b7879a5d747e30a3adb37a9e41c046928fce933",
    "0x08f5e230d412f0cd475b03b9acb51d1562f730f9",
    "0xa03fc3c62d26253b3ec3076cb871afa3b5fa60ab",
    "0x8044f37eb661c524227f0936eca21a1a748dd79c",
    "0x289c9c58355e1a7d2b0ad4a5e8f2c3c961b48ae9",
})

# Delegates the research treated as known wallet code.
GOOD_7702 = frozenset({
    "0xd2e28229f6f2c235e57de2ebc727025a1d0530fb",
    "0x5a7fc11397e9a8ad41bf10bf13f22b0a63f96f6d",
    "0x80296ff8d1ed46f8e3c7992664d13b833504c2bb",
    "0x63c0c19a282a1b52b07dd5a65b58948a07dae32b",
})

_ROUTERS = frozenset({
    PERMIT2,
    "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad",
    "0x66a9893cc07d91d95644aedd05d03f95e1dba8af",
    "0xef1c6e67703c7bd7107eed8303fbe6ec2554bf6b",
    "0x00000011f84b9aa48e5f8aa8b9897600006289be",
    "0x6000da47483062a0d734ba3dc7576ce6a0b645c4",
    "0x111111125421ca6dc452d289314280a0f8842a65",
    "0x1111111254eeb25477b68fb85ed929f73a960582",
    "0x9008d19f58aabd9ed0d60971565aa8510560ab41",
    "0xc92e8bdf79f0507f65a392b0ab4667716bfe0110",
    "0x0000000000000068f116a894984e2db1123eb395",
    "0x00000000000000adc04c56bf30ac9d3c0aaf14dc",
    "0x1e0049783f008a0085193e00003d00cd54003c71",
    "0x6a000f20005980200259b80c5102003040001068",
    "0xcf5540fffcdc3d510b18bfca6d2b9987b0772559",
    "0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae",
    "0x6131b5fae19ea4f9d964eac0408e4408b66337b5",
    "0x881d40237659c251811cec9c364ef91dc08d300c",
    "0x888888888889758f76e7103c6cbf23abbf58f946",
    "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2",
    "0x29fcb43b46531bca003ddc8fcb67ffe91900c762",
    "0x000000000000ad05ccc4f10045630fb830b95127",
    "0x29469395eaf6f95920e59f858042f0e28d98a20b",
    "0x7f268357a8c2552623316e2562d90e642bb538e5",
    "0x00000000006c3852cbef3e08e8df289169ede581",
})

# Gasless swaps and the delegation manager. Same address on every chain
# the deployments publish.
_GASLESS = frozenset({
    "0xdb9b1e94b5b69df7e401ddbede43491141047db3",  # Delegation Manager
    "0xc066ac5d385419b1a8c43a0e146fa439837a8b8c",  # Gas Station
    "0xe3478b0bb1a5084567c319096437924948be1964",  # Gas Station Swap
    "0x0000000000bbf5c5fd284e657f01bd000933c96d",  # ParaSwap Delta V2
})

# Public exchange tags. A pull into one of these is the deposit sweep
# the permit-then-pull rule was noisy on. Not a spender allow.
CEX_SWEEP = frozenset({
    "0x58edf78281334335effa23101bbe3371b6a36a51",  # KuCoin 20
    "0x53f78a071d04224b8e254e243fffc6d9f2f3fa23",  # KuCoin hot wallet 2
    "0xab782bc7d4a2b306825de5a7730034f8f63ee1bc",  # Bitvavo hot 3
    "0x079a892628ebf28d0ed8f00151cff225a093dc63",  # Bitvavo hot 1
    "0xd2674da94285660c9b2353131bef2d8211369a4b",  # Bitvavo 15
})

ALLOW_SPENDERS = _ROUTERS | _GASLESS
RECIPIENT_QUIET = ALLOW_SPENDERS | CEX_SWEEP

# Wrapped native, so an internal forward into WETH is not the claim pattern.
# These are the canonical mainnet and Base addresses. A testnet WETH that
# is not in this set is not assumed.
WRAPPED_NATIVE = frozenset({
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    "0x4200000000000000000000000000000000000006",
})
