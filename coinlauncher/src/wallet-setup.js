#!/usr/bin/env node
// CLI wrapper over ./wallet.js — same logic the dashboard uses internally.
//
// Usage:
//   node src/wallet-setup.js new ~/.config/solana/id.json
//   node src/wallet-setup.js airdrop ~/.config/solana/id.json
//   node src/wallet-setup.js balance ~/.config/solana/id.json

import {
  DEFAULT_KEYPAIR_PATH,
  loadOrCreateKeypair,
  requestDevnetAirdrop,
  getBalanceSol,
} from "./wallet.js";

async function cmdNew(keypairPath) {
  const { keypair, path: resolved, created } = loadOrCreateKeypair(keypairPath);
  console.log(created ? `Created: ${resolved}` : `Already exists: ${resolved}`);
  console.log(`Wallet address: ${keypair.publicKey.toBase58()}`);
  if (created) console.log("\nThis file is your private key. Never share it, never commit it.");
}

async function cmdAirdrop(keypairPath, amountSol = 2) {
  const { keypair } = loadOrCreateKeypair(keypairPath);
  console.log(`Requesting ${amountSol} devnet SOL for ${keypair.publicKey.toBase58()}...`);
  const balance = await requestDevnetAirdrop(keypair.publicKey, amountSol);
  console.log(`Done. Balance: ${balance} SOL (devnet)`);
}

async function cmdBalance(keypairPath, network = "devnet") {
  const { keypair } = loadOrCreateKeypair(keypairPath);
  const balance = await getBalanceSol(keypair.publicKey, network);
  console.log(`${keypair.publicKey.toBase58()}: ${balance} SOL (${network})`);
}

const [, , cmd, arg1, arg2] = process.argv;

try {
  if (cmd === "new") await cmdNew(arg1 || DEFAULT_KEYPAIR_PATH);
  else if (cmd === "airdrop") await cmdAirdrop(arg1 || DEFAULT_KEYPAIR_PATH, arg2 ? Number(arg2) : 2);
  else if (cmd === "balance") await cmdBalance(arg1 || DEFAULT_KEYPAIR_PATH, arg2 || "devnet");
  else {
    console.error("Usage: node src/wallet-setup.js <new|airdrop|balance> [keypairPath] [arg2]");
    process.exit(1);
  }
} catch (err) {
  console.error("Error:", err.message || err);
  process.exit(1);
}
