// Splits token supply across a set of wallets by percentage. Every wallet
// here is created by and stays under the control of the same operator —
// this is internal fund organization, not distribution to third parties,
// and every wallet is clearly labeled as operator-controlled everywhere
// it's shown.
//
// Two layers:
//  - transferShareToWallet: the granular, resumable primitive (one wallet,
//    one transaction). Idempotent-safe: checks what already exists
//    (ATA, SOL balance) before building the transaction, so calling it
//    again after a partial success doesn't re-fund or fail on "account
//    already exists". distributionService.js calls this directly, in its
//    own loop, persisting to the DB after each call — that's what makes
//    a crash mid-distribution recoverable (see distributionService.js).
//  - distributeToOperatorWallets: a convenience batch wrapper over the
//    same primitive, computing amounts from a total-supply figure. Kept
//    for CLI/manual use; the state-machine-driven distribution flow uses
//    transferShareToWallet directly against real observed balances instead
//    (see clarification: percentages apply to actual on-chain balance, not
//    an assumed total).

import {
  Connection,
  SystemProgram,
  Transaction,
  sendAndConfirmTransaction,
} from "@solana/web3.js";
import {
  getOrCreateAssociatedTokenAccount,
  getAssociatedTokenAddress,
  createAssociatedTokenAccountInstruction,
  createTransferCheckedInstruction,
} from "@solana/spl-token";
import { RPC_ENDPOINTS } from "./createToken.js";

// Small SOL top-up so each destination wallet can pay its own transaction
// fees later (rent for the token account is paid by the source wallet).
export const OPERATOR_SOL_TOPUP = 0.01;

/**
 * Transfer an exact amount (already computed, in base units) of one token
 * from `fromKeypair` to `toPublicKey`, topping up `toPublicKey` with a
 * little SOL if it doesn't already have enough for its own future fees.
 * Safe to call again for the same destination — it only adds the
 * instructions that are actually still needed.
 */
export async function transferShareToWallet({
  network,
  fromKeypair,
  toPublicKey,
  mintAddress,
  decimals,
  amountBaseUnits,
  solTopup = OPERATOR_SOL_TOPUP,
}) {
  const connection = new Connection(RPC_ENDPOINTS[network], "confirmed");

  const fromAta = await getOrCreateAssociatedTokenAccount(connection, fromKeypair, mintAddress, fromKeypair.publicKey);
  const toAtaAddress = await getAssociatedTokenAddress(mintAddress, toPublicKey);

  const instructions = [];

  const toAtaInfo = await connection.getAccountInfo(toAtaAddress);
  if (!toAtaInfo) {
    instructions.push(
      createAssociatedTokenAccountInstruction(fromKeypair.publicKey, toAtaAddress, toPublicKey, mintAddress)
    );
  }

  if (solTopup > 0) {
    const toBalance = await connection.getBalance(toPublicKey);
    if (toBalance < Math.round(solTopup * 1e9)) {
      instructions.push(
        SystemProgram.transfer({
          fromPubkey: fromKeypair.publicKey,
          toPubkey: toPublicKey,
          lamports: Math.round(solTopup * 1e9) - toBalance,
        })
      );
    }
  }

  if (amountBaseUnits > 0n) {
    instructions.push(
      createTransferCheckedInstruction(
        fromAta.address,
        mintAddress,
        toAtaAddress,
        fromKeypair.publicKey,
        amountBaseUnits,
        decimals
      )
    );
  }

  if (instructions.length === 0) {
    return { signature: null, skipped: true };
  }

  const tx = new Transaction().add(...instructions);
  const signature = await sendAndConfirmTransaction(connection, tx, [fromKeypair], { commitment: "confirmed" });
  return { signature, skipped: false };
}

/**
 * Convenience batch wrapper: compute each wallet's share from a total
 * supply figure and percent, then call transferShareToWallet for each.
 * Per-wallet failures are collected rather than aborting the batch.
 */
export async function distributeToOperatorWallets({
  network,
  ownerKeypair,
  mintAddress,
  decimals,
  totalSupply,
  operatorWallets, // [{ id, label, percent, keypair, path }]
}) {
  const results = [];
  for (const w of operatorWallets) {
    const amountBaseUnits = BigInt(Math.floor(Number(totalSupply) * (w.percent / 100))) * BigInt(10 ** decimals);
    try {
      const { signature } = await transferShareToWallet({
        network,
        fromKeypair: ownerKeypair,
        toPublicKey: w.keypair.publicKey,
        mintAddress,
        decimals,
        amountBaseUnits,
      });
      results.push({
        id: w.id,
        label: w.label,
        percent: w.percent,
        address: w.keypair.publicKey.toBase58(),
        path: w.path,
        amount: amountBaseUnits.toString(),
        solTopup: OPERATOR_SOL_TOPUP,
        txSignature: signature,
        ok: true,
      });
    } catch (err) {
      results.push({
        id: w.id,
        label: w.label,
        percent: w.percent,
        address: w.keypair.publicKey.toBase58(),
        path: w.path,
        ok: false,
        error: err.message || String(err),
      });
    }
  }
  return results;
}
