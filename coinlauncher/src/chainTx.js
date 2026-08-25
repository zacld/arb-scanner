// Hardened blockchain-write pipeline: build -> sign -> persist (with
// signature) -> broadcast -> poll/confirm -> update -> caller advances
// whatever milestone/operation state depends on it.
//
// The key property this closes: a Solana transaction's signature is
// deterministic from signing (it's the signer's ed25519 signature over the
// message, not an ID assigned by the network), so it's known *before*
// broadcasting. Persisting it right after signing means a crash at any
// point after that leaves a durable record reconciliation can resolve
// against the chain later -- instead of the old approach (sign+send+
// confirm as one opaque call) where a crash mid-flight left nothing to
// check.
//
// Every operation that writes real funds should go through submitAndTrack,
// and every wallet should be reconciled (assertWalletClearToTransact)
// before a new transaction is built for it.

import { Connection, Transaction, VersionedTransaction } from "@solana/web3.js";
import bs58 from "bs58";
import { RPC_ENDPOINTS } from "./createToken.js";
import * as store from "./db/store.js";

function txSignatureBase58(transaction) {
  if (transaction instanceof VersionedTransaction) {
    return bs58.encode(transaction.signatures[0]);
  }
  return bs58.encode(transaction.signature);
}

/**
 * Pure decision function: given what we know about a submitted
 * transaction (its on-chain signature status, if any, and whether its
 * blockhash has expired), decide what it actually means. Exported
 * separately from any RPC call so it's unit-testable without live network.
 */
export function classifyReconciliation({ signatureStatus, lastValidBlockHeight, currentBlockHeight }) {
  if (
    signatureStatus &&
    signatureStatus.confirmationStatus &&
    (signatureStatus.confirmationStatus === "confirmed" || signatureStatus.confirmationStatus === "finalized")
  ) {
    return signatureStatus.err
      ? { outcome: "failed", reason: JSON.stringify(signatureStatus.err) }
      : { outcome: "confirmed" };
  }
  if (lastValidBlockHeight != null && currentBlockHeight != null && currentBlockHeight > lastValidBlockHeight) {
    // The blockhash this transaction was built against has expired without
    // it ever landing. It is now permanently impossible for these exact
    // signed bytes to confirm -- not "maybe still pending", genuinely dead.
    // A NEW transaction (fresh blockhash) for the same logical operation is
    // safe to build from here; this is verified fact, not a blind retry.
    return { outcome: "expired" };
  }
  return { outcome: "still-pending" };
}

/**
 * Sign, persist, broadcast, and poll one transaction to a terminal outcome
 * (or give up after pollTimeoutMs and leave it 'submitted' for later
 * reconciliation -- never silently marked failed just because we stopped
 * watching).
 *
 * `transaction` must have its instructions (and feePayer, for legacy
 * Transactions) set but NOT recentBlockhash/signatures -- this function
 * fetches a fresh blockhash itself so it can track expiry.
 */
