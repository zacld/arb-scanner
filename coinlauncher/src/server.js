// Local-only dashboard for launching a token. Binds to 127.0.0.1 — same
// security posture as the arb-scanner Flask dashboard: never exposed beyond
// this machine. Keypair files are read/written from disk on THIS machine by
// THIS process; private keys never cross a network boundary.

import express from "express";
import multer from "multer";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { PublicKey } from "@solana/web3.js";
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
  labeledWalletPath,
} from "./wallet.js";
import { buildAndUploadMetadata } from "./metadata.js";
import { distributeToOperatorWallets } from "./distribute.js";
import { newLaunchId, launchDir, saveLaunchManifest } from "./launchManifest.js";
import { consoleApiRouter } from "./consoleApi.js";

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
app.use("/api/console", consoleApiRouter(ROOT));

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
    operatorWallets: operatorWalletsRaw, // JSON string: [{ label, percent }, ...]
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

  let operatorWalletConfigs = [];
  if (operatorWalletsRaw) {
    try {
      operatorWalletConfigs = JSON.parse(operatorWalletsRaw).filter((w) => w && w.percent > 0);
    } catch {
      cleanupUpload();
      return res.status(400).json({ error: "operatorWallets was not valid JSON." });
    }
    const totalPercent = operatorWalletConfigs.reduce((sum, w) => sum + Number(w.percent), 0);
    if (totalPercent > 100) {
      cleanupUpload();
      return res.status(400).json({ error: `Operator wallet percentages add up to ${totalPercent}%, which is over 100%.` });
    }
  }

  const resolvedKeypairPath =
    walletMode === "existing" ? keypairPath || DEFAULT_KEYPAIR_PATH : newWalletPath(symbol, WALLETS_DIR);

  const launchId = newLaunchId(symbol);

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

    // Operator wallets: create each, then distribute the configured
    // percentage of supply to it. Every one of these is created by and
    // stays under the control of whoever ran this launch — same operator,
    // separated funds. Failure here doesn't undo the mint that already
    // happened; it's reported alongside whatever succeeded.
    let operatorResults = [];
    let distributionNote = null;
    if (operatorWalletConfigs.length > 0) {
      try {
        const opWalletsDir = path.join(launchDir(ROOT, launchId), "wallets");
        const preparedWallets = operatorWalletConfigs.map((w, i) => {
          const wPath = labeledWalletPath(w.label || `wallet-${i + 1}`, opWalletsDir);
          const { keypair } = loadOrCreateKeypair(wPath);
          return { id: `op${i + 1}`, label: w.label || `Wallet ${i + 1}`, percent: Number(w.percent), path: wPath, keypair };
        });

        operatorResults = await distributeToOperatorWallets({
          network,
          ownerKeypair: walletInfo.keypair,
          mintAddress: new PublicKey(result.mintAddress),
          decimals: Number(decimals ?? 6),
          totalSupply: supply,
          operatorWallets: preparedWallets,
        });
        const failed = operatorResults.filter((r) => r.ok === false);
        if (failed.length > 0) {
          distributionNote = `${failed.length} of ${operatorResults.length} operator wallet transfers failed: ${failed
            .map((f) => `${f.label} (${f.error})`)
            .join("; ")}`;
        }
      } catch (distErr) {
        distributionNote = `Operator wallet distribution failed or partially completed: ${distErr.message || distErr}`;
      }
    }

    saveLaunchManifest(ROOT, launchId, {
      name,
      symbol,
      network,
      mintAddress: result.mintAddress,
      decimals: Number(decimals ?? 6),
      supply: String(supply),
      createdAt: new Date().toISOString(),
      ownerWallet: { path: walletInfo.path, address: walletInfo.address, label: "Owner (mint authority origin)" },
      operatorWallets: operatorResults.map((r) => ({
        id: r.id,
        label: r.label,
        percent: r.percent,
        address: r.address,
        path: r.path,
        funded: r.ok !== false,
      })),
    });

    res.json({
      ok: true,
      ...result,
      metadataUri,
      metadataNote,
      wallet: { path: walletInfo.path, created: walletInfo.created, airdropped: walletInfo.airdropped },
      raydiumCreatePoolUrl: raydiumCreatePoolUrl(result.mintAddress),
      launchId,
      consoleUrl: `/console.html?launch=${launchId}`,
      operatorWallets: operatorResults,
      distributionNote,
    });
  } catch (err) {
    cleanupUpload();
    console.error(err);
    res.status(500).json({ error: err.message || String(err) });
  }
});

app.listen(PORT, "127.0.0.1", () => {
  console.log(`Coin launcher dashboard: http://127.0.0.1:${PORT}`);
  console.log(`Wallet console: http://127.0.0.1:${PORT}/console.html`);
  console.log("Bound to 127.0.0.1 only — not reachable from other machines.");
});
