#!/usr/bin/env node
// Generates a standard-format Solana keypair file (same format the official
// `solana-keygen` CLI produces) without needing that CLI installed, and can
// request a devnet airdrop directly. Devnet only — mainnet needs real SOL
// from an exchange/on-ramp, this can't create real money.
//
// Usage:
//   node src/wallet-setup.js new ~/.config/solana/id.json
//   node src/wallet-setup.js airdrop ~/.config/solana/id.json
//   node src/wallet-setup.js balance ~/.config/solana/id.json

import fs from "fs";
import path from "path";
import os from "os";
import { Keypair, Connection, LAMPORTS_PER_SOL, PublicKey } from "@solana/web3.js";

const DEVNET_RPC = "https://api.devnet.solana.com";

function expandHome(p) {
  return p.startsWith("~") ? path.join(os.homedir(), p.slice(1)) : p;
}

async function cmdNew(keypairPath) {
  const resolved = expandHome(keypairPath);
  if (fs.existsSync(resolved)) {
    const existing = Keypair.fromSecretKey(Uint8Array.from(JSON.parse(fs.readFileSync(resolved, "utf8"))));
    console.log(`Already exists: ${resolved}`);
    console.log(`Wallet address: ${existing.publicKey.toBase58()}`);
    return;
  }
  const kp = Keypair.generate();
  fs.mkdirSync(path.dirname(resolved), { recursive: true });
  fs.writeFileSync(resolved, JSON.stringify(Array.from(kp.secretKey)), { mode: 0o600 });
  console.log(`Created: ${resolved}`);
  console.log(`Wallet address: ${kp.publicKey.toBase58()}`);
  console.log("\nThis file is your private key. Never share it, never commit it.");
}

async function cmdAirdrop(keypairPath, amountSol = 2) {
  const resolved = expandHome(keypairPath);
  const kp = Keypair.fromSecretKey(Uint8Array.from(JSON.parse(fs.readFileSync(resolved, "utf8"))));
  const connection = new Connection(DEVNET_RPC, "confirmed");
  console.log(`Requesting ${amountSol} devnet SOL for ${kp.publicKey.toBase58()}...`);
  const sig = await connection.requestAirdrop(kp.publicKey, amountSol * LAMPORTS_PER_SOL);
  await connection.confirmTransaction(sig, "confirmed");
  const balance = await connection.getBalance(kp.publicKey);
  console.log(`Done. Balance: ${balance / LAMPORTS_PER_SOL} SOL (devnet)`);
}

async function cmdBalance(keypairPath, network = "devnet") {
  const resolved = expandHome(keypairPath);
  const kp = Keypair.fromSecretKey(Uint8Array.from(JSON.parse(fs.readFileSync(resolved, "utf8"))));
  const rpc = network === "mainnet-beta" ? "https://api.mainnet-beta.solana.com" : DEVNET_RPC;
  const connection = new Connection(rpc, "confirmed");
  const balance = await connection.getBalance(kp.publicKey);
  console.log(`${kp.publicKey.toBase58()}: ${balance / LAMPORTS_PER_SOL} SOL (${network})`);
}

const [, , cmd, arg1, arg2] = process.argv;

try {
  if (cmd === "new") await cmdNew(arg1 || "~/.config/solana/id.json");
  else if (cmd === "airdrop") await cmdAirdrop(arg1 || "~/.config/solana/id.json", arg2 ? Number(arg2) : 2);
  else if (cmd === "balance") await cmdBalance(arg1 || "~/.config/solana/id.json", arg2 || "devnet");
  else {
    console.error("Usage: node src/wallet-setup.js <new|airdrop|balance> [keypairPath] [arg2]");
    process.exit(1);
  }
} catch (err) {
  console.error("Error:", err.message || err);
  process.exit(1);
}
