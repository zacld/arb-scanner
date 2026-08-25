// The Funding Wallet's real SOL -> TOKEN market buy, and forwarding the
// result to Main Holding. Two separate, explicit, separately-retriable
// operations (not one atomic mega-transaction) -- consistent with
// treating the launch as observable states, not one giant transaction.
//
// Both are OPERATION kind (tracked via the operations ledger + chainTx.js's
// hardened pipeline), same discipline as distributionService.js:
//   - never attempted without a real, live-verified route (no mocked
//     success -- if ROUTE_CONFIRMED isn't true right now, this refuses
//     and explains why, full stop)
//   - assertWalletClearToTransact before any new write
//   - amounts computed from real observed balances, not assumed figures
//   - GBP/display-only concerns never enter these calculations -- SOL and
//     base units throughout

import fs from "fs";
import { Keypair, Connection, PublicKey, VersionedTransaction } from "@solana/web3.js";
import { RPC_ENDPOINTS } from "./createToken.js";
import { getBalanceSol } from "./wallet.js";
import { getTokenBalanceBaseUnits } from "./distributionService.js";
import { buildShareTransferTransaction } from "./distribute.js";
import { getSwapQuote, buildSwapTransaction } from "./jupiter.js";
import { checkRoute } from "./liquidityService.js";
import { submitAndTrack, assertWalletClearToTransact } from "./chainTx.js";
import * as store from "./db/store.js";

export class FundingSwapError extends Error {}

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";

function loadKeypairFromPath(p) {
  const secret = Uint8Array.from(JSON.parse(fs.readFileSync(p, "utf8")));
  return Keypair.fromSecretKey(secret);
}

/**
 * Step 1: Funding wallet buys TOKEN with (balance - reserve) * swapPercent%
 * of its real SOL balance, via a real Jupiter route. Refuses outright if
 * no live route exists -- never submits a transaction expected to fail.
 */
export async function runFundingSwap(root, projectId, { swapPercent, slippageBps, force = false } = {}) {
  const project = store.getProject(root, projectId);
  if (!project) throw new FundingSwapError("Project not found.");
  if (!project.mint_address) throw new FundingSwapError("This project has no mint yet.");

  const funding = store.getWalletByRole(root, projectId, "funding");
  if (!funding) throw new FundingSwapError("This project has no Funding wallet.");

  if (project.network !== "mainnet-beta") {
    throw new FundingSwapError("The funding swap requires mainnet — devnet has no real liquidity to swap against.");
  }

  const route = await checkRoute(project.network, project.mint_address);
  if (!route.available) {
    throw new FundingSwapError(`No executable route is currently available for this token: ${route.reason}`);
  }

  const existing = store.findOperation(root, projectId, "FUNDING_SWAP");
  if (existing && existing.status === "confirmed" && !force) {
    throw new FundingSwapError("A funding swap has already completed for this project. Re-run with force to swap again.");
  }
  const operationId =
    existing && existing.status === "pending"
      ? existing.id
      : store.createOperation(root, { projectId, operationType: "FUNDING_SWAP", config: { swapPercent, slippageBps } });

  await assertWalletClearToTransact(root, { network: project.network, walletId: funding.id });

  const reserveSol = project.funding_reserve_sol ?? 0.02;
  const percent = swapPercent ?? project.funding_swap_percent ?? 100;
  const fundingSol = await getBalanceSol(new PublicKey(funding.address), project.network);
  const swappableSol = (fundingSol - reserveSol) * (percent / 100);

  if (swappableSol <= 0) {
    store.updateOperationStatus(root, operationId, "failed");
    throw new FundingSwapError(
      `Funding wallet has ${fundingSol} SOL; after reserving ${reserveSol} SOL for fees there's nothing left to swap. Send it more SOL first.`
    );
  }

  const amountLamports = Math.round(swappableSol * 1e9);
  const quote = await getSwapQuote({
    inputMint: WRAPPED_SOL_MINT,
    outputMint: project.mint_address,
    amount: amountLamports,
    slippageBps: slippageBps ?? project.swap_slippage_bps ?? 100,
  });

  const fundingKeypair = loadKeypairFromPath(funding.keypair_path);
  const { swapTransactionBase64, lastValidBlockHeight } = await buildSwapTransaction({
    userPublicKey: funding.address,
    quoteResponse: quote,
  });
  const transaction = VersionedTransaction.deserialize(Buffer.from(swapTransactionBase64, "base64"));

  const result = await submitAndTrack(root, {
    network: project.network,
    transaction,
    signers: [fundingKeypair],
    prebuilt: { lastValidBlockHeight },
    meta: {
      projectId,
      walletId: funding.id,
      walletRole: "funding",
      type: "SWAP",
      inputAsset: "SOL",
      inputAmount: String(swappableSol),
      outputAsset: project.symbol,
      outputAmount: quote.outAmount,
      route: "jupiter",
      operationId,
    },
  });

  if (result.status === "confirmed") {
    store.updateOperationStatus(root, operationId, "confirmed");
    store.setState(root, projectId, "FUNDING_SWAP_COMPLETE", `Operation ${operationId}`);
  } else {
    store.updateOperationStatus(root, operationId, result.status === "expired" ? "pending" : "failed");
  }

  return { operationId, ...result, swappableSol, quote };
}

