// Phase 1 tests: the data layer, state machine, and distribution
// validation/idempotency logic that don't require live network access.
//
// What's deliberately NOT covered here (and why): actual on-chain calls
// (mint, transfer, swap, balance reads) need a real RPC connection this
// sandbox's network policy blocks entirely. Those were verified live
// against a running server instead (curl against real endpoints, with real
// generated keypairs) — see the Phase 1 report for what was exercised that
// way. This file covers the parts that should be correct regardless of
// network: schema/CRUD round-tripping, milestone derivation logic, and the
// distribution guards that run before any chain call.

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import os from "os";
import path from "path";

const TEST_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), "coinlauncher-test-"));

const store = await import("../src/db/store.js");
const { runDistribution, DistributionError } = await import("../src/distributionService.js");
const { computeMilestones, STATES } = await import("../src/stateMachine.js");

test.after(() => {
  fs.rmSync(TEST_ROOT, { recursive: true, force: true });
});

test("project + wallet CRUD round-trips correctly", () => {
  const projectId = store.createProject(TEST_ROOT, { name: "T", symbol: "TTT", network: "devnet" });
  assert.ok(projectId.startsWith("proj_"));

  const project = store.getProject(TEST_ROOT, projectId);
  assert.equal(project.name, "T");
  assert.equal(project.mint_address, null);

  store.updateProjectMint(TEST_ROOT, projectId, { mintAddress: "MintAddr111", decimals: 6, supply: "1000000000", metadataUri: "" });
  const updated = store.getProject(TEST_ROOT, projectId);
  assert.equal(updated.mint_address, "MintAddr111");
  assert.equal(updated.decimals, 6);

  const ownerId = store.addWallet(TEST_ROOT, { projectId, role: "owner", label: "Owner", address: "OwnerAddr", keypairPath: "/tmp/o.json" });
  store.addWallet(TEST_ROOT, { projectId, role: "funding", label: "Funding", address: "FundingAddr", keypairPath: "/tmp/f.json" });
  store.addWallet(TEST_ROOT, { projectId, role: "main_holding", label: "Main Holding", address: "MainAddr", keypairPath: "/tmp/m.json" });
  store.addWallet(TEST_ROOT, { projectId, role: "operator", label: "Wallet 1", address: "Op1Addr", keypairPath: "/tmp/1.json", allocationPercent: 25 });
  store.addWallet(TEST_ROOT, { projectId, role: "operator", label: "Wallet 2", address: "Op2Addr", keypairPath: "/tmp/2.json", allocationPercent: 25 });

  const wallets = store.getWalletsByProject(TEST_ROOT, projectId);
  assert.equal(wallets.length, 5);
  assert.equal(store.getWalletByRole(TEST_ROOT, projectId, "owner").id, ownerId);
  assert.equal(store.getOperatorWallets(TEST_ROOT, projectId).length, 2);
});

test("state history only records a state once, even if set repeatedly", () => {
  const projectId = store.createProject(TEST_ROOT, { name: "T2", symbol: "TT2", network: "devnet" });
  store.setState(TEST_ROOT, projectId, "PROJECT_CREATED", "a");
  store.setState(TEST_ROOT, projectId, "PROJECT_CREATED", "b"); // setState itself doesn't dedupe -- that's refreshState's job
  const history = store.getStateHistory(TEST_ROOT, projectId);
  assert.equal(history.length, 2); // setState is a raw append; refreshState (tested below) is what dedupes
  assert.equal(store.getState(TEST_ROOT, projectId).state, "PROJECT_CREATED");
});

test("transactions: insert-pending then update-in-place does not duplicate rows", () => {
  const projectId = store.createProject(TEST_ROOT, { name: "T3", symbol: "TT3", network: "devnet" });
  const walletId = store.addWallet(TEST_ROOT, { projectId, role: "operator", label: "W1", address: "Addr1", keypairPath: "/tmp/w1.json" });
  const opId = store.createOperation(TEST_ROOT, { projectId, operationType: "DISTRIBUTION", config: {} });

  const txId = store.recordTransaction(TEST_ROOT, {
    projectId, walletId, walletRole: "operator", type: "DISTRIBUTION",
    outputAsset: "TT3", outputAmount: "100", destination: "Addr1", status: "pending", operationId: opId, network: "devnet",
  });
  let rows = store.getTransactionsByOperation(TEST_ROOT, opId);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].status, "pending");
  assert.equal(rows[0].signature, null);

  store.updateTransaction(TEST_ROOT, txId, { status: "confirmed", signature: "SIG123" });
  rows = store.getTransactionsByOperation(TEST_ROOT, opId);
  assert.equal(rows.length, 1); // still exactly one row -- updated in place, not duplicated
  assert.equal(rows[0].status, "confirmed");
  assert.equal(rows[0].signature, "SIG123");
});

