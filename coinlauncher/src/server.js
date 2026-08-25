// Local-only dashboard for launching a token. Binds to 127.0.0.1 — same
// security posture as the arb-scanner Flask dashboard: never exposed beyond
// this machine. Your keypair file path is read from disk on THIS machine by
// THIS process; the private key never crosses a network boundary.

import express from "express";
import path from "path";
import { fileURLToPath } from "url";
import "dotenv/config";
import {
  launchToken,
  ESTIMATED_LAUNCH_COST_SOL,
  raydiumCreatePoolUrl,
} from "./createToken.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = Number(process.env.PORT || 7777);

app.use(express.json());
app.use(express.static(path.join(__dirname, "..", "public")));

const MAINNET_CONFIRM_PHRASE = "LAUNCH MAINNET";

app.get("/api/config", (_req, res) => {
  res.json({ estimatedMainnetCostSol: ESTIMATED_LAUNCH_COST_SOL, mainnetConfirmPhrase: MAINNET_CONFIRM_PHRASE });
});

app.post("/api/launch", async (req, res) => {
  const {
    network,
    keypairPath,
    name,
    symbol,
    uri,
    decimals,
    supply,
    revokeAuthorities,
    mainnetConfirm,
  } = req.body || {};

  if (network === "mainnet-beta" && mainnetConfirm !== MAINNET_CONFIRM_PHRASE) {
    return res.status(400).json({
      error: `Mainnet launch requires the confirmation field to exactly equal "${MAINNET_CONFIRM_PHRASE}".`,
    });
  }
  if (!keypairPath) {
    return res.status(400).json({ error: "keypairPath is required (path to your local Solana keypair JSON file)." });
  }

  try {
    const result = await launchToken({
      network,
      keypairPath,
      name,
      symbol,
      uri,
      decimals: Number(decimals ?? 6),
      supply,
      revokeAuthorities: revokeAuthorities !== false,
    });
    res.json({ ok: true, ...result, raydiumCreatePoolUrl: raydiumCreatePoolUrl(result.mintAddress) });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: err.message || String(err) });
  }
});

app.listen(PORT, "127.0.0.1", () => {
  console.log(`Coin launcher dashboard: http://127.0.0.1:${PORT}`);
  console.log("Bound to 127.0.0.1 only — not reachable from other machines.");
});
