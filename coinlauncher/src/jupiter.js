// Thin wrapper over Jupiter's public swap API (https://station.jup.ag/docs/apis/swap-api).
// Mainnet only — Jupiter routes against real liquidity, which devnet doesn't
// have, so this is never called for a devnet wallet (the console UI disables
// the Swap tab there).
//
// NOTE: this could not be exercised against the live Jupiter API from the
// sandbox this was built in (outbound network is blocked there entirely).
// The v6 quote/swap REST shape has been stable for a long time, but if
// Jupiter has since moved these endpoints, the fix is almost certainly just
// updating JUPITER_QUOTE_URL / JUPITER_SWAP_URL below — report the exact
// error back and it can be patched quickly.

import { Connection, VersionedTransaction } from "@solana/web3.js";

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

/** Get quote + execute the swap, signed and sent from `keypair`. Returns the tx signature. */
export async function executeSwap({ keypair, quoteResponse }) {
  const swapRes = await fetch(JUPITER_SWAP_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      quoteResponse,
      userPublicKey: keypair.publicKey.toBase58(),
      wrapAndUnwrapSol: true,
      dynamicComputeUnitLimit: true,
      prioritizationFeeLamports: "auto",
    }),
  });
  if (!swapRes.ok) {
    const body = await swapRes.text().catch(() => "");
    throw new Error(`Jupiter swap-transaction request failed (${swapRes.status}): ${body || swapRes.statusText}`);
  }
  const { swapTransaction } = await swapRes.json();
  if (!swapTransaction) throw new Error("Jupiter did not return a swap transaction.");

  const tx = VersionedTransaction.deserialize(Buffer.from(swapTransaction, "base64"));
  tx.sign([keypair]);

  const connection = new Connection(MAINNET_RPC, "confirmed");
  const signature = await connection.sendTransaction(tx, { skipPreflight: false, maxRetries: 3 });
  const latestBlockhash = await connection.getLatestBlockhash();
  await connection.confirmTransaction({ signature, ...latestBlockhash }, "confirmed");
  return signature;
}