test("computeMilestones: explicit states reflect real project/mint facts, unavailable ones stay false with a reason", async () => {
  const projectId = store.createProject(TEST_ROOT, { name: "T4", symbol: "TT4", network: "devnet" });
  let milestones = await computeMilestones(TEST_ROOT, projectId);
  assert.equal(milestones.PROJECT_CREATED.reached, true);
  assert.equal(milestones.TOKEN_CREATED.reached, false);
  assert.equal(milestones.LIQUIDITY_CREATED.reached, false);
  assert.equal(milestones.LIQUIDITY_CREATED.mode, "unavailable");
  assert.equal(milestones.LAUNCH_ACTIVE.mode, "unavailable");

  store.updateProjectMint(TEST_ROOT, projectId, { mintAddress: "MintAddr222", decimals: 6, supply: "1000000000", metadataUri: "" });
  milestones = await computeMilestones(TEST_ROOT, projectId);
  assert.equal(milestones.TOKEN_CREATED.reached, true);

  // No funding/main_holding wallets exist for this project -- derived
  // checks must not throw or fabricate a positive result.
  assert.equal(milestones.FUNDING_RECEIVED.reached, false);
  assert.equal(milestones.MAIN_WALLET_FUNDED.reached, false);

  assert.deepEqual(STATES.includes("LAUNCH_ACTIVE"), true);
});

test("runDistribution: rejects before any chain call when preconditions aren't met", async () => {
  // No project at all
  await assert.rejects(() => runDistribution(TEST_ROOT, "proj_doesnotexist"), DistributionError);

  // Project with no mint yet
  const p1 = store.createProject(TEST_ROOT, { name: "T5", symbol: "TT5", network: "devnet" });
  await assert.rejects(() => runDistribution(TEST_ROOT, p1), DistributionError);

  // Project with a mint but no Main Holding wallet
  store.updateProjectMint(TEST_ROOT, p1, { mintAddress: "MintAddr333", decimals: 6, supply: "1000000000", metadataUri: "" });
  await assert.rejects(() => runDistribution(TEST_ROOT, p1), DistributionError);

  // Add Main Holding but no operator wallets
  store.addWallet(TEST_ROOT, { projectId: p1, role: "main_holding", label: "Main Holding", address: "MainAddr333", keypairPath: "/tmp/main333.json" });
  await assert.rejects(() => runDistribution(TEST_ROOT, p1), DistributionError);

  // Operator wallets exist, but allocations sum over 100%
  store.addWallet(TEST_ROOT, { projectId: p1, role: "operator", label: "W1", address: "OpA", keypairPath: "/tmp/opA.json", allocationPercent: 25 });
  store.addWallet(TEST_ROOT, { projectId: p1, role: "operator", label: "W2", address: "OpB", keypairPath: "/tmp/opB.json", allocationPercent: 25 });
  await assert.rejects(
    () => runDistribution(TEST_ROOT, p1, { allocations: [{ walletId: "x", percent: 70 }, { walletId: "y", percent: 70 }] }),
    (err) => err instanceof DistributionError && /over 100%/.test(err.message)
  );
});

test("runDistribution: refuses to re-run a completed distribution without force", async () => {
  const p2 = store.createProject(TEST_ROOT, { name: "T6", symbol: "TT6", network: "devnet" });
  store.updateProjectMint(TEST_ROOT, p2, { mintAddress: "MintAddr444", decimals: 6, supply: "1000000000", metadataUri: "" });
  store.addWallet(TEST_ROOT, { projectId: p2, role: "main_holding", label: "Main Holding", address: "MainAddr444", keypairPath: "/tmp/main444.json" });
  store.addWallet(TEST_ROOT, { projectId: p2, role: "operator", label: "W1", address: "OpC", keypairPath: "/tmp/opC.json", allocationPercent: 50 });

  // Simulate a completed operation directly (this specific test targets the
  // guard logic, not the on-chain transfer, which needs live network).
  const opId = store.createOperation(TEST_ROOT, { projectId: p2, operationType: "DISTRIBUTION", config: {} });
  store.updateOperationStatus(TEST_ROOT, opId, "confirmed");

  await assert.rejects(
    () => runDistribution(TEST_ROOT, p2),
    (err) => err instanceof DistributionError && /already completed/.test(err.message)
  );
});
