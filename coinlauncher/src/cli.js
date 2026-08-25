#!/usr/bin/env node
// Command-line alternative to the dashboard. Same underlying logic, same
// single-owner/no-hidden-wallets guarantees.
//
// Usage:
//   node src/cli.js --name "My Token" --symbol MYTKN --supply 1000000000
//
// Wallet defaults to ~/.config/solana/id.json and is created (and, on
// devnet, funded) automatically. Pass --keypair <path> to use a different one.
// Add --network mainnet-beta --confirm "LAUNCH MAINNET" to go live for real.

import "dotenv/config";
import { launchToken, raydiumCreatePoolUrl, ESTIMATED_LAUNCH_COST_SOL } from "./createToken.js";
import { DEFAULT_KEYPAIR_PATH, loadOrCreateKeypair, ensureFundedDevnetWallet, getBalanceSol } from "./wallet.js";

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith("--")) {
        out[key] = true;
      } else {
        out[key] = next;
        i++;
      }
    }
  }
  return out;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const network = args.network || "devnet";

  if (network === "mainnet-beta" && args.confirm !== "LAUNCH MAINNET") {
    console.error('Mainnet launch requires --confirm "LAUNCH MAINNET"');
    process.exit(1);
  }
  if (!args.name || !args.symbol || !args.supply) {
    console.error("Required: --name <name> --symbol <symbol> --supply <amount>");
    console.error("(--keypair is optional; defaults to " + DEFAULT_KEYPAIR_PATH + " and is created automatically if missing)");
    process.exit(1);
  }

  const keypairPath = args.keypair || DEFAULT_KEYPAIR_PATH;

  if (network === "mainnet-beta") {
    const { keypair, path: resolved, created } = loadOrCreateKeypair(keypairPath);
    const balance = await getBalanceSol(keypair.publicKey, "mainnet-beta");
    if (balance < ESTIMATED_LAUNCH_COST_SOL) {
      console.error(
        `Wallet ${keypair.publicKey.toBase58()} (${resolved}) has ${balance} SOL, ` +
        `needs at least ~${ESTIMATED_LAUNCH_COST_SOL} SOL.` +
        (created ? " This wallet was just created and is empty — fund it with real SOL first." : " Fund it first.")
      );
      process.exit(1);
    }
  } else {
    const { path: resolved, created, airdropped, balance } = await ensureFundedDevnetWallet(keypairPath);
    console.log(`Wallet: ${resolved}${created ? " (created)" : ""}${airdropped ? " — topped up with devnet SOL" : ""}, balance ${balance} SOL`);
  }

  console.log(`Launching "${args.name}" (${args.symbol}) on ${network}...`);

  const result = await launchToken({
    network,
    keypairPath,
    name: args.name,
    symbol: args.symbol,
    uri: args.uri || "",
    decimals: Number(args.decimals ?? 6),
    supply: args.supply,
    revokeAuthorities: args["keep-authorities"] ? false : true,
  });

  console.log("\n✅ Done.");
  console.log("Mint address:", result.mintAddress);
  console.log("Owner wallet:", result.ownerAddress);
  console.log("Authorities revoked:", result.authoritiesRevoked);
  console.log("Explorer:", result.explorerUrl);
  console.log("\nNext step (you drive this yourself, your own wallet):");
  console.log("Create liquidity pool:", raydiumCreatePoolUrl(result.mintAddress));
}

main().catch((err) => {
  console.error("\n❌ Launch failed:", err.message || err);
  process.exit(1);
});
