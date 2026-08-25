// Thin wrapper over Jupiter's public swap API. Mainnet only — Jupiter
// routes against real liquidity, which devnet doesn't have, so this is
// never called for a devnet wallet (the console UI disables the Swap tab
// there, and Phase 2's route check short-circuits before ever calling
// Jupiter on devnet).
//
// Verified against Jupiter's own official client, @jup-ag/api v6.0.48
// (published 2026-08-03 — three weeks old at the time this was checked),
// by installing it via npm (registry access works from this sandbox even
// though calling Jupiter's actual API directly does not) and reading its
// generated source. Confirmed current, not guessed:
//   - Base URL is still https://quote-api.jup.ag/v6 (the free, unauthenticated
//     tier) — this is literally BASE_PATH in their own SDK's runtime.ts,
//     not a deprecated legacy endpoint. lite-api.jup.ag/api.jup.ag exist as
//     paid-tier options for higher rate limits, not replacements.
//   - Paths /quote and /swap, and the QuoteResponse/SwapRequest/SwapResponse
//     field names below, match their published TypeScript models exactly.
//   - SwapResponse includes `lastValidBlockHeight` directly -- this is what
//     lets buildSwapTransaction hand chainTx.js's submitAndTrack the real
//     expiry height instead of us having to guess or fetch a fresh one
//     (which would be wrong here -- see buildSwapTransaction's doc comment).
// If Jupiter moves these endpoints again after this check, re-running the
// same "npm install @jup-ag/api, read dist/runtime.ts's BASE_PATH" check
// is faster and more reliable than guessing from documentation.

export const JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote";
export const JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap";
export const MAINNET_RPC = process.env.MAINNET_RPC_URL || "https://api.mainnet-beta.solana.com";

export async function getSwapQuote({ inputMint, outputMint, amount, slippageBps = 100 }) {
  const url = new URL(JUPITER_QUOTE_URL);
  url.searchParams.set("inputMint", inputMint);
  url.searchParams.set("outputMint", outputMint);
  url.searchParams.set("amount", String(amount));
  url.searchParams.set("slippageBps", String(slippageBps));

  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Jupiter quote failed (${res.status}): ${body || res.statusText}`);
  }
  return res.json();
}

/**
 * Ask Jupiter to build the swap transaction for a given quote, and return
 * it UNSIGNED along with its real expiry height -- signing/sending is
 * chainTx.js's job (submitAndTrack), so this transaction can go through
 * the same persist-before-broadcast/reconcile pipeline as every other
 * write in this app, instead of Swap being a special case with its own
 * separate sign+send+confirm logic.
 *
 * Deliberately does NOT set message.recentBlockhash on the returned
 * transaction, and the caller must not either -- Jupiter bakes the
 * blockhash its quote/route was priced against directly into the
 * transaction it returns. Overwriting it would decouple the transaction
 * from the price/route it was actually built for. lastValidBlockHeight
 * (Jupiter's own value, not one we compute) is what submitAndTrack should
 * use for expiry tracking on this specific transaction.
 */
export async function buildSwapTransaction({ userPublicKey, quoteResponse }) {
  const swapRes = await fetch(JUPITER_SWAP_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      quoteResponse,
      userPublicKey,
      wrapAndUnwrapSol: true,
      dynamicComputeUnitLimit: true,
      prioritizationFeeLamports: "auto",
    }),
  });
  if (!swapRes.ok) {
    const body = await swapRes.text().catch(() => "");
    throw new Error(`Jupiter swap-transaction request failed (${swapRes.status}): ${body || swapRes.statusText}`);
  }
  const { swapTransaction, lastValidBlockHeight } = await swapRes.json();
  if (!swapTransaction) throw new Error("Jupiter did not return a swap transaction.");
  if (lastValidBlockHeight == null) throw new Error("Jupiter did not return lastValidBlockHeight — cannot track this transaction's expiry safely.");

  return { swapTransactionBase64: swapTransaction, lastValidBlockHeight };
}