/**
 * Step 2: forward whatever TOKEN the Funding wallet actually holds (real
 * balance, not the swap quote's predicted output) to Main Holding.
 */
export async function runFundingForward(root, projectId, { force = false } = {}) {
  const project = store.getProject(root, projectId);
  if (!project) throw new FundingSwapError("Project not found.");
  if (!project.mint_address) throw new FundingSwapError("This project has no mint yet.");

  const funding = store.getWalletByRole(root, projectId, "funding");
  if (!funding) throw new FundingSwapError("This project has no Funding wallet.");
  const mainHolding = store.getWalletByRole(root, projectId, "main_holding");
  if (!mainHolding) throw new FundingSwapError("This project has no Main Holding wallet.");

  const existing = store.findOperation(root, projectId, "FUNDING_FORWARD");
  if (existing && existing.status === "confirmed" && !force) {
    throw new FundingSwapError("Funding has already been forwarded to Main Holding for this project. Re-run with force to forward again.");
  }
  const operationId =
    existing && existing.status === "pending" ? existing.id : store.createOperation(root, { projectId, operationType: "FUNDING_FORWARD", config: {} });

  await assertWalletClearToTransact(root, { network: project.network, walletId: funding.id });

  const fundingKeypair = loadKeypairFromPath(funding.keypair_path);
  const mintAddress = new PublicKey(project.mint_address);
  const balance = await getTokenBalanceBaseUnits(project.network, mintAddress, fundingKeypair.publicKey);

  if (balance === 0n) {
    store.updateOperationStatus(root, operationId, "failed");
    throw new FundingSwapError(`Funding wallet (${funding.address}) has no ${project.symbol} balance to forward.`);
  }

  const connection = new Connection(RPC_ENDPOINTS[project.network], "confirmed");
  const { transaction } = await buildShareTransferTransaction({
    connection,
    fromKeypair: fundingKeypair,
    toPublicKey: new PublicKey(mainHolding.address),
    mintAddress,
    decimals: project.decimals,
    amountBaseUnits: balance,
  });

  if (!transaction) {
    store.updateOperationStatus(root, operationId, "confirmed");
    return { operationId, status: "confirmed", skipped: true };
  }

  const result = await submitAndTrack(root, {
    network: project.network,
    transaction,
    signers: [fundingKeypair],
    meta: {
      projectId,
      walletId: funding.id,
      walletRole: "funding",
      type: "TRANSFER",
      inputAsset: project.symbol,
      inputAmount: balance.toString(),
      outputAsset: project.symbol,
      outputAmount: balance.toString(),
      destination: mainHolding.address,
      operationId,
    },
  });

  store.updateOperationStatus(root, operationId, result.status === "confirmed" ? "confirmed" : result.status === "expired" ? "pending" : "failed");
  // MAIN_WALLET_FUNDED is derived live from Main Holding's real balance
  // (stateMachine.js) -- no explicit setState needed here.
  return { operationId, ...result };
}
