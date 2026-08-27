// Runs the three already-hardened operations (funding swap -> forward ->
// distribution) as one triggered sequence, instead of clicking through 3
// buttons on 2 pages. Each step is still independently tracked in the
// operations ledger exactly as if triggered separately -- this is
// orchestration over the existing hardened primitives, not a new atomic
// mega-transaction. Stops cleanly and reports exactly where it got to the
// moment any step isn't ready or doesn't confirm; never cascades past a
// step that didn't actually succeed, and never masks a failure as success.

import { runFundingSwap, runFundingForward } from "./fundingSwapService.js";
import { runDistribution } from "./distributionService.js";

export async function runFullLaunchSequence(root, projectId, { swapPercent, slippageBps, allocations, force = false } = {}) {
  const steps = [];

  let swapResult;
  try {
    swapResult = await runFundingSwap(root, projectId, { swapPercent, slippageBps, force });
  } catch (err) {
    steps.push({ step: "funding_swap", ok: false, error: err.message || String(err) });
    return { complete: false, stoppedAt: "funding_swap", steps };
  }
  steps.push({ step: "funding_swap", ok: swapResult.status === "confirmed", ...swapResult });
  if (swapResult.status !== "confirmed") {
    // expired / still-submitted / failed -- don't cascade into forwarding
    // tokens that don't exist yet.
    return { complete: false, stoppedAt: "funding_swap", steps };
  }

  let forwardResult;
  try {
    forwardResult = await runFundingForward(root, projectId, { force });
  } catch (err) {
    steps.push({ step: "funding_forward", ok: false, error: err.message || String(err) });
    return { complete: false, stoppedAt: "funding_forward", steps };
  }
  const forwardOk = forwardResult.status === "confirmed" || forwardResult.skipped;
  steps.push({ step: "funding_forward", ok: forwardOk, ...forwardResult });
  if (!forwardOk) {
    return { complete: false, stoppedAt: "funding_forward", steps };
  }

  let distResult;
  try {
    distResult = await runDistribution(root, projectId, { allocations, force });
  } catch (err) {
    steps.push({ step: "distribution", ok: false, error: err.message || String(err) });
    return { complete: false, stoppedAt: "distribution", steps };
  }
  steps.push({ step: "distribution", ok: distResult.complete, ...distResult });

  return { complete: distResult.complete, stoppedAt: distResult.complete ? null : "distribution", steps };
}
