// The 12-state launch model. Implemented as a checklist of independently-
// derivable/action-driven milestones rather than a rigid single-pointer
// FSM that only advances strictly in order -- kept deliberately (per
// explicit direction) rather than converted to strict linear ordering.
//
// Formalized here: every state is classified by KIND, independent of
// whether it's implemented yet:
//
//   "milestone" -- a pure OBSERVATION of chain/DB state. No side effects.
//     Always safe to recompute, any number of times, from any process.
//     Never needs an idempotency ledger because checking it can't cause
//     harm. E.g. "does Funding wallet have SOL" is just a balance read.
//
//   "operation" -- an intentional, tracked WRITE. Goes through the
//     operations ledger (db/store.js) and chainTx.js's hardened
//     build->sign->persist->broadcast->poll->update pipeline. Has a
//     PENDING/COMPLETE pair specifically because it's tracking an attempt
//     at doing something, not observing whether something already exists.
//     E.g. DISTRIBUTION_PENDING/COMPLETE track the distribution operation's
//     own lifecycle, not an independently-observable chain condition.
//
// This distinction matters operationally: milestones can be refreshed
// freely from anywhere (a page load, a poll loop) with zero risk.
// Operations must never be triggered opportunistically the same way --
// they're user-initiated, idempotency-guarded, and reconciled against the
// chain before any retry.

import { PublicKey } from "@solana/web3.js";
import { getBalanceSol } from "./wallet.js";
import { getTokenBalanceBaseUnits } from "./distributionService.js";
import { verifyPoolAddress, checkRoute } from "./liquidityService.js";
import * as store from "./db/store.js";

export const STATES = [
  "PROJECT_CREATED",
  "TOKEN_CREATED",
  "LIQUIDITY_PENDING",
  "LIQUIDITY_CREATED",
  "ROUTE_CONFIRMED",
  "FUNDING_RECEIVED",
  "FUNDING_SWAP_PENDING",
  "FUNDING_SWAP_COMPLETE",
  "MAIN_WALLET_FUNDED",
  "DISTRIBUTION_PENDING",
  "DISTRIBUTION_COMPLETE",
  "LAUNCH_ACTIVE",
];

/** Static classification -- milestone vs operation. See module doc above. */
export const STATE_KIND = {
  PROJECT_CREATED: "milestone",
  TOKEN_CREATED: "milestone", // NOTE: the mint transaction itself is a real write not yet migrated onto chainTx.js -- known gap, see report.
  LIQUIDITY_PENDING: "milestone",
  LIQUIDITY_CREATED: "milestone",
  ROUTE_CONFIRMED: "milestone",
  FUNDING_RECEIVED: "milestone",
  FUNDING_SWAP_PENDING: "operation", // fundingSwapService.js
  FUNDING_SWAP_COMPLETE: "operation",
  MAIN_WALLET_FUNDED: "milestone",
  DISTRIBUTION_PENDING: "operation", // distributionService.js
  DISTRIBUTION_COMPLETE: "operation",
  LAUNCH_ACTIVE: "milestone",
};

const FUNDING_RECEIVED_MIN_SOL = 0.001;

