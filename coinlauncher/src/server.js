// Local-only dashboard for launching a token. Binds to 127.0.0.1 — same
// security posture as the arb-scanner Flask dashboard: never exposed beyond
// this machine. Your keypair file path is read/written from disk on THIS
// machine by THIS process; the private key never crosses a network boundary.

import express from "express";
import path from "path";
import { fileURLToPath } from "url";
import "dotenv/config";
import {
  launchToken,
  ESTIMATED_LAUNCH_COST_SOL,
  raydiumCreatePoolUrl,
} from "./createToken.js";
import {
  DEFAULT_KEYPAIR_PATH,
  loadOrCreateKeypair,
  ensureFundedDevnetWallet,
  getBalanceSol,
} from "./wallet.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = Number(process.env.PORT || 7777);

app.use(express.json());
app.use(express.static(path.join(__dirname, "..", "public")));

const MAINNET_CONFIRM_PHRASE = "LAUNCH MAINNET";
const MIN_MAINNET_SOL = ESTIMATED_LAUNCH_COST_SOL;

app.get("/api/config", (_req, res) => {
  res.json({
    estimatedMainnetCostSol: ESTIMATED_LAUNCH_COST_SOL,
    mainnetConfirmPhrase: MAINNET_CONFIRM_PHRASE,
    defaultKeypairPath: DEFAULT_KEYPAIR_PATH,
  });
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

  const resolvedKeypairPath = keypairPath || DEFAULT_KEYPAIR_PATH;

  try {
    let walletInfo;

    if (network === "mainnet-beta") {
      // Never auto-fund mainnet — that would mean creating real money,
      // which isn't possible and wouldn't be right to fake. We only
      // create the wallet file itself if it's missing, and check the
      // balance before letting a real transaction go out.
      const { keypair, path: resolved, created } = loadOrCreateKeypair(resolvedKeypairPath);
      const balance = await getBalanceSol(keypair.publicKey, "mainnet-beta");
      if (balance < MIN_MAINNET_SOL) {
        return res.status(400).json({
          error:
            `Wallet ${keypair.publicKey.toBase58()} has ${balance} SOL, ` +
            `needs at least ~${MIN_MAINNET_SOL} SOL to cover this launch. ` +
            (created
              ? "This wallet was just created and is empty — send it real SOL from an exchange first."
              : "Send more SOL to it first."),
        });
      }
      walletInfo = { path: resolved, created, address: keypair.publicKey.toBase58(), balance };
    } else {
      // Devnet: create the wallet if missing and top it up with free test
      // SOL automatically if the balance is low. No real money involved.
      const { path: resolved, created, address, balance, airdropped, keypair } =
        await ensureFundedDevnetWallet(resolvedKeypairPath);
      walletInfo = {
        path: resolved,
        created,
        airdropped,
        address: (address || keypair.publicKey.toBase58()),
        balance,
      };
    }

    const result = await launchToken({
      network,
      keypairPath: resolvedKeypairPath,
      name,
      symbol,
      uri,
      decimals: Number(decimals ?? 6),
      supply,
      revokeAuthorities: revokeAuthorities !== false,
    });

    res.json({
      ok: true,
      ...result,
      wallet: walletInfo,
      raydiumCreatePoolUrl: raydiumCreatePoolUrl(result.mintAddress),
    });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: err.message || String(err) });
  }
});

app.listen(PORT, "127.0.0.1", () => {
  console.log(`Coin launcher dashboard: http://127.0.0.1:${PORT}`);
  console.log("Bound to 127.0.0.1 only — not reachable from other machines.");
});
