'use strict';
/**
 * The EVM chains this build reads, with their public RPCs and explorer. Plain
 * data with no dependencies, so a reader that only needs the table (the
 * privacy policy's truth test, the Claw Guard simulator) does not load ethers.
 * lib/nft.js re-exports it; the reasons the chain travels with its RPCs are
 * written there, beside the code that picks one.
 */
const DEFAULT_CHAIN_ID = 8453; // Base
const CHAINS = {
  8453: {
    name: 'Base',
    rpcs: ['https://mainnet.base.org', 'https://base-rpc.publicnode.com'],
    explorer: 'https://basescan.org',
  },
  // Base Sepolia exists here for one reason: this contract has no owner, no
  // pause and no upgrade path, so the mainnet deploy is permanent and worth
  // rehearsing end-to-end first.
  84532: {
    name: 'Base Sepolia',
    rpcs: ['https://sepolia.base.org'],
    explorer: 'https://sepolia.basescan.org',
  },
};

module.exports = { DEFAULT_CHAIN_ID, CHAINS };
