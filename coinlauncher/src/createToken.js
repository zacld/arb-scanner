// Core token-launch logic. One owner (whoever holds `keypairPath`), one mint,
// no hidden wallets, no coordinated buyers. This module only ever does three
// on-chain things: create the mint, attach Metaplex metadata, mint the fixed
// supply to the owner's own wallet. Nothing here touches any wallet but the
// one the caller provides.

import fs from "fs";
import { Keypair, Connection, PublicKey } from "@solana/web3.js";
import { AuthorityType, createSetAuthorityInstruction } from "@solana/spl-token";
import { createUmi } from "@metaplex-foundation/umi-bundle-defaults";
import {
  keypairIdentity as umiKeypairIdentity,
  generateSigner,
  percentAmount,
  createSignerFromKeypair,
  transactionBuilder,
} from "@metaplex-foundation/umi";
import { createFungible, mintV1, TokenStandard } from "@metaplex-foundation/mpl-token-metadata";
import {
  fromWeb3JsKeypair,
  toWeb3JsPublicKey,
} from "@metaplex-foundation/umi-web3js-adapters";

export const RPC_ENDPOINTS = {
  devnet: "https://api.devnet.solana.com",
  "mainnet-beta": process.env.MAINNET_RPC_URL || "https://api.mainnet-beta.solana.com",
};

/** Load a standard `solana-keygen`-format JSON keypair file. Never leaves this process. */
export function loadOwnerKeypair(keypairPath) {
  const raw = fs.readFileSync(keypairPath, "utf8");
  const secret = Uint8Array.from(JSON.parse(raw));
  return Keypair.fromSecretKey(secret);
}

/**
 * Rough SOL cost estimate shown to the user before they confirm a mainnet
 * launch. Not exact (network fees vary), just enough to not surprise anyone.
 */
export const ESTIMATED_LAUNCH_COST_SOL = 0.02;

/**
 * Create a fungible SPL token with Metaplex metadata and mint the full
 * fixed supply to the owner's wallet. Optionally revokes mint + freeze
 * authority afterward so supply can never be inflated later — the standard
 * trust signal for a token that isn't going to be rug-pulled via minting.
 *
 * Does not touch liquidity. Does not touch any wallet other than `owner`.
 */
export async function launchToken({
  network,
  keypairPath,
  name,
  symbol,
  uri,
  decimals,
  supply,
  revokeAuthorities = true,
}) {
  if (!RPC_ENDPOINTS[network]) {
    throw new Error(`Unknown network "${network}". Use "devnet" or "mainnet-beta".`);
  }
  if (!name || !symbol || !supply) {
    throw new Error("name, symbol, and supply are required.");
  }

  const rpcUrl = RPC_ENDPOINTS[network];
  const owner = loadOwnerKeypair(keypairPath);

  const umi = createUmi(rpcUrl);
  const umiOwnerKeypair = fromWeb3JsKeypair(owner);
  const ownerSigner = createSignerFromKeypair(umi, umiOwnerKeypair);
  umi.use(umiKeypairIdentity(ownerSigner));

  const mint = generateSigner(umi);
  const decimalsNum = Number(decimals ?? 6);
  const supplyBaseUnits = BigInt(supply) * BigInt(10 ** decimalsNum);

  let builder = transactionBuilder()
    .add(
      createFungible(umi, {
        mint,
        name,
        symbol,
        uri: uri || "",
        sellerFeeBasisPoints: percentAmount(0),
        decimals: decimalsNum,
      })
    )
    .add(
      mintV1(umi, {
        mint: mint.publicKey,
        authority: ownerSigner,
        amount: supplyBaseUnits,
        tokenOwner: ownerSigner.publicKey,
        tokenStandard: TokenStandard.Fungible,
      })
    );

  await builder.sendAndConfirm(umi, { confirm: { commitment: "confirmed" } });

  const mintAddress = toWeb3JsPublicKey(mint.publicKey);

  if (revokeAuthorities) {
    await revokeMintAndFreezeAuthority({ rpcUrl, owner, mintAddress });
  }

  return {
    network,
    mintAddress: mintAddress.toBase58(),
    ownerAddress: owner.publicKey.toBase58(),
    decimals: decimalsNum,
    supply: String(supply),
    authoritiesRevoked: revokeAuthorities,
    explorerUrl: solscanUrl(mintAddress.toBase58(), network),
  };
}

async function revokeMintAndFreezeAuthority({ rpcUrl, owner, mintAddress }) {
  const connection = new Connection(rpcUrl, "confirmed");
  const { Transaction, sendAndConfirmTransaction } = await import("@solana/web3.js");

  const tx = new Transaction().add(
    createSetAuthorityInstruction(
      mintAddress,
      owner.publicKey,
      AuthorityType.MintTokens,
      null
    ),
    createSetAuthorityInstruction(
      mintAddress,
      owner.publicKey,
      AuthorityType.FreezeAccount,
      null
    )
  );

  await sendAndConfirmTransaction(connection, tx, [owner]);
}

export function solscanUrl(mintAddress, network) {
  const cluster = network === "devnet" ? "?cluster=devnet" : "";
  return `https://solscan.io/token/${mintAddress}${cluster}`;
}

/** Raydium's own pool-creation UI. We deliberately don't hand-roll LP creation
 * here — that's real, hard-to-reverse fund custody logic best left to
 * Raydium's audited interface, which you drive yourself with your own wallet.
 */
export function raydiumCreatePoolUrl(mintAddress) {
  return `https://raydium.io/liquidity/create-pool/?input=${mintAddress}`;
}
