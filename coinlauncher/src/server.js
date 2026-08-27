// Local-only dashboard for launching and managing a token. Binds to
// 127.0.0.1 — same security posture as the arb-scanner Flask dashboard:
// never exposed beyond this machine. Keypair files are read/written from
// disk on THIS machine by THIS process; private keys never cross a network
// boundary and never enter SQLite (only public addresses/paths do).

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
  labeledWalletPath,
} from "./wallet.js";
import { buildAndUploadMetadata } from "./metadata.js";
import { consoleApiRouter } from "./consoleApi.js";
import { projectApiRouter } from "./projectApi.js";
import { socialsApiRouter } from "./socialsApi.js";
import * as store from "./db/store.js";

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
app.use("/api/projects", projectApiRouter(ROOT));
app.use("/api/projects/:projectId/social", socialsApiRouter(ROOT));

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
    walletMode, // "new" (default) or "existing" — applies to the owner/mint-authority wallet
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

  // Project row exists before anything else so every subsequent step has
  // somewhere to record itself, even if a later step fails.
  const projectId = store.createProject(ROOT, { name, symbol, network });
  store.setState(ROOT, projectId, "PROJECT_CREATED", "Project created");
  const projectWalletsDir = path.join(WALLETS_DIR, projectId);

  const resolvedKeypairPath =
    walletMode === "existing" ? keypairPath || DEFAULT_KEYPAIR_PATH : newWalletPath(symbol, projectWalletsDir);

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
      const { path: resolved, created, balance, airdropped, keypair } = await ensureFundedDevnetWallet(resolvedKeypairPath);
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

    store.updateProjectMint(ROOT, projectId, {
      mintAddress: result.mintAddress,
      decimals: Number(decimals ?? 6),
      supply,
      metadataUri,
    });
    store.setState(ROOT, projectId, "TOKEN_CREATED", `Mint ${result.mintAddress}`);
    store.recordTransaction(ROOT, {
      projectId,
      type: "MINT",
      outputAsset: symbol,
      outputAmount: supply,
      destination: walletInfo.address,
      signature: null,
      network,
      status: "confirmed",
    });

    const ownerWalletId = store.addWallet(ROOT, {
      projectId,
      role: "owner",
      label: "Owner (mint authority origin)",
      address: walletInfo.address,
      keypairPath: walletInfo.path,
    });

    // Funding wallet: receives SOL from the operator's external wallet,
    // then performs the real market buy -- but that only ever runs on
    // mainnet (Jupiter has no devnet liquidity to swap against), so on
    // devnet this wallet's SOL balance is never actually used for
    // anything. Just create it -- don't spend today's shared devnet
    // faucet allowance on a balance nothing will touch.
    const fundingResult = loadOrCreateKeypair(labeledWalletPath("funding", projectWalletsDir));
    const fundingWalletId = store.addWallet(ROOT, {
      projectId,
      role: "funding",
      label: "Funding wallet",
      address: fundingResult.keypair.publicKey.toBase58(),
      keypairPath: fundingResult.path,
    });

    // Main Holding wallet: also just created here, not funded. It doesn't
    // need SOL until it actually pays for a distribution -- that's when
    // distributionService.js tops it up lazily on devnet, right when it's
    // needed, instead of eagerly here whether or not this launch ever
    // reaches that step.
    const mainHoldingResult = loadOrCreateKeypair(labeledWalletPath("main-holding", projectWalletsDir));
    const mainHoldingWalletId = store.addWallet(ROOT, {
      projectId,
      role: "main_holding",
      label: "Main Holding wallet",
      address: mainHoldingResult.keypair.publicKey.toBase58(),
      keypairPath: mainHoldingResult.path,
    });

    // Operator wallets are created now (so they exist, have addresses, and
    // can be shown/configured immediately) but are NOT funded here.
    // Funding them is the separate, hardened Distribution action
    // (POST /api/projects/:id/distribute) run against Main Holding's real
    // balance once it has one — not baked into the launch call.
    const operatorWallets = operatorWalletConfigs.map((w, i) => {
      const label = w.label || `Wallet ${i + 1}`;
      const wPath = labeledWalletPath(label, projectWalletsDir);
      const { keypair } = loadOrCreateKeypair(wPath);
      const walletId = store.addWallet(ROOT, {
        projectId,
        role: "operator",
        label,
        address: keypair.publicKey.toBase58(),
        keypairPath: wPath,
        allocationPercent: Number(w.percent),
      });
      return { id: walletId, label, percent: Number(w.percent), address: keypair.publicKey.toBase58() };
    });

    res.json({
      ok: true,
      ...result,
      metadataUri,
      metadataNote,
      projectId,
      overviewUrl: `/overview.html?project=${projectId}`,
      wallets: {
        owner: { id: ownerWalletId, address: walletInfo.address, created: walletInfo.created, airdropped: walletInfo.airdropped },
        funding: { id: fundingWalletId, address: fundingResult.keypair.publicKey.toBase58() },
        mainHolding: { id: mainHoldingWalletId, address: mainHoldingResult.keypair.publicKey.toBase58() },
        operators: operatorWallets,
      },
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
  console.log(`Overview: http://127.0.0.1:${PORT}/overview.html`);
  console.log("Bound to 127.0.0.1 only — not reachable from other machines.");
});
