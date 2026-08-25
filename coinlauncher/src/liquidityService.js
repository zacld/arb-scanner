// Liquidity milestones: LIQUIDITY_CREATED (does a real pool exist for this
// mint) and ROUTE_CONFIRMED (does Jupiter currently return a live quote
// for it). Both are pure observations -- MILESTONE kind per
// stateMachine.js's STATE_KIND, never side-effecting, safe to recompute
// anywhere. No pool is ever created here -- that stays on Raydium's own
// audited UI, same call as Phase 1. This module only verifies.

import { Connection, PublicKey } from "@solana/web3.js";
import { RPC_ENDPOINTS } from "./createToken.js";
import { getSwapQuote } from "./jupiter.js";

// Verified 2026-08-25 against @raydium-io/raydium-sdk-v2 (published
// 2026-08-24 -- one day old at the time of this check), by installing it
// via npm (registry access works here even though calling Raydium's/
// Jupiter's actual APIs does not) and reading the real values out of
// lib/common/programId.mjs, rather than trusting training data. If pool
// verification starts rejecting real pools, re-running that same check is
// the first thing to try before assuming these are stale.
const DEX_PROGRAM_IDS = {
  "mainnet-beta": {
    AMM_V4: "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    AMM_STABLE: "5quBtoiQqxF9Jv6KYKctB59NT3gtJD2Y65kdnB1Uev3h",
    CLMM: "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",
    CPMM: "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",
  },
  devnet: {
    AMM_V4: "DRaya7Kj3aMWQSy19kSjvmuwq9docCHofyP9kanQGaav",
    AMM_STABLE: "DRayDdXc1NZQ9C3hRWmoSf8zK4iapgMnjdNZWrfwsP8m",
    CLMM: "DRayAUgENGQBKVaX8owNhgzkEDyoHTGVEGHVJT1E9pfH",
    CPMM: "DRaycpLY18LhpbydsBWbVJtxpNv9oXPgjRSfpF2bWpYb",
  },
};

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const ROUTE_CHECK_PROBE_LAMPORTS = 10_000_000; // 0.01 SOL — small, representative, cheap to quote

/**
 * Check that a pool address is a real, on-chain Raydium pool account.
 * Deliberately shallow: existence + program ownership, not full reserve
 * parsing (fragile/version-sensitive against a program's internal binary
 * layout). ROUTE_CONFIRMED below is the actual functional gate on whether
 * a swap can execute; this just rules out a typo'd or unrelated address.
 */
export async function verifyPoolAddress(network, poolAddress) {
  const connection = new Connection(RPC_ENDPOINTS[network], "confirmed");
  let pubkey;
  try {
    pubkey = new PublicKey(poolAddress);
  } catch {
    return { verified: false, reason: "Not a valid Solana address." };
  }

  const accountInfo = await connection.getAccountInfo(pubkey);
  if (!accountInfo) {
    return { verified: false, reason: "No account found at this address on " + network + "." };
  }

  const owner = accountInfo.owner.toBase58();
  const knownIds = DEX_PROGRAM_IDS[network] || {};
  const matchedProgram = Object.entries(knownIds).find(([, id]) => id === owner)?.[0];

  if (!matchedProgram) {
    return {
      verified: false,
      reason: `Account exists but isn't owned by a recognized Raydium program (owner: ${owner}). ` +
        `Double check the pool address, or this may be a different DEX/version not yet recognized here.`,
    };
  }

  return { verified: true, program: matchedProgram };
}

// Route-check results are cached briefly so the Overview/Liquidity pages
// polling state on load don't hammer Jupiter's public endpoint.
const routeCheckCache = new Map(); // mintAddress -> { result, expiresAt }
const ROUTE_CHECK_TTL_MS = 45_000;

/**
 * Ask Jupiter for a real quote on a small probe amount. A route existing
 * proves tradeable liquidity right now -- this is the actual functional
 * gate, independent of which DEX or how many hops it goes through.
 * Mainnet only: Jupiter has no devnet equivalent at all, so devnet
 * short-circuits without ever calling their API.
 */
export async function checkRoute(network, mintAddress) {
  if (network !== "mainnet-beta") {
    return { available: false, reason: "Jupiter has no devnet equivalent — route checks only run on mainnet." };
  }

  const cached = routeCheckCache.get(mintAddress);
  if (cached && cached.expiresAt > Date.now()) return cached.result;

  let result;
  try {
    const quote = await getSwapQuote({
      inputMint: WRAPPED_SOL_MINT,
      outputMint: mintAddress,
      amount: ROUTE_CHECK_PROBE_LAMPORTS,
      slippageBps: 100,
    });
    result =
      quote && quote.outAmount && Number(quote.outAmount) > 0
        ? { available: true, quote }
        : { available: false, reason: "Jupiter returned no usable route for this mint." };
  } catch (err) {
    result = { available: false, reason: err.message || String(err) };
  }

  routeCheckCache.set(mintAddress, { result, expiresAt: Date.now() + ROUTE_CHECK_TTL_MS });
  return result;
}
