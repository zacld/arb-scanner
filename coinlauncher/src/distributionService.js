// Hardened Main Holding -> Operator Wallets distribution. This is the
// execution layer requested for Phase 1: real source-balance validation,
// percentage validation, an idempotency ledger so a crash mid-run can be
// resumed instead of blindly restarted, and a full audit trail written as
// it happens (not just at the end). DISTRIBUTION_COMPLETE is only ever set
// once every wallet's transfer has actually confirmed on-chain.
//
// Percentages apply to the Main Holding wallet's REAL observed token
// balance at the moment of the run, not to a configured/assumed total
// supply figure -- consistent with wallets always reflecting actual
// on-chain state.

import fs from "fs";
import { Keypair, Connection, PublicKey } from "@solana/web3.js";
import { getAssociatedTokenAddress, TokenAccountNotFoundError } from "@solana/spl-token";
import { RPC_ENDPOINTS } from "./createToken.js";
import { transferShareToWallet } from "./distribute.js";
import * as store from "./db/store.js";

export class DistributionError extends Error {}

function loadKeypairFromPath(p) {
  const secret = Uint8Array.from(JSON.parse(fs.readFileSync(p, "utf8")));
  return Keypair.fromSecretKey(secret);
}

/**
 * IMPORTANT: only "the token account genuinely doesn't exist yet" collapses
 * to a balance of 0. Any other failure (network unreachable, RPC error,
 * rate limit, ...) is rethrown rather than swallowed — silently treating
 * "couldn't check the balance" the same as "confirmed zero balance" is
 * exactly the kind of ambiguity that leads to wrong distribution decisions.
 */
export async function getTokenBalanceBaseUnits(network, mintAddress, ownerPublicKey) {
  const connection = new Connection(RPC_ENDPOINTS[network], "confirmed");
  const ata = await getAssociatedTokenAddress(mintAddress, ownerPublicKey);
  try {
    const bal = await connection.getTokenAccountBalance(ata);
    return BigInt(bal.value.amount);
  } catch (err) {
    if (err instanceof TokenAccountNotFoundError || /could not find account/i.test(err.message || "")) {
      return 0n; // genuinely no token account yet — a real, confirmed zero
    }
    throw err; // anything else (network, RPC) is a real failure — don't hide it
  }
}

/**
 * @param allocations optional [{ walletId, percent }] override; defaults to
 *   each operator wallet's configured allocation_percent from the DB.
 * @param force explicitly re-run even though a prior run completed, or
 *   retry a wallet whose last attempt didn't resolve cleanly (verify on
 *   Solscan first -- see the orphaned-pending check below).
 */
