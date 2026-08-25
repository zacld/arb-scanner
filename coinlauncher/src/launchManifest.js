// Records what got created for one coin launch: the mint, the owner wallet,
// and any operator wallets it was distributed to. This is how the console
// (public/console.html) knows which wallets belong to which coin. Purely
// local bookkeeping — never touches the network itself.

import fs from "fs";
import path from "path";

export function launchesDir(root) {
  return path.join(root, "launches");
}

export function newLaunchId(symbol) {
  const slug = (symbol || "coin").toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 12) || "COIN";
  return `${slug}-${Date.now()}`;
}

export function launchDir(root, launchId) {
  return path.join(launchesDir(root), launchId);
}

export function saveLaunchManifest(root, launchId, manifest) {
  const dir = launchDir(root, launchId);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify(manifest, null, 2));
  return path.join(dir, "manifest.json");
}

export function loadLaunchManifest(root, launchId) {
  const file = path.join(launchDir(root, launchId), "manifest.json");
  if (!fs.existsSync(file)) return null;
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

/** List all launch manifests, newest first. */
export function listLaunches(root) {
  const dir = launchesDir(root);
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => {
      const manifest = loadLaunchManifest(root, d.name);
      return manifest ? { id: d.name, ...manifest } : null;
    })
    .filter(Boolean)
    .sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
}

export function findWalletInLaunch(manifest, walletId) {
  if (walletId === "owner") return { role: "owner", ...manifest.ownerWallet };
  return (manifest.operatorWallets || []).find((w) => w.id === walletId);
}