export async function submitAndTrack(root, { network, transaction, signers, meta, pollTimeoutMs = 30000 }) {
  const connection = new Connection(RPC_ENDPOINTS[network], "confirmed");
  const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash();

  let signature;
  if (transaction instanceof VersionedTransaction) {
    transaction.message.recentBlockhash = blockhash;
    transaction.sign(signers);
  } else {
    transaction.recentBlockhash = blockhash;
    transaction.feePayer = signers[0].publicKey;
    transaction.sign(...signers);
  }
  signature = txSignatureBase58(transaction);

  // Persisted BEFORE broadcasting. If the process dies right here, this
  // row is reconciliation's starting point -- not a guess.
  const txId = store.recordTransaction(root, {
    ...meta,
    signature,
    lastValidBlockHeight,
    network,
    status: "submitted",
  });

  try {
    const raw = transaction.serialize();
    await connection.sendRawTransaction(raw, { skipPreflight: false, maxRetries: 3 });
  } catch {
    // A send-time error doesn't prove the transaction didn't land (the
    // network can accept it despite a client-side timeout/error). Fall
    // through to polling rather than assuming failure here.
  }

  const deadline = Date.now() + pollTimeoutMs;
  while (Date.now() < deadline) {
    const { value: signatureStatus } = await connection.getSignatureStatus(signature, { searchTransactionHistory: true });
    const currentBlockHeight = await connection.getBlockHeight();
    const result = classifyReconciliation({ signatureStatus, lastValidBlockHeight, currentBlockHeight });

    if (result.outcome === "confirmed") {
      store.updateTransaction(root, txId, { status: "confirmed" });
      return { signature, txId, status: "confirmed" };
    }
    if (result.outcome === "failed") {
      store.updateTransaction(root, txId, { status: "failed", error: result.reason });
      return { signature, txId, status: "failed", error: result.reason };
    }
    if (result.outcome === "expired") {
      store.updateTransaction(root, txId, { status: "expired" });
      return { signature, txId, status: "expired" };
    }
    await new Promise((r) => setTimeout(r, 1500));
  }

  // Genuinely unresolved after our poll window -- left as 'submitted' on
  // purpose. reconcileWallet picks this up on the next call or app restart.
  return { signature, txId, status: "submitted" };
}

/**
 * Try to resolve every unresolved (status='submitted') transaction for one
 * wallet against the chain. Safe to call any time, including at startup.
 * `_fetchStatus`/`_fetchBlockHeight` are injectable for testing; default to
 * real RPC calls.
 */
export async function reconcileWallet(
  root,
  { network, walletId },
  {
    _fetchStatus = async (connection, signature) => (await connection.getSignatureStatus(signature, { searchTransactionHistory: true })).value,
    _fetchBlockHeight = async (connection) => connection.getBlockHeight(),
  } = {}
) {
  const rows = store.getUnresolvedTransactionsForWallet(root, walletId);
  if (rows.length === 0) return [];

  const connection = new Connection(RPC_ENDPOINTS[network], "confirmed");
  const results = [];
  for (const row of rows) {
    if (!row.signature) {
      // Predates this pipeline, or crashed before signing ever completed.
      // Nothing on-chain to check -- surface for manual review, don't guess.
      results.push({ id: row.id, outcome: "unknown-no-signature" });
      continue;
    }
    const signatureStatus = await _fetchStatus(connection, row.signature);
    const currentBlockHeight = await _fetchBlockHeight(connection);
    const result = classifyReconciliation({ signatureStatus, lastValidBlockHeight: row.last_valid_block_height, currentBlockHeight });

    if (result.outcome === "confirmed") store.updateTransaction(root, row.id, { status: "confirmed" });
    else if (result.outcome === "failed") store.updateTransaction(root, row.id, { status: "failed", error: result.reason });
    else if (result.outcome === "expired") store.updateTransaction(root, row.id, { status: "expired" });

    results.push({ id: row.id, signature: row.signature, ...result });
  }
  return results;
}

/**
 * The normal recovery path before building any new transaction for a
 * wallet: reconcile first (so anything genuinely resolved gets updated),
 * then refuse if something is still truly ambiguous. `force` bypassing
 * this is a deliberate, logged exception -- not how retries normally work.
 */
export async function assertWalletClearToTransact(root, { network, walletId }, opts) {
  const reconciled = await reconcileWallet(root, { network, walletId }, opts);
  const stillUnresolved = reconciled.filter((r) => r.outcome === "still-pending" || r.outcome === "unknown-no-signature");
  if (stillUnresolved.length > 0) {
    throw new Error(
      `This wallet has ${stillUnresolved.length} transaction(s) that couldn't be confirmed as succeeded, ` +
      `failed, or expired (signature(s): ${stillUnresolved.map((r) => r.signature || "unknown").join(", ")}). ` +
      `Wait and try again, or verify manually on Solscan before forcing.`
    );
  }
}
