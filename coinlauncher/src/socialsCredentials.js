// X/Twitter API credentials, stored the same way private keys are: a
// chmod-600 file on disk, referenced by project id, never written into
// SQLite and never echoed back in full over the API. These are YOUR
// developer-app credentials from YOUR own X account -- this tool never
// creates the account, never runs OAuth on your behalf, and never works
// around CAPTCHA/email/phone verification. You generate the app and keys
// on developer.x.com yourself; this just stores and uses them locally to
// sign requests.

import fs from "fs";
import path from "path";

function credentialsDir(root) {
  return path.join(root, "secrets", "social");
}

function credentialsPath(root, projectId) {
  return path.join(credentialsDir(root), `${projectId}.json`);
}

const REQUIRED_FIELDS = ["apiKey", "apiSecret", "accessToken", "accessTokenSecret"];

export function saveCredentials(root, projectId, creds) {
  for (const field of REQUIRED_FIELDS) {
    if (!creds || !creds[field] || typeof creds[field] !== "string" || !creds[field].trim()) {
      throw new Error(`Missing required credential field: ${field}.`);
    }
  }
  fs.mkdirSync(credentialsDir(root), { recursive: true });
  const payload = {
    apiKey: creds.apiKey.trim(),
    apiSecret: creds.apiSecret.trim(),
    accessToken: creds.accessToken.trim(),
    accessTokenSecret: creds.accessTokenSecret.trim(),
    savedAt: new Date().toISOString(),
  };
  fs.writeFileSync(credentialsPath(root, projectId), JSON.stringify(payload), { mode: 0o600 });
}

export function loadCredentials(root, projectId) {
  const p = credentialsPath(root, projectId);
  if (!fs.existsSync(p)) return null;
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

export function deleteCredentials(root, projectId) {
  const p = credentialsPath(root, projectId);
  if (fs.existsSync(p)) fs.unlinkSync(p);
}

/** Safe-to-return status: never includes the secret values themselves. */
export function credentialsStatus(root, projectId) {
  const creds = loadCredentials(root, projectId);
  if (!creds) return { configured: false };
  return {
    configured: true,
    apiKeyPreview: maskKey(creds.apiKey),
    savedAt: creds.savedAt,
  };
}

function maskKey(key) {
  if (!key || key.length < 6) return "••••";
  return `${key.slice(0, 4)}…${key.slice(-4)}`;
}
