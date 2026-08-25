#!/usr/bin/env node
// Command-line alternative to the dashboard. Same underlying logic, same
// single-owner/no-hidden-wallets guarantees.
//
// Usage:
//   node src/cli.js --network devnet --keypair ~/.config/solana/id.json \
//     --name "My Token" --symbol MYTKN --supply 1000000000 --decimals 6
//
// Add --network mainnet-beta --confirm "LAUNCH MAINNET" to go live for real.

import "dotenv/config";
import { launchToken, raydiumCreatePoolUrl } from "./createToken.js";

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
  if (!args.keypair || !args.name || !args.symbol || !args.supply) {
    console.error("Required: --keypair <path> --name <name> --symbol <symbol> --supply <amount>");
    process.exit(1);
  }

  console.log(`Launching "${args.name}" (${args.symbol}) on ${network}...`);

  const result = await launchToken({
    network,
    keypairPath: args.keypair,
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