/** Recompute every milestone from real facts (DB + live chain balances). Never invents state. */
export async function computeMilestones(root, projectId) {
  const project = store.getProject(root, projectId);
  if (!project) throw new Error("Project not found.");
  const wallets = store.getWalletsByProject(root, projectId);
  const funding = wallets.find((w) => w.role === "funding");
  const mainHolding = wallets.find((w) => w.role === "main_holding");

  const milestones = {};
  milestones.PROJECT_CREATED = { reached: true, mode: "explicit" };
  milestones.TOKEN_CREATED = { reached: !!project.mint_address, mode: "explicit" };

  // LIQUIDITY_CREATED trusts the recorded verification (set by
  // POST .../liquidity/verify-pool once verifyPoolAddress succeeds) rather
  // than re-checking on-chain every refresh -- a verified pool account
  // doesn't stop existing; ROUTE_CONFIRMED below is the one that must stay
  // live/fresh, since tradeability can genuinely change moment to moment.
  const liquidityCreated = !!(project.pool_address && project.pool_verified_at);
  milestones.LIQUIDITY_CREATED = liquidityCreated
    ? { reached: true, mode: "derived", detail: { poolAddress: project.pool_address, verifiedAt: project.pool_verified_at } }
    : { reached: false, mode: "derived", note: "No verified pool address on file yet. Create one on Raydium, then verify it here." };
  milestones.LIQUIDITY_PENDING = { reached: milestones.TOKEN_CREATED.reached && !liquidityCreated, mode: "derived" };

  let routeResult = { available: false, reason: "No mint yet." };
  if (project.mint_address) {
    routeResult = await checkRoute(project.network, project.mint_address);
  }
  milestones.ROUTE_CONFIRMED = {
    reached: routeResult.available,
    mode: "derived",
    detail: routeResult.available ? { quote: { outAmount: routeResult.quote?.outAmount } } : { reason: routeResult.reason },
    note: routeResult.available ? undefined : routeResult.reason,
  };

  let fundingSol = 0;
  let fundingCheckError = null;
  if (funding) {
    try {
      fundingSol = await getBalanceSol(new PublicKey(funding.address), project.network);
    } catch (err) {
      fundingCheckError = err.message || String(err);
    }
  }
  milestones.FUNDING_RECEIVED = {
    reached: fundingSol >= FUNDING_RECEIVED_MIN_SOL,
    mode: "derived",
    detail: { sol: fundingSol, error: fundingCheckError },
  };

  const swapOp = store.findOperation(root, projectId, "FUNDING_SWAP");
  milestones.FUNDING_SWAP_PENDING = { reached: !!swapOp, mode: "action", detail: { operationStatus: swapOp?.status || null } };
  milestones.FUNDING_SWAP_COMPLETE = { reached: swapOp?.status === "confirmed", mode: "action" };

  let mainBalance = 0n;
  let mainCheckError = null;
  if (mainHolding && project.mint_address) {
    try {
      mainBalance = await getTokenBalanceBaseUnits(project.network, new PublicKey(project.mint_address), new PublicKey(mainHolding.address));
    } catch (err) {
      mainCheckError = err.message || String(err);
    }
  }
  milestones.MAIN_WALLET_FUNDED = {
    reached: mainBalance > 0n,
    mode: "derived",
    detail: { balanceBaseUnits: mainBalance.toString(), error: mainCheckError },
  };

  const distOp = store.findOperation(root, projectId, "DISTRIBUTION");
  milestones.DISTRIBUTION_PENDING = { reached: !!distOp, mode: "action", detail: { operationStatus: distOp?.status || null } };
  milestones.DISTRIBUTION_COMPLETE = { reached: distOp?.status === "confirmed", mode: "action" };

  milestones.LAUNCH_ACTIVE = {
    reached: milestones.ROUTE_CONFIRMED.reached && milestones.DISTRIBUTION_COMPLETE.reached,
    mode: "derived",
    note: "Real liquidity exists to trade against AND operator wallets have been funded.",
  };

  for (const state of STATES) milestones[state].kind = STATE_KIND[state];
  return milestones;
}

/** Recompute milestones and record any newly-reached ones to history. Safe/idempotent to call repeatedly. */
export async function refreshState(root, projectId) {
  const milestones = await computeMilestones(root, projectId);
  const history = store.getStateHistory(root, projectId);
  const alreadyRecorded = new Set(history.map((h) => h.state));

  let furthest = "PROJECT_CREATED";
  for (const state of STATES) {
    if (milestones[state].reached) {
      furthest = state;
      if (!alreadyRecorded.has(state)) {
        store.setState(root, projectId, state, `auto-derived (${milestones[state].mode})`);
      }
    }
  }
  return { furthest, milestones };
}

/** Verify and record a pool address for LIQUIDITY_CREATED. Never creates a pool -- only checks one that already exists. */
export async function verifyAndRecordPool(root, projectId, poolAddress) {
  const project = store.getProject(root, projectId);
  if (!project) throw new Error("Project not found.");
  const result = await verifyPoolAddress(project.network, poolAddress);
  if (result.verified) {
    store.setPoolAddress(root, projectId, poolAddress);
  }
  return result;
}
