// The 12-state launch model. Phase 1 implements this as a checklist of
// independently-derivable/action-driven milestones rather than a rigid
// single-pointer FSM that only advances strictly in order -- see the
// reasoning in the Phase 1 report (README / commit message) for why. Short
// version: some milestones are genuinely *observed from live chain state*
// (funding received, main wallet funded) rather than *driven by a pipeline
// action*, consistent with the "balances always reflect actual on-chain
// reality" principle. This lets Distribution be built, hardened, and
// actually tested now without requiring Phase 2's liquidity/swap work to
// artificially unlock it. Flagged explicitly as a judgment call.

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
