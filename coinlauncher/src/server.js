// Local-only dashboard for launching a token. Binds to 127.0.0.1 — same
// security posture as the arb-scanner Flask dashboard: never exposed beyond
// this machine. Keypair files are read/written from disk on THIS machine by
// THIS process; private keys never cross a network boundary.

import express from "express";
import multer from "multer";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import "dotenv/config";
import {
  launchToken,
  ESTIMATED_LAUNCH_COST_SOL,
  raydiumCreatePoolUrl,
  RPC_ENDPOINTS,
} from "./createToken.js";
import {
  DEFAULT_KEYPAIR_PATH,
  loadOrCreateKeypair,
  ensureFundedDevnetWallet,
  getBalanceSol,
  newWalletPath,
} from "./wallet.js";
import { buildAndUploadMetadata } from "./metadata.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");
const WALLETS_DIR = path.join(ROOT, "wallets");
const UPLOADS_DIR = path.join(ROOT, "uploads");
fs.mkdirSync(WALLETS_DIR, { recursive: true });
fs.mkdirSync(UPLOADS_DIR, { recursive: true });

const app = express();
const PORT = Number(process.env.PORT || 7777);
const upload = multer({ dest: UPLOADS_DIR, limits: { fileSize: 10 * 1024 * 1024 } });

app.use(express.json());
app.use(express.static(path.join(ROOT, "public")));

const MAINNET_CONFIRM_PHRASE = "LAUNCH MAINNET";
const MIN_MAINNET_SOL = ESTIMATED_LAUNCH_COST_SOL;

app.get("/api/config", (_req, res) => {
  res.json({
    estimatedMainnetCostSol: ESTIMATED_LAUNCH_COST_SOL,
    mainnetConfirmPhrase: MAINNET_CONFIRM_PHRASE,
    defaultKeypairPath: DEFAULT_KEYPAIR_PATH,
  });
});

app.post("/api/launch", upload.single("thumbnail"), async (req, res) => {
  const {
    network,
    walletMode, // "new" (default) or "existing"
    keypairPath,
    name,
    symbol,
    uri, // manual metadata URI override, optional
    description,
    twitterUrl,
    decimals,
    supply,
    revokeAuthorities,
    mainnetConfirm,
  } = req.body || {};

  const cleanupUpload = () => {
    if (req.file) fs.unlink(req.file.path, () => {});
  };

  if (network === "mainnet-beta" && mainnetConfirm !== MAINNET_CONFIRM_PHRASE) {
    cleanupUpload();
    return res.status(400).json({
      error: `Mainnet launch requires the confirmation field to exactly equal "${MAINNET_CONFIRM_PHRASE}".`,
    });
  }
  if (!name || !symbol || !supply) {
    cleanupUpload();
    return res.status(400).json({ error: "name, symbol, and supply are required." });
  }

  const resolvedKeypairPath =
    walletMode === "existing" ? keypairPath || DEFAULT_KEYPAIR_PATH : newWalletPath(symbol, WALLETS_DIR);

  try {
    let walletInfo;

    if (network === "mainnet-beta") {
      // Never auto-fund mainnet — that would mean creating real money,
      // which isn't possible. We only create the wallet file itself if
      // it's missing, and check the balance before a real transaction goes out.
      const { keypair, path: resolved, created } = loadOrCreateKeypair(resolvedKeypairPath);
      const balance = await getBalanceSol(keypair.publicKey, "mainnet-beta");
      if (balance < MIN_MAINNET_SOL) {
        cleanupUpload();
        return res.status(400).json({
          error:
            `Wallet ${keypair.publicKey.toBase58()} (${resolved}) has ${balance} SOL, ` +
            `needs at least ~${MIN_MAINNET_SOL} SOL to cover this launch. ` +
            (created
              ? "This wallet was just created and is empty — send it real SOL from an exchange first, then launch again."
              : "Send more SOL to it first."),
        });
      }
      walletInfo = { path: resolved, created, address: keypair.publicKey.toBase58(), balance, keypair };
    } else {
      // Devnet: create the wallet if missing and top it up with free test
      // SOL automatically if the balance is low. No real money involved.
      const { path: resolved, created, balance, airdropped, keypair } =
        await ensureFundedDevnetWallet(resolvedKeypairPath);
      walletInfo = { path: resolved, created, airdropped, address: keypair.publicKey.toBase58(), balance, keypair };
    }

    // Metadata: use a manually supplied URI as-is if given; otherwise, if
    // there's a thumbnail and/or description/twitter link to work with,
    // build and host the metadata JSON automatically. Otherwise leave blank.
    let metadataUri = uri || "";
    let metadataNote = null;
    if (!metadataUri && (req.file || description || twitterUrl)) {
      try {
        metadataUri = await buildAndUploadMetadata({
          keypair: walletInfo.keypair,
          network,
          rpcUrl: RPC_ENDPOINTS[network],
          name,
          symbol,
          description,
          twitterUrl,
          imagePath: req.file ? req.file.path : null,
        });
      } catch (metaErr) {
        // Don't fail the whole launch over a metadata hosting problem —
        // report it and continue with no image/description rather than
        // lose the mint over a storage hiccup.
        metadataNote = `Metadata/thumbnail upload failed, launching without it: ${metaErr.message || metaErr}`;
      }
    }

    const result = await launchToken({
      network,
      keypairPath: resolvedKeypairPath,
      name,
      symbol,
      uri: metadataUri,
      decimals: Number(decimals ?? 6),
      supply,
      revokeAuthorities: revokeAuthorities !== false,
    });

    cleanupUpload();

    res.json({
      ok: true,
      ...result,
      metadataUri,
      metadataNote,
      wallet: { path: walletInfo.path, created: walletInfo.created, airdropped: walletInfo.airdropped },
      raydiumCreatePoolUrl: raydiumCreatePoolUrl(result.mintAddress),
    });
  } catch (err) {
    cleanupUpload();
    console.error(err);
    res.status(500).json({ error: err.message || String(err) });
  }
});

app.listen(PORT, "127.0.0.1", () => {
  console.log(`Coin launcher dashboard: http://127.0.0.1:${PORT}`);
  console.log("Bound to 127.0.0.1 only — not reachable from other machines.");
});