export async function runDistribution(root, projectId, { allocations, force = false } = {}) {
  const project = store.getProject(root, projectId);
  if (!project) throw new DistributionError("Project not found.");
  if (!project.mint_address) throw new DistributionError("This project has no mint yet.");

  const mainHolding = store.getWalletByRole(root, projectId, "main_holding");
  if (!mainHolding) throw new DistributionError("This project has no Main Holding wallet.");

  const operators = store.getOperatorWallets(root, projectId);
  if (operators.length === 0) throw new DistributionError("This project has no operator wallets configured.");

  const allocationsToUse = allocations || operators.map((o) => ({ walletId: o.id, percent: o.allocation_percent }));
  const totalPercent = allocationsToUse.reduce((s, a) => s + Number(a.percent || 0), 0);
  if (totalPercent > 100) throw new DistributionError(`Allocation percentages total ${totalPercent}%, which is over 100%.`);
  if (totalPercent <= 0) throw new DistributionError("No allocation percentage configured — nothing to distribute.");

  const existing = store.findOperation(root, projectId, "DISTRIBUTION");
  let operationId;
  if (existing && existing.status === "confirmed") {
    if (!force) {
      throw new DistributionError(
        "A distribution has already completed for this project. Re-run with force to distribute the current balance again."
      );
    }
    operationId = store.createOperation(root, { projectId, operationType: "DISTRIBUTION", config: { allocations: allocationsToUse } });
  } else if (existing && existing.status === "pending") {
    operationId = existing.id; // resume the same in-flight operation
  } else {
    operationId = store.createOperation(root, { projectId, operationType: "DISTRIBUTION", config: { allocations: allocationsToUse } });
  }

  const mainHoldingKeypair = loadKeypairFromPath(mainHolding.keypair_path);
  const mintAddress = new PublicKey(project.mint_address);
  const sourceBalance = await getTokenBalanceBaseUnits(project.network, mintAddress, mainHoldingKeypair.publicKey);

  if (sourceBalance === 0n) {
    store.updateOperationStatus(root, operationId, "failed");
    throw new DistributionError(
      `Main Holding wallet (${mainHolding.address}) has no token balance to distribute. Fund it first, then retry.`
    );
  }

  const priorTxForOp = store.getTransactionsByOperation(root, operationId);
  const results = [];
  let anyFailed = false;

  for (const alloc of allocationsToUse) {
    const operatorWallet = operators.find((o) => o.id === alloc.walletId);
    if (!operatorWallet) {
      anyFailed = true;
      results.push({ walletId: alloc.walletId, ok: false, error: "Unknown operator wallet id." });
      continue;
    }

    const confirmedTx = priorTxForOp.find((t) => t.wallet_id === operatorWallet.id && t.status === "confirmed");
    if (confirmedTx) {
      results.push({ walletId: operatorWallet.id, label: operatorWallet.label, ok: true, signature: confirmedTx.signature, resumed: true });
      continue;
    }

    // A pending row with no resolved outcome means a previous attempt was
    // interrupted mid-flight (process crash between submit and record).
    // Don't silently retry -- that's exactly the double-transfer risk
    // idempotency is supposed to prevent. Surface it instead.
    const orphanedPending = priorTxForOp.find((t) => t.wallet_id === operatorWallet.id && t.status === "pending");
    if (orphanedPending && !force) {
      anyFailed = true;
      results.push({
        walletId: operatorWallet.id,
        label: operatorWallet.label,
        ok: false,
        error: `A previous attempt for this wallet (tx #${orphanedPending.id}) did not resolve. Verify on Solscan whether it landed before retrying with force.`,
      });
      continue;
    }

    const amountBaseUnits = (sourceBalance * BigInt(Math.round(Number(alloc.percent) * 100))) / 10000n;

    const pendingTxId = store.recordTransaction(root, {
      projectId,
      walletId: operatorWallet.id,
      walletRole: "operator",
      type: "DISTRIBUTION",
      inputAsset: project.symbol,
      inputAmount: amountBaseUnits.toString(),
      outputAsset: project.symbol,
      outputAmount: amountBaseUnits.toString(),
      destination: operatorWallet.address,
      network: project.network,
      status: "pending",
      operationId,
    });

    try {
      const { signature } = await transferShareToWallet({
        network: project.network,
        fromKeypair: mainHoldingKeypair,
        toPublicKey: new PublicKey(operatorWallet.address),
        mintAddress,
        decimals: project.decimals,
        amountBaseUnits,
      });
      store.updateTransaction(root, pendingTxId, { status: "confirmed", signature });
      results.push({ walletId: operatorWallet.id, label: operatorWallet.label, ok: true, signature, amount: amountBaseUnits.toString() });
    } catch (err) {
      anyFailed = true;
      store.updateTransaction(root, pendingTxId, { status: "failed", error: err.message || String(err) });
      results.push({ walletId: operatorWallet.id, label: operatorWallet.label, ok: false, error: err.message || String(err) });
    }
  }

  store.updateOperationStatus(root, operationId, anyFailed ? "pending" : "confirmed");
  if (!anyFailed) {
    store.setState(root, projectId, "DISTRIBUTION_COMPLETE", `Operation ${operationId}`);
  } else {
    store.setState(root, projectId, "DISTRIBUTION_PENDING", `Operation ${operationId} incomplete — see transaction log`);
  }

  return { operationId, results, complete: !anyFailed };
}
