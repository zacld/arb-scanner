// API for the operator wallet console (public/console.html): balances,
// send, transaction history, QR codes, and (mainnet only) swaps. Every
// endpoint here operates only on wallets this tool itself created and
// recorded in a launch manifest — never an arbitrary path from the client.

import express from "express";
import fs from "fs";
import QRCode from "qrcode";
import {
  Connection,
  Keypair,
  PublicKey,
  SystemProgram,
  Transaction,
  sendAndConfirmTransaction,
  LAMPORTS_PER_SOL,
} from "@solana/web3.js";
import {
  getAssociatedTokenAddress,
  getOrCreateAssociatedTokenAccount,
  createTransferCheckedInstruction,
  TokenAccountNotFoundError,
} from "@solana/spl-token";
import { RPC_ENDPOINTS } from "./createToken.js";
import { listLaunches, loadLaunchManifest, findWalletInLaunch } from "./launchManifest.js";
import { getSwapQuote, executeSwap, MAINNET_RPC } from "./jupiter.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";

export function consoleApiRouter(root) {
  const router = express.Router();

  function getManifestOr404(req, res) {
    const manifest = loadLaunchManifest(root, req.params.launchId);
    if (!manifest) {
      res.status(404).json({ error: "Launch not found." });
      return null;
    }
    return manifest;
  }

  function getWalletOr404(req, res, manifest) {
    const wallet = findWalletInLaunch(manifest, req.params.walletId);
    if (!wallet) {
      res.status(404).json({ error: "Wallet not found in this launch." });
      return null;
    }
    return wallet;
  }

  function loadKeypair(walletEntry) {
    const secret = Uint8Array.from(JSON.parse(fs.readFileSync(walletEntry.path, "utf8")));
    return Keypair.fromSecretKey(secret);
  }

  function connectionFor(network) {
    return new Connection(RPC_ENDPOINTS[network], "confirmed");
  }

  router.get("/launches", (_req, res) => {
    const launches = listLaunches(root).map((l) => ({
      id: l.id,
      name: l.name,
      symbol: l.symbol,
      network: l.network,
      mintAddress: l.mintAddress,
      createdAt: l.createdAt,
      walletCount: 1 + (l.operatorWallets || []).length,
    }));
    res.json({ launches });
  });

  router.get("/launches/:launchId", (req, res) => {
    const manifest = getManifestOr404(req, res);
    if (!manifest) return;
    res.json({ manifest });
  });

  router.get("/launches/:launchId/wallets/:walletId/balance", async (req, res) => {
    const manifest = getManifestOr404(req, res);
    if (!manifest) return;
    const wallet = getWalletOr404(req, res, manifest);
    if (!wallet) return;

    try {
      const connection = connectionFor(manifest.network);
      const pubkey = new PublicKey(wallet.address);
      const solLamports = await connection.getBalance(pubkey);

      let tokenAmount = "0";
      let tokenUiAmount = 0;
      try {
        const ata = await getAssociatedTokenAddress(new PublicKey(manifest.mintAddress), pubkey);
        const bal = await connection.getTokenAccountBalance(ata);
        tokenAmount = bal.value.amount;
        tokenUiAmount = bal.value.uiAmount || 0;
      } catch (e) {
        if (!(e instanceof TokenAccountNotFoundError) && !/could not find account/i.test(e.message || "")) {
          throw e;
        }
        // No token account yet for this wallet — balance is just 0.
      }

      res.json({
        address: wallet.address,
        sol: solLamports / LAMPORTS_PER_SOL,
        token: { amount: tokenAmount, uiAmount: tokenUiAmount, symbol: manifest.symbol, decimals: manifest.decimals },
      });
    } catch (err) {
      res.status(500).json({ error: err.message || String(err) });
    }
  });

  router.get("/launches/:launchId/wallets/:walletId/history", async (req, res) => {
    const manifest = getManifestOr404(req, res);
    if (!manifest) return;
    const wallet = getWalletOr404(req, res, manifest);
    if (!wallet) return;

    try {
      const connection = connectionFor(manifest.network);
      const pubkey = new PublicKey(wallet.address);
      const sigs = await connection.getSignaturesForAddress(pubkey, { limit: 20 });
      const cluster = manifest.network === "devnet" ? "?cluster=devnet" : "";
      res.json({
        transactions: sigs.map((s) => ({
          signature: s.signature,
          slot: s.slot,
          blockTime: s.blockTime,
          status: s.err ? "failed" : "success",
          explorerUrl: `https://solscan.io/tx/${s.signature}${cluster}`,
        })),
      });
    } catch (err) {
      res.status(500).json({ error: err.message || String(err) });
    }
  });

  router.get("/launches/:launchId/wallets/:walletId/qrcode.png", async (req, res) => {
    const manifest = getManifestOr404(req, res);
    if (!manifest) return;
    const wallet = getWalletOr404(req, res, manifest);
    if (!wallet) return;
    try {
      res.setHeader("Content-Type", "image/png");
      const buffer = await QRCode.toBuffer(wallet.address, { width: 240, margin: 1 });
      res.send(buffer);
    } catch (err) {
      res.status(500).json({ error: err.message || String(err) });
    }
  });

  router.post("/launches/:launchId/wallets/:walletId/send", async (req, res) => {
    const manifest = getManifestOr404(req, res);
    if (!manifest) return;
    const wallet = getWalletOr404(req, res, manifest);
    if (!wallet) return;

    const { assetType, to, amount } = req.body || {};
    if (!to || !amount || !["SOL", "TOKEN"].includes(assetType)) {
      return res.status(400).json({ error: "Required: assetType ('SOL' or 'TOKEN'), to, amount." });
    }

    try {
      const connection = connectionFor(manifest.network);
      const keypair = loadKeypair(wallet);
      const toPubkey = new PublicKey(to);
      let signature;

      if (assetType === "SOL") {
        const tx = new Transaction().add(
          SystemProgram.transfer({
            fromPubkey: keypair.publicKey,
            toPubkey,
            lamports: Math.round(Number(amount) * LAMPORTS_PER_SOL),
          })
        );
        signature = await sendAndConfirmTransaction(connection, tx, [keypair], { commitment: "confirmed" });
      } else {
        const mint = new PublicKey(manifest.mintAddress);
        const fromAta = await getAssociatedTokenAddress(mint, keypair.publicKey);
        const toAtaAccount = await getOrCreateAssociatedTokenAccount(connection, keypair, mint, toPubkey);
        const amountBaseUnits = BigInt(Math.round(Number(amount) * 10 ** manifest.decimals));
        const tx = new Transaction().add(
          createTransferCheckedInstruction(
            fromAta,
            mint,
            toAtaAccount.address,
            keypair.publicKey,
            amountBaseUnits,
            manifest.decimals
          )
        );
        signature = await sendAndConfirmTransaction(connection, tx, [keypair], { commitment: "confirmed" });
      }

      const cluster = manifest.network === "devnet" ? "?cluster=devnet" : "";
      res.json({ ok: true, signature, explorerUrl: `https://solscan.io/tx/${signature}${cluster}` });
    } catch (err) {
      res.status(500).json({ error: err.message || String(err) });
    }
  });

  router.post("/launches/:launchId/wallets/:walletId/swap/quote", async (req, res) => {
    const manifest = getManifestOr404(req, res);
    if (!manifest) return;
    const wallet = getWalletOr404(req, res, manifest);
    if (!wallet) return;
    if (manifest.network !== "mainnet-beta") {
      return res.status(400).json({ error: "Swap requires mainnet — devnet has no real liquidity to swap against." });
    }

    const { direction, amount, slippageBps } = req.body || {}; // direction: "SOL_TO_TOKEN" | "TOKEN_TO_SOL"
    try {
      const mint = manifest.mintAddress;
      const inputMint = direction === "TOKEN_TO_SOL" ? mint : WRAPPED_SOL_MINT;
      const outputMint = direction === "TOKEN_TO_SOL" ? WRAPPED_SOL_MINT : mint;
      const inputDecimals = direction === "TOKEN_TO_SOL" ? manifest.decimals : 9;
      const amountBaseUnits = Math.round(Number(amount) * 10 ** inputDecimals);

      const quote = await getSwapQuote({ inputMint, outputMint, amount: amountBaseUnits, slippageBps: slippageBps || 100 });
      res.json({ quote });
    } catch (err) {
      res.status(500).json({ error: err.message || String(err) });
    }
  });

  router.post("/launches/:launchId/wallets/:walletId/swap/execute", async (req, res) => {
    const manifest = getManifestOr404(req, res);
    if (!manifest) return;
    const wallet = getWalletOr404(req, res, manifest);
    if (!wallet) return;
    if (manifest.network !== "mainnet-beta") {
      return res.status(400).json({ error: "Swap requires mainnet — devnet has no real liquidity to swap against." });
    }

    const { quoteResponse } = req.body || {};
    if (!quoteResponse) return res.status(400).json({ error: "quoteResponse is required (from /swap/quote)." });

    try {
      const keypair = loadKeypair(wallet);
      const signature = await executeSwap({ keypair, quoteResponse });
      res.json({ ok: true, signature, explorerUrl: `https://solscan.io/tx/${signature}` });
    } catch (err) {
      res.status(500).json({ error: err.message || String(err) });
    }
  });

  return router;
}
