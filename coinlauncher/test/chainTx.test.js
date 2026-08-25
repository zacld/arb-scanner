// Tests for the crash-safety hardening: the pure reconciliation decision
// function (no network needed at all), and the reconcile/guard behavior
// using an injected fake RPC response (no live network needed either --
// this is exactly why reconcileWallet accepts _fetchStatus/_fetchBlockHeight
// overrides).

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import os from "os";
import path from "path";

const TEST_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), "coinlauncher-chaintx-test-"));

const store = await import("../src/db/store.js");
const { classifyReconciliation, reconcileWallet, assertWalletClearToTransact } = await import("../src/chainTx.js");

test.after(() => {
  fs.rmSync(TEST_ROOT, { recursive: true, force: true });
});

test("classifyReconciliation: confirmed success", () => {
  const result = classifyReconciliation({
    signatureStatus: { confirmationStatus: "confirmed", err: null },
    lastValidBlockHeight: 1000,
    currentBlockHeight: 990,
  });
  assert.equal(result.outcome, "confirmed");
});

test("classifyReconciliation: confirmed but landed with an error", () => {
  const result = classifyReconciliation({
    signatureStatus: { confirmationStatus: "finalized", err: { InstructionError: [0, "Custom"] } },
    lastValidBlockHeight: 1000,
    currentBlockHeight: 990,
  });
  assert.equal(result.outcome, "failed");
  assert.ok(result.reason.includes("InstructionError"));
});

test("classifyReconciliation: not found yet, still within blockhash validity -> still-pending, not touched", () => {
  const result = classifyReconciliation({
    signatureStatus: null,
    lastValidBlockHeight: 1000,
    currentBlockHeight: 990, // still before expiry
  });
  assert.equal(result.outcome, "still-pending");
});

test("classifyReconciliation: not found and blockhash expired -> expired, safe to retry", () => {
  const result = classifyReconciliation({
    signatureStatus: null,
    lastValidBlockHeight: 1000,
    currentBlockHeight: 1001, // past expiry
  });
  assert.equal(result.outcome, "expired");
});

test("reconcileWallet: resolves a submitted transaction to confirmed using an injected chain response", async () => {
  const projectId = store.createProject(TEST_ROOT, { name: "T", symbol: "TT", network: "devnet" });
  const walletId = store.addWallet(TEST_ROOT, { projectId, role: "main_holding", label: "Main Holding", address: "Addr1", keypairPath: "/tmp/w.json" });

  store.recordTransaction(TEST_ROOT, {
    projectId, walletId, walletRole: "main_holding", type: "DISTRIBUTION",
    signature: "SIG_CONFIRMED_CASE", lastValidBlockHeight: 1000, network: "devnet", status: "submitted",
  });

  const results = await reconcileWallet(
    TEST_ROOT,
    { network: "devnet", walletId },
    { _fetchStatus: async () => ({ confirmationStatus: "confirmed", err: null }), _fetchBlockHeight: async () => 990 }
  );

  assert.equal(results.length, 1);
  assert.equal(results[0].outcome, "confirmed");
  const rows = store.getUnresolvedTransactionsForWallet(TEST_ROOT, walletId);
  assert.equal(rows.length, 0); // no longer unresolved
});

test("reconcileWallet: an expired transaction is marked expired, not left stuck", async () => {
  const projectId = store.createProject(TEST_ROOT, { name: "T2", symbol: "TT2", network: "devnet" });
  const walletId = store.addWallet(TEST_ROOT, { projectId, role: "main_holding", label: "Main Holding", address: "Addr2", keypairPath: "/tmp/w2.json" });

  store.recordTransaction(TEST_ROOT, {
    projectId, walletId, walletRole: "main_holding", type: "DISTRIBUTION",
    signature: "SIG_EXPIRED_CASE", lastValidBlockHeight: 1000, network: "devnet", status: "submitted",
  });

  await reconcileWallet(
    TEST_ROOT,
    { network: "devnet", walletId },
    { _fetchStatus: async () => null, _fetchBlockHeight: async () => 1050 } // well past expiry, never landed
  );

  const rows = store.getUnresolvedTransactionsForWallet(TEST_ROOT, walletId);
  assert.equal(rows.length, 0); // expired is resolved, not "unresolved" anymore
});

test("assertWalletClearToTransact: blocks when a transaction is still genuinely ambiguous", async () => {
  const projectId = store.createProject(TEST_ROOT, { name: "T3", symbol: "TT3", network: "devnet" });
  const walletId = store.addWallet(TEST_ROOT, { projectId, role: "main_holding", label: "Main Holding", address: "Addr3", keypairPath: "/tmp/w3.json" });

  store.recordTransaction(TEST_ROOT, {
    projectId, walletId, walletRole: "main_holding", type: "DISTRIBUTION",
    signature: "SIG_STILL_PENDING", lastValidBlockHeight: 1000, network: "devnet", status: "submitted",
  });

  await assert.rejects(
    () => assertWalletClearToTransact(
      TEST_ROOT,
      { network: "devnet", walletId },
      { _fetchStatus: async () => null, _fetchBlockHeight: async () => 990 } // not found, but not expired either
    ),
    (err) => /couldn't be confirmed/.test(err.message)
  );
});

test("assertWalletClearToTransact: passes cleanly once reconciliation resolves everything (no force needed)", async () => {
  const projectId = store.createProject(TEST_ROOT, { name: "T4", symbol: "TT4", network: "devnet" });
  const walletId = store.addWallet(TEST_ROOT, { projectId, role: "main_holding", label: "Main Holding", address: "Addr4", keypairPath: "/tmp/w4.json" });

  store.recordTransaction(TEST_ROOT, {
    projectId, walletId, walletRole: "main_holding", type: "DISTRIBUTION",
    signature: "SIG_RESOLVES_CLEAN", lastValidBlockHeight: 1000, network: "devnet", status: "submitted",
  });

  // Should not throw -- reconciliation resolves it to confirmed on its own.
  await assertWalletClearToTransact(
    TEST_ROOT,
    { network: "devnet", walletId },
    { _fetchStatus: async () => ({ confirmationStatus: "finalized", err: null }), _fetchBlockHeight: async () => 995 }
  );
});
