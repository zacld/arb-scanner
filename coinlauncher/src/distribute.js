// Builds the transaction for one wallet's share of a distribution. This
// module only ever BUILDS -- signing, broadcasting, persisting, and
// confirming are chainTx.js's job (submitAndTrack), which is what actually
// closes the crash-safety gap: knowing the signature before broadcast only
// matters if something else signs and sends it.
//
// Every wallet here is created by and stays under the control of the same
// operator -- this is internal fund organization, not distribution to
// third parties, and every wallet is clearly labeled as operator-
// controlled everywhere it's shown.

import { SystemProgram, Transaction } from "@solana/web3.js";
import {
  getOrCreateAssociatedTokenAccount,
  getAssociatedTokenAddress,
  createAssociatedTokenAccountInstruction,
  createTransferCheckedInstruction,
} from "@solana/spl-token";

// Small SOL top-up so each destination wallet can pay its own transaction
// fees later (rent for the token account is paid by the source wallet).
export const OPERATOR_SOL_TOPUP = 0.01;

/**
 * Build (but do not sign/send) a transaction moving an exact amount
 * (already computed, in base units) of one token from `fromKeypair` to
 * `toPublicKey`, topping up `toPublicKey` with a little SOL if it doesn't
 * already have enough for its own future fees. Only includes the
 * instructions actually still needed (skips ATA creation / SOL topup if
 * already in place from an earlier attempt).
 */
export async function buildShareTransferTransaction({
  connection,
  fromKeypair,
  toPublicKey,
  mintAddress,
  decimals,
  amountBaseUnits,
  solTopup = OPERATOR_SOL_TOPUP,
}) {
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

  return { transaction: instructions.length ? new Transaction().add(...instructions) : null, instructionCount: instructions.length };
}
