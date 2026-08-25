// Splits a just-minted supply across a set of operator wallets by
// percentage. Every wallet here is created by this same tool for this same
// launch — this is internal fund organization for one operator, not
// distribution to third parties, and every wallet is clearly labeled as
// operator-controlled everywhere it's shown (console UI, this module's own
// output).

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

// Small SOL top-up so each operator wallet can pay its own transaction fees
// later (rent for the token account is paid by the owner wallet directly).
export const OPERATOR_SOL_TOPUP = 0.01;

/**
 * @param operatorWallets [{ id, label, percent, keypair (web3.js Keypair) }]
 * Returns the same list with `.address` and `.txSignature` filled in.
 */
export async function distributeToOperatorWallets({
  network,
  ownerKeypair,
  mintAddress, // web3.js PublicKey
  decimals,
  totalSupply,
  operatorWallets,
}) {
  const connection = new Connection(RPC_ENDPOINTS[network], "confirmed");

  const ownerAta = await getOrCreateAssociatedTokenAccount(
    connection,
    ownerKeypair,
    mintAddress,
    ownerKeypair.publicKey
  );

  // Each wallet is its own transaction, and one failing (e.g. the owner
  // runs low on SOL partway through) shouldn't erase the wallets that
  // already succeeded — those funds moved for real and need to stay
  // tracked. Collect per-wallet outcomes instead of letting the whole
  // batch throw away completed work.
  const results = [];
  for (const w of operatorWallets) {
    const amountBaseUnits =
      BigInt(Math.floor(Number(totalSupply) * (w.percent / 100))) * BigInt(10 ** decimals);

    try {
      const operatorAtaAddress = await getAssociatedTokenAddress(mintAddress, w.keypair.publicKey);

      const tx = new Transaction().add(
        SystemProgram.transfer({
          fromPubkey: ownerKeypair.publicKey,
          toPubkey: w.keypair.publicKey,
          lamports: Math.round(OPERATOR_SOL_TOPUP * 1e9),
        }),
        createAssociatedTokenAccountInstruction(
          ownerKeypair.publicKey,
          operatorAtaAddress,
          w.keypair.publicKey,
          mintAddress
        ),
        createTransferCheckedInstruction(
          ownerAta.address,
          mintAddress,
          operatorAtaAddress,
          ownerKeypair.publicKey,
          amountBaseUnits,
          decimals
        )
      );

      const sig = await sendAndConfirmTransaction(connection, tx, [ownerKeypair], { commitment: "confirmed" });

      results.push({
        id: w.id,
        label: w.label,
        percent: w.percent,
        address: w.keypair.publicKey.toBase58(),
        path: w.path,
        amount: amountBaseUnits.toString(),
        solTopup: OPERATOR_SOL_TOPUP,
        txSignature: sig,
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
