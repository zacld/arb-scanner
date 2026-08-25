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
  TOKEN_CREATED: "milestone", // NOTE: the mint transaction itself is a real write not yet migrated onto chainTx.js -- see Phase 2 notes.
  LIQUIDITY_PENDING: "milestone",
  LIQUIDITY_CREATED: "milestone",
  ROUTE_CONFIRMED: "milestone",
  FUNDING_RECEIVED: "milestone",
  FUNDING_SWAP_PENDING: "operation", // Phase 2: the funding wallet's SOL->TOKEN swap
  FUNDING_SWAP_COMPLETE: "operation",
  MAIN_WALLET_FUNDED: "milestone",
  DISTRIBUTION_PENDING: "operation", // implemented -- distributionService.js
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

  milestones.LIQUIDITY_PENDING = { reached: false, mode: "unavailable", note: "Liquidity creation lands in Phase 2." };
  milestones.LIQUIDITY_CREATED = { reached: false, mode: "unavailable", note: "Requires manual pool creation + verification (Phase 2)." };
  milestones.ROUTE_CONFIRMED = { reached: false, mode: "unavailable", note: "Requires a live router quote (Phase 2)." };

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

  milestones.FUNDING_SWAP_PENDING = { reached: false, mode: "unavailable", note: "Real market swap lands in Phase 2." };
  milestones.FUNDING_SWAP_COMPLETE = { reached: false, mode: "unavailable", note: "Real market swap lands in Phase 2." };

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

  milestones.LAUNCH_ACTIVE = { reached: false, mode: "unavailable", note: "Requires real liquidity + route (Phase 2)." };

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
