// Marketing content: template-based draft generation (grounded only in real,
// already-recorded project data -- never a fabricated stat), and posting to
// X once a human has approved a draft. Nothing here ever auto-posts; publish
// is always a separate, explicit call after approve.
//
// Guardrails baked into every template, not left to trust:
//   - no promised/implied returns ("moon", "10x", price targets)
//   - no holder/volume/liquidity numbers we don't actually track and verify
//   - no invented partnerships, endorsements, or exchange listings
//   - operator wallets are never described as independent holders/investors
// If you edit a draft before approving, that's on you -- this file's job is
// just to not hand you a fabrication to start from.

import crypto from "crypto";
import { loadCredentials } from "./socialsCredentials.js";

export class SocialsError extends Error {}

function explorerUrl(mintAddress, network) {
  const cluster = network === "mainnet-beta" ? "" : `?cluster=${network}`;
  return `https://solscan.io/token/${mintAddress}${cluster}`;
}

function raydiumSwapUrl(mintAddress) {
  return `https://raydium.io/swap/?outputMint=${mintAddress}`;
}

const TEMPLATES = {
  mint_live: {
    label: "Token live",
    build(project) {
      if (!project.mint_address) throw new SocialsError("This project hasn't minted yet -- nothing real to announce.");
      const supply = project.supply ? Number(project.supply).toLocaleString() : null;
      return [
        `${project.symbol} (${project.name}) is live on Solana${project.network !== "mainnet-beta" ? ` ${project.network}` : ""}.`,
        supply ? `Fixed supply: ${supply} ${project.symbol}.` : null,
        `Contract: ${project.mint_address}`,
        explorerUrl(project.mint_address, project.network),
        ``,
        `DYOR. Not financial advice.`,
      ].filter(Boolean).join("\n");
    },
  },
  pool_live: {
    label: "Liquidity pool live",
    build(project) {
      if (!project.mint_address) throw new SocialsError("This project hasn't minted yet.");
      if (!project.pool_address) throw new SocialsError("No verified pool yet -- verify one on the Liquidity page first.");
      return [
        `${project.symbol} liquidity is live on Raydium.`,
        `Pool: ${project.pool_address}`,
        `Trade: ${raydiumSwapUrl(project.mint_address)}`,
        ``,
        `DYOR. Not financial advice.`,
      ].join("\n");
    },
  },
  custom: {
    label: "Blank / write your own",
    build(project) {
      return `${project.symbol}: `;
    },
  },
};

export function listTemplates() {
  return Object.entries(TEMPLATES).map(([id, t]) => ({ id, label: t.label }));
}

export function generateContent(project, templateId) {
  const tpl = TEMPLATES[templateId];
  if (!tpl) throw new SocialsError(`Unknown template "${templateId}".`);
  return tpl.build(project);
}

// ---- X (Twitter) API v2, OAuth 1.0a user-context signing ----
// Implemented directly against the HTTP API rather than pulling in an SDK --
// posting a single JSON tweet needs one signed header, not a dependency.

function percentEncode(str) {
  return encodeURIComponent(str).replace(/[!*'()]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
}

function buildOauthHeader({ apiKey, apiSecret, accessToken, accessTokenSecret }, method, url) {
  const oauthParams = {
    oauth_consumer_key: apiKey,
    oauth_nonce: crypto.randomBytes(16).toString("hex"),
    oauth_signature_method: "HMAC-SHA1",
    oauth_timestamp: Math.floor(Date.now() / 1000).toString(),
    oauth_token: accessToken,
    oauth_version: "1.0",
  };
  // POST /2/tweets takes its body as JSON, not signed form params -- the
  // signature base string here is just the oauth params themselves, per
  // OAuth 1.0a + X's own documented behaviour for this endpoint.
  const paramString = Object.keys(oauthParams)
    .sort()
    .map((k) => `${percentEncode(k)}=${percentEncode(oauthParams[k])}`)
    .join("&");
  const baseString = `${method.toUpperCase()}&${percentEncode(url)}&${percentEncode(paramString)}`;
  const signingKey = `${percentEncode(apiSecret)}&${percentEncode(accessTokenSecret)}`;
  const signature = crypto.createHmac("sha1", signingKey).update(baseString).digest("base64");

  const headerParams = { ...oauthParams, oauth_signature: signature };
  return (
    "OAuth " +
    Object.keys(headerParams)
      .sort()
      .map((k) => `${percentEncode(k)}="${percentEncode(headerParams[k])}"`)
      .join(", ")
  );
}

async function postTweet(creds, text) {
  const url = "https://api.twitter.com/2/tweets";
  const authorization = buildOauthHeader(creds, "POST", url);
  const res = await fetch(url, {
    method: "POST",
    headers: { Authorization: authorization, "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data?.detail || data?.title || data?.errors?.[0]?.message || `X API error (${res.status})`;
    throw new SocialsError(detail);
  }
  return data.data; // { id, text }
}

/** Publish an already-approved post. Throws SocialsError for anything the caller should show, not crash on. */
export async function publishToX(root, projectId, { text }) {
  const creds = loadCredentials(root, projectId);
  if (!creds) {
    throw new SocialsError("No X API credentials saved for this project yet -- add them above first.");
  }
  const result = await postTweet(creds, text);
  return {
    remotePostId: result.id,
    remoteUrl: `https://x.com/i/web/status/${result.id}`,
  };
}
