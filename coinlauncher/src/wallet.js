// Shared wallet helpers: generate/load a standard-format keypair file, check
// balance, request a devnet airdrop. Used by both the CLI (wallet-setup.js)
// and the dashboard server, so "auto-create my wallet" behaves identically
// either way. Devnet airdrops only — mainnet needs real SOL, nothing here
// can create real money.

import fs from "fs";
import path from "path";
import os from "os";
import { Keypair, Connection, LAMPORTS_PER_SOL } from "@solana/web3.js";

export const DEVNET_RPC = "https://api.devnet.solana.com";
export const DEFAULT_KEYPAIR_PATH = "~/.config/solana/id.json";

export function expandHome(p) {
  return p.startsWith("~") ? path.join(os.homedir(), p.slice(1)) : p;
}

/** Path for a fresh, dedicated wallet for one coin — never collides, never reused. */
export function newWalletPath(symbol, walletsDir) {
  const slug = (symbol || "coin").toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 12) || "COIN";
  return path.join(walletsDir, `${slug}-${Date.now()}.json`);
}

/** Load an existing keypair, or generate + save a new one if the file doesn't exist yet. */
export function loadOrCreateKeypair(keypairPath) {
  const resolved = expandHome(keypairPath || DEFAULT_KEYPAIR_PATH);
  if (fs.existsSync(resolved)) {
    const kp = Keypair.fromSecretKey(Uint8Array.from(JSON.parse(fs.readFileSync(resolved, "utf8"))));
    return { keypair: kp, path: resolved, created: false };
  }
  const kp = Keypair.generate();
  fs.mkdirSync(path.dirname(resolved), { recursive: true });
  fs.writeFileSync(resolved, JSON.stringify(Array.from(kp.secretKey)), { mode: 0o600 });
  return { keypair: kp, path: resolved, created: true };
}

export async function getBalanceSol(publicKey, network = "devnet") {
  const rpc = network === "mainnet-beta" ? "https://api.mainnet-beta.solana.com" : DEVNET_RPC;
  const connection = new Connection(rpc, "confirmed");
  const lamports = await connection.getBalance(publicKey);
  return lamports / LAMPORTS_PER_SOL;
}

export async function requestDevnetAirdrop(publicKey, amountSol = 2) {
  const connection = new Connection(DEVNET_RPC, "confirmed");
  const sig = await connection.requestAirdrop(publicKey, amountSol * LAMPORTS_PER_SOL);
  await connection.confirmTransaction(sig, "confirmed");
  return getBalanceSol(publicKey, "devnet");
}

/**
 * Devnet convenience: load/create the wallet, and top it up automatically if
 * its balance is below `minSol`. Never touches mainnet balances — real SOL
 * has to come from you, on purpose.
 *
 * The public devnet faucet is shared across everyone using it and gets
 * rate-limited (HTTP 429) fairly often — that's not a bug in this tool, it's
 * upstream. If the automatic airdrop fails, we surface a clear next step
 * instead of a raw RPC error.
 */
export async function ensureFundedDevnetWallet(keypairPath, minSol = 0.05) {
  const { keypair, path: resolved, created } = loadOrCreateKeypair(keypairPath);
  let balance = await getBalanceSol(keypair.publicKey, "devnet");
  let airdropped = false;
  if (balance < minSol) {
    try {
      balance = await requestDevnetAirdrop(keypair.publicKey, 2);
      airdropped = true;
    } catch (err) {
      const address = keypair.publicKey.toBase58();
      throw new Error(
        `Automatic devnet airdrop failed (the public faucet is rate-limited — ` +
        `this is a Solana-wide limit, not specific to this tool). ` +
        `Get free test SOL manually: open https://faucet.solana.com, paste in ` +
        `wallet address ${address}, request SOL there, then click Launch again ` +
        `— it'll skip the airdrop once the balance is enough. ` +
        `(Underlying error: ${err.message || err})`
      );
    }
  }
  return { keypair, path: resolved, created, balance, airdropped };
}
