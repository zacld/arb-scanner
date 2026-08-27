// Phase 2 tests: liquidity/route milestone wiring and funding-swap
// preconditions that don't require live network access. Live network
// paths (verifyPoolAddress's on-chain lookup, checkRoute's Jupiter call,
// the actual swap/forward transactions) were instead verified against a
// running server — see the Phase 2 report for what was exercised that way.

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import os from "os";
import path from "path";

const TEST_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), "coinlauncher-phase2-test-"));

const store = await import("../src/db/store.js");
const { runFundingSwap, runFundingForward, FundingSwapError } = await import("../src/fundingSwapService.js");
const { computeMilestones } = await import("../src/stateMachine.js");
const { runFullLaunchSequence } = await import("../src/launchOrchestrator.js");

test.after(() => {
  fs.rmSync(TEST_ROOT, { recursive: true, force: true });
});

test("liquidity config columns round-trip with sensible defaults", () => {
  const projectId = store.createProject(TEST_ROOT, { name: "L1", symbol: "LQ1", network: "mainnet-beta" });
  const project = store.getProject(TEST_ROOT, projectId);
  assert.equal(project.pool_address, null);
  assert.equal(project.swap_slippage_bps, 100);
  assert.equal(project.funding_reserve_sol, 0.02);
  assert.equal(project.funding_swap_percent, 100);

  store.updateLiquidityConfig(TEST_ROOT, projectId, { slippageBps: 250, reserveSol: 0.05, swapPercent: 80 });
  const updated = store.getProject(TEST_ROOT, projectId);
  assert.equal(updated.swap_slippage_bps, 250);
  assert.equal(updated.funding_reserve_sol, 0.05);
  assert.equal(updated.funding_swap_percent, 80);

  store.setPoolAddress(TEST_ROOT, projectId, "SomePoolAddress111");
  const withPool = store.getProject(TEST_ROOT, projectId);
  assert.equal(withPool.pool_address, "SomePoolAddress111");
  assert.ok(withPool.pool_verified_at);
});

test("computeMilestones: LIQUIDITY_CREATED reflects a verified pool address, not an assumed one", async () => {
  const projectId = store.createProject(TEST_ROOT, { name: "L2", symbol: "LQ2", network: "devnet" });
  store.updateProjectMint(TEST_ROOT, projectId, { mintAddress: "MintAddrL2", decimals: 6, supply: "1000000000", metadataUri: "" });

  let milestones = await computeMilestones(TEST_ROOT, projectId);
  assert.equal(milestones.LIQUIDITY_CREATED.reached, false);
  assert.equal(milestones.LIQUIDITY_PENDING.reached, true); // token exists, no pool yet

  store.setPoolAddress(TEST_ROOT, projectId, "VerifiedPoolAddr");
  milestones = await computeMilestones(TEST_ROOT, projectId);
  assert.equal(milestones.LIQUIDITY_CREATED.reached, true);
  assert.equal(milestones.LIQUIDITY_PENDING.reached, false); // no longer pending once created
});

test("computeMilestones: ROUTE_CONFIRMED short-circuits to false on devnet without ever calling Jupiter", async () => {
  const projectId = store.createProject(TEST_ROOT, { name: "L3", symbol: "LQ3", network: "devnet" });
  store.updateProjectMint(TEST_ROOT, projectId, { mintAddress: "MintAddrL3", decimals: 6, supply: "1000000000", metadataUri: "" });
  const milestones = await computeMilestones(TEST_ROOT, projectId);
  assert.equal(milestones.ROUTE_CONFIRMED.reached, false);
  assert.match(milestones.ROUTE_CONFIRMED.note, /devnet/i);
});

test("runFundingSwap: rejects before any chain call when preconditions aren't met", async () => {
  await assert.rejects(() => runFundingSwap(TEST_ROOT, "proj_doesnotexist"), FundingSwapError);

  const p1 = store.createProject(TEST_ROOT, { name: "F1", symbol: "FS1", network: "mainnet-beta" });
  await assert.rejects(() => runFundingSwap(TEST_ROOT, p1), FundingSwapError); // no mint

  store.updateProjectMint(TEST_ROOT, p1, { mintAddress: "MintAddrF1", decimals: 6, supply: "1000000000", metadataUri: "" });
  await assert.rejects(() => runFundingSwap(TEST_ROOT, p1), FundingSwapError); // no funding wallet

  store.addWallet(TEST_ROOT, { projectId: p1, role: "funding", label: "Funding", address: "FundingAddrF1", keypairPath: "/tmp/f1.json" });
  // Has mint + funding wallet, but network is devnet-incompatible check happens
  // before route check for a devnet project:
  const p2 = store.createProject(TEST_ROOT, { name: "F2", symbol: "FS2", network: "devnet" });
  store.updateProjectMint(TEST_ROOT, p2, { mintAddress: "MintAddrF2", decimals: 6, supply: "1000000000", metadataUri: "" });
  store.addWallet(TEST_ROOT, { projectId: p2, role: "funding", label: "Funding", address: "FundingAddrF2", keypairPath: "/tmp/f2.json" });
  await assert.rejects(
    () => runFundingSwap(TEST_ROOT, p2),
    (err) => err instanceof FundingSwapError && /mainnet/i.test(err.message)
  );
});

test("runFundingForward: rejects before any chain call when preconditions aren't met", async () => {
  await assert.rejects(() => runFundingForward(TEST_ROOT, "proj_doesnotexist"), FundingSwapError);

  const p1 = store.createProject(TEST_ROOT, { name: "F3", symbol: "FS3", network: "devnet" });
  await assert.rejects(() => runFundingForward(TEST_ROOT, p1), FundingSwapError); // no mint

  store.updateProjectMint(TEST_ROOT, p1, { mintAddress: "MintAddrF3", decimals: 6, supply: "1000000000", metadataUri: "" });
  await assert.rejects(() => runFundingForward(TEST_ROOT, p1), FundingSwapError); // no funding wallet

  store.addWallet(TEST_ROOT, { projectId: p1, role: "funding", label: "Funding", address: "FundingAddrF3", keypairPath: "/tmp/f3.json" });
  await assert.rejects(() => runFundingForward(TEST_ROOT, p1), FundingSwapError); // no main_holding wallet
});

test("runFullLaunchSequence: stops cleanly at the first step that isn't ready, never cascades", async () => {
  // A project with no funding wallet at all -- runFundingSwap throws
  // immediately. The sequence must report that and stop, not attempt
  // forward or distribution afterward.
  const p1 = store.createProject(TEST_ROOT, { name: "O1", symbol: "OR1", network: "mainnet-beta" });
  store.updateProjectMint(TEST_ROOT, p1, { mintAddress: "MintAddrO1", decimals: 6, supply: "1000000000", metadataUri: "" });

  const result = await runFullLaunchSequence(TEST_ROOT, p1);
  assert.equal(result.complete, false);
  assert.equal(result.stoppedAt, "funding_swap");
  assert.equal(result.steps.length, 1); // never attempted funding_forward or distribution
  assert.equal(result.steps[0].step, "funding_swap");
  assert.equal(result.steps[0].ok, false);
});
