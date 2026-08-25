// Uploads a thumbnail image + generated metadata JSON to Irys — permanent,
// content-addressed storage, paid for directly out of the coin's own wallet.
// No separate account or API key to set up, consistent with the rest of this
// tool: point it at a wallet, it handles the rest.
//
// Devnet uploads are for testing the flow only (cheap/free, may not be
// permanently retrievable). For a thumbnail that actually shows up in
// wallets/explorers long-term, do the real upload on mainnet.

import fs from "fs";
import path from "path";
import { Uploader } from "@irys/upload";
import { Solana } from "@irys/upload-solana";

const CONTENT_TYPES = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
};

async function getIrysUploader(keypair, network, rpcUrl) {
  let builder = Uploader(Solana).withWallet(Array.from(keypair.secretKey));
  if (rpcUrl) builder = builder.withRpc(rpcUrl);
  builder = network === "mainnet-beta" ? builder.mainnet() : builder.devnet();
  return builder;
}

async function fundForBytes(irys, sizeBytes) {
  const price = await irys.getPrice(sizeBytes);
  const buffered = price.multipliedBy(1.1).integerValue();
  await irys.fund(buffered);
}

/** Upload one image file. Returns its permanent URI. */
export async function uploadImage({ keypair, network, rpcUrl, imagePath }) {
  const irys = await getIrysUploader(keypair, network, rpcUrl);
  const ext = path.extname(imagePath).toLowerCase();
  const contentType = CONTENT_TYPES[ext] || "application/octet-stream";
  const size = fs.statSync(imagePath).size;
  await fundForBytes(irys, size);
  const receipt = await irys.uploadFile(imagePath, {
    tags: [{ name: "Content-Type", value: contentType }],
  });
  return `https://gateway.irys.xyz/${receipt.id}`;
}

/** Build and upload the standard token metadata JSON. Returns its URI. */
export async function uploadMetadataJson({
  keypair,
  network,
  rpcUrl,
  name,
  symbol,
  description,
  imageUri,
  twitterUrl,
}) {
  const irys = await getIrysUploader(keypair, network, rpcUrl);
  const metadata = {
    name,
    symbol,
    description: description || "",
    image: imageUri || "",
  };
  if (twitterUrl) {
    metadata.extensions = { twitter: twitterUrl };
  }
  const json = JSON.stringify(metadata);
  await fundForBytes(irys, Buffer.byteLength(json));
  const receipt = await irys.upload(json, {
    tags: [{ name: "Content-Type", value: "application/json" }],
  });
  return `https://gateway.irys.xyz/${receipt.id}`;
}

/**
 * Convenience: upload the thumbnail (if any), then the metadata JSON that
 * points to it, in one call. Returns the metadata URI to feed into the mint.
 */
export async function buildAndUploadMetadata({
  keypair,
  network,
  rpcUrl,
  name,
  symbol,
  description,
  twitterUrl,
  imagePath,
}) {
  let imageUri = "";
  if (imagePath) {
    imageUri = await uploadImage({ keypair, network, rpcUrl, imagePath });
  }
  return uploadMetadataJson({ keypair, network, rpcUrl, name, symbol, description, imageUri, twitterUrl });
}
