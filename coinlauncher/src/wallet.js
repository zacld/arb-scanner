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

/** Path for a fresh, arbitrarily-labeled wallet (e.g. an operator wallet within a launch). */
export function labeledWalletPath(label, walletsDir) {
  const slug = (label || "wallet").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 24) || "wallet";
  return path.join(walletsDir, `${slug}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}.json`);
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
 * The public devnet faucet is shared across everyone using it and fails
 * often, for more reasons than just rate-limiting -- HTTP 429 ("too many
 * requests"), but also plain "Internal error" from the faucet itself
 * (drained, temporarily down, flaky). None of that is a bug in this tool.
 * A short retry covers the common transient case; if it's still failing
 * after that, surface a clear manual fallback instead of a raw RPC error.
 */
export async function ensureFundedDevnetWallet(keypairPath, minSol = 0.05) {
  const { keypair, path: resolved, created } = loadOrCreateKeypair(keypairPath);
  let balance = await getBalanceSol(keypair.publicKey, "devnet");
  let airdropped = false;
  if (balance < minSol) {
    const attempts = 3;
    let lastErr;
    for (let i = 0; i < attempts; i++) {
      try {
        balance = await requestDevnetAirdrop(keypair.publicKey, 2);
        airdropped = true;
        lastErr = null;
        break;
      } catch (err) {
        lastErr = err;
        if (i < attempts - 1) await new Promise((r) => setTimeout(r, 1500 * (i + 1)));
      }
    }
    if (lastErr) {
      const address = keypair.publicKey.toBase58();
      throw new Error(
        `Automatic devnet airdrop failed after ${attempts} attempts (the public devnet faucet is shared ` +
        `across everyone using it and fails often -- rate limits, or just its own "Internal error" -- ` +
        `this is a Solana-wide issue, not specific to this tool). ` +
        `Get free test SOL manually: open https://faucet.solana.com, paste in ` +
        `wallet address ${address}, request SOL there, then click Launch again ` +
        `— it'll skip the airdrop once the balance is enough. ` +
        `(Underlying error: ${lastErr.message || lastErr})`
      );
    }
  }
  return { keypair, path: resolved, created, balance, airdropped };
}
