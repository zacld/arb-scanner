// API for the wallet console: balances, send, transaction history, QR
// codes, and (mainnet only) swaps. Every endpoint operates only on wallets
// recorded in the DB for a given project — never an arbitrary client-
// supplied path. Re-plumbed onto the SQLite store; the actual balance/
// send/history/swap logic is unchanged from the earlier JSON-manifest
// version.

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
import * as store from "./db/store.js";
import { getSwapQuote, executeSwap } from "./jupiter.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";

export function consoleApiRouter(root) {
  const router = express.Router();

  function getProjectOr404(req, res) {
    const project = store.getProject(root, req.params.projectId);
    if (!project) {
      res.status(404).json({ error: "Project not found." });
      return null;
    }
    return project;
  }

  function getWalletOr404(req, res) {
    const wallet = store.getWallet(root, req.params.walletId);
    if (!wallet || wallet.project_id !== req.params.projectId) {
      res.status(404).json({ error: "Wallet not found in this project." });
      return null;
    }
    return wallet;
  }

  function loadKeypair(wallet) {
    const secret = Uint8Array.from(JSON.parse(fs.readFileSync(wallet.keypair_path, "utf8")));
    return Keypair.fromSecretKey(secret);
  }

  function connectionFor(network) {
    return new Connection(RPC_ENDPOINTS[network], "confirmed");
  }

  router.get("/projects/:projectId/wallets", (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    res.json({ wallets: store.getWalletsByProject(root, project.id) });
  });

  router.get("/projects/:projectId/wallets/:walletId/balance", async (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    const wallet = getWalletOr404(req, res);
    if (!wallet) return;

    try {
      const connection = connectionFor(project.network);
      const pubkey = new PublicKey(wallet.address);
      const solLamports = await connection.getBalance(pubkey);

      let tokenAmount = "0";
      let tokenUiAmount = 0;
      if (project.mint_address) {
        try {
          const ata = await getAssociatedTokenAddress(new PublicKey(project.mint_address), pubkey);
          const bal = await connection.getTokenAccountBalance(ata);
          tokenAmount = bal.value.amount;
          tokenUiAmount = bal.value.uiAmount || 0;
        } catch (e) {
          if (!(e instanceof TokenAccountNotFoundError) && !/could not find account/i.test(e.message || "")) {
            throw e;
          }
          // No token account yet for this wallet — balance is just 0.
        }
      }

      res.json({
        address: wallet.address,
        role: wallet.role,
        sol: solLamports / LAMPORTS_PER_SOL,
        token: { amount: tokenAmount, uiAmount: tokenUiAmount, symbol: project.symbol, decimals: project.decimals },
      });
    } catch (err) {
      res.status(500).json({ error: err.message || String(err) });
    }
  });

  router.get("/projects/:projectId/wallets/:walletId/history", async (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    const wallet = getWalletOr404(req, res);
    if (!wallet) return;

    try {
      const connection = connectionFor(project.network);
      const pubkey = new PublicKey(wallet.address);
      const sigs = await connection.getSignaturesForAddress(pubkey, { limit: 20 });
      const cluster = project.network === "devnet" ? "?cluster=devnet" : "";
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

  router.get("/projects/:projectId/wallets/:walletId/qrcode.png", async (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    const wallet = getWalletOr404(req, res);
    if (!wallet) return;
    try {
      res.setHeader("Content-Type", "image/png");
      const buffer = await QRCode.toBuffer(wallet.address, { width: 240, margin: 1 });
      res.send(buffer);
    } catch (err) {
      res.status(500).json({ error: err.message || String(err) });
    }
  });

  router.post("/projects/:projectId/wallets/:walletId/send", async (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    const wallet = getWalletOr404(req, res);
    if (!wallet) return;

    const { assetType, to, amount } = req.body || {};
    if (!to || !amount || !["SOL", "TOKEN"].includes(assetType)) {
      return res.status(400).json({ error: "Required: assetType ('SOL' or 'TOKEN'), to, amount." });
    }

    // Distinguish a transfer between our own wallets from a withdrawal to
    // an external address, for the audit trail (§9/§19 of the spec).
    const allProjectWallets = store.getWalletsByProject(root, project.id);
    const isInternal = allProjectWallets.some((w) => w.address === to);
    const txType = isInternal ? "TRANSFER" : "WITHDRAWAL";

    try {
      const connection = connectionFor(project.network);
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
        const mint = new PublicKey(project.mint_address);
        const fromAta = await getAssociatedTokenAddress(mint, keypair.publicKey);
        const toAtaAccount = await getOrCreateAssociatedTokenAccount(connection, keypair, mint, toPubkey);
        const amountBaseUnits = BigInt(Math.round(Number(amount) * 10 ** project.decimals));
        const tx = new Transaction().add(
          createTransferCheckedInstruction(
            fromAta,
            mint,
            toAtaAccount.address,
            keypair.publicKey,
            amountBaseUnits,
            project.decimals
          )
        );
        signature = await sendAndConfirmTransaction(connection, tx, [keypair], { commitment: "confirmed" });
      }

      store.recordTransaction(root, {
        projectId: project.id,
        walletId: wallet.id,
        walletRole: wallet.role,
        type: txType,
        inputAsset: assetType === "SOL" ? "SOL" : project.symbol,
        inputAmount: amount,
        destination: to,
        signature,
        network: project.network,
        status: "confirmed",
      });

      const cluster = project.network === "devnet" ? "?cluster=devnet" : "";
      res.json({ ok: true, signature, explorerUrl: `https://solscan.io/tx/${signature}${cluster}` });
    } catch (err) {
      store.recordTransaction(root, {
        projectId: project.id,
        walletId: wallet.id,
        walletRole: wallet.role,
        type: txType,
        inputAsset: assetType === "SOL" ? "SOL" : project.symbol,
        inputAmount: amount,
        destination: to,
        network: project.network,
        status: "failed",
        error: err.message || String(err),
      });
      res.status(500).json({ error: err.message || String(err) });
    }
  });

  router.post("/projects/:projectId/wallets/:walletId/swap/quote", async (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    const wallet = getWalletOr404(req, res);
    if (!wallet) return;
    if (project.network !== "mainnet-beta") {
      return res.status(400).json({ error: "Swap requires mainnet — devnet has no real liquidity to swap against." });
    }

    const { direction, amount, slippageBps } = req.body || {}; // direction: "SOL_TO_TOKEN" | "TOKEN_TO_SOL"
    try {
      const mint = project.mint_address;
      const inputMint = direction === "TOKEN_TO_SOL" ? mint : WRAPPED_SOL_MINT;
      const outputMint = direction === "TOKEN_TO_SOL" ? WRAPPED_SOL_MINT : mint;
      const inputDecimals = direction === "TOKEN_TO_SOL" ? project.decimals : 9;
      const amountBaseUnits = Math.round(Number(amount) * 10 ** inputDecimals);

      const quote = await getSwapQuote({ inputMint, outputMint, amount: amountBaseUnits, slippageBps: slippageBps || 100 });
      res.json({ quote });
    } catch (err) {
      res.status(500).json({ error: err.message || String(err) });
    }
  });

  router.post("/projects/:projectId/wallets/:walletId/swap/execute", async (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    const wallet = getWalletOr404(req, res);
    if (!wallet) return;
    if (project.network !== "mainnet-beta") {
      return res.status(400).json({ error: "Swap requires mainnet — devnet has no real liquidity to swap against." });
    }

    const { quoteResponse } = req.body || {};
    if (!quoteResponse) return res.status(400).json({ error: "quoteResponse is required (from /swap/quote)." });

    try {
      const keypair = loadKeypair(wallet);
      const signature = await executeSwap({ keypair, quoteResponse });
      store.recordTransaction(root, {
        projectId: project.id,
        walletId: wallet.id,
        walletRole: wallet.role,
        type: "SWAP",
        route: "jupiter",
        signature,
        network: project.network,
        status: "confirmed",
      });
      res.json({ ok: true, signature, explorerUrl: `https://solscan.io/tx/${signature}` });
    } catch (err) {
      res.status(500).json({ error: err.message || String(err) });
    }
  });

  return router;
}
