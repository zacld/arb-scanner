// Socials tests: template generation guardrails, the draft/approve/publish
// status machine in store.js, and credential file storage/masking. The
// actual X API call (postTweet) isn't exercised here -- it needs real
// credentials and posts for real -- so it's excluded the same way live
// Jupiter/Raydium calls are in phase2.test.js.

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import os from "os";
import path from "path";

const TEST_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), "coinlauncher-socials-test-"));

const store = await import("../src/db/store.js");
const { generateContent, listTemplates, SocialsError } = await import("../src/socialsService.js");
const creds = await import("../src/socialsCredentials.js");

test.after(() => {
  fs.rmSync(TEST_ROOT, { recursive: true, force: true });
});

test("generateContent: mint_live requires a real mint, never fabricates one", () => {
  const projectId = store.createProject(TEST_ROOT, { name: "Marketing Test", symbol: "MKT1", network: "devnet" });
  const project = store.getProject(TEST_ROOT, projectId);
  assert.throws(() => generateContent(project, "mint_live"), SocialsError);

  store.updateProjectMint(TEST_ROOT, projectId, { mintAddress: "MintAddrMKT1", decimals: 6, supply: "1000000000", metadataUri: "" });
  const minted = store.getProject(TEST_ROOT, projectId);
  const content = generateContent(minted, "mint_live");
  assert.match(content, /MintAddrMKT1/);
  assert.match(content, /MKT1/);
  // Guardrail: no promised-returns language ever slips into a template.
  assert.doesNotMatch(content, /moon|guarantee|10x|100x/i);
});

test("generateContent: pool_live requires a verified pool, not just a mint", () => {
  const projectId = store.createProject(TEST_ROOT, { name: "Marketing Test 2", symbol: "MKT2", network: "mainnet-beta" });
  store.updateProjectMint(TEST_ROOT, projectId, { mintAddress: "MintAddrMKT2", decimals: 6, supply: "1000000000", metadataUri: "" });
  const noPool = store.getProject(TEST_ROOT, projectId);
  assert.throws(() => generateContent(noPool, "pool_live"), SocialsError);

  store.setPoolAddress(TEST_ROOT, projectId, "VerifiedPoolMKT2");
  const withPool = store.getProject(TEST_ROOT, projectId);
  const content = generateContent(withPool, "pool_live");
  assert.match(content, /VerifiedPoolMKT2/);
});

test("generateContent: unknown template rejected rather than silently falling back", () => {
  const projectId = store.createProject(TEST_ROOT, { name: "Marketing Test 3", symbol: "MKT3", network: "devnet" });
  const project = store.getProject(TEST_ROOT, projectId);
  assert.throws(() => generateContent(project, "not_a_real_template"), SocialsError);
});

test("listTemplates: exposes ids usable by generateContent", () => {
  const templates = listTemplates();
  assert.ok(templates.length >= 3);
  for (const t of templates) {
    assert.ok(t.id);
    assert.ok(t.label);
  }
});

test("social_posts: draft -> approved -> published status machine, publish never reachable from draft directly", () => {
  const projectId = store.createProject(TEST_ROOT, { name: "Status Test", symbol: "STA1", network: "devnet" });
  const postId = store.createSocialPost(TEST_ROOT, { projectId, template: "custom", content: "hello world" });

  let post = store.getSocialPost(TEST_ROOT, postId);
  assert.equal(post.status, "draft");
  assert.equal(post.approved_at, null);
  assert.equal(post.published_at, null);

  store.updateSocialPostContent(TEST_ROOT, postId, "edited content");
  post = store.getSocialPost(TEST_ROOT, postId);
  assert.equal(post.content, "edited content");

  store.setSocialPostStatus(TEST_ROOT, postId, "approved");
  post = store.getSocialPost(TEST_ROOT, postId);
  assert.equal(post.status, "approved");
  assert.ok(post.approved_at);
  assert.equal(post.published_at, null);

  store.setSocialPostStatus(TEST_ROOT, postId, "published", { remotePostId: "12345", remoteUrl: "https://x.com/i/web/status/12345" });
  post = store.getSocialPost(TEST_ROOT, postId);
  assert.equal(post.status, "published");
  assert.ok(post.published_at);
  assert.equal(post.remote_post_id, "12345");
  assert.equal(post.remote_url, "https://x.com/i/web/status/12345");
});

test("social_posts: listSocialPosts scopes to the right project", () => {
  const p1 = store.createProject(TEST_ROOT, { name: "Scope A", symbol: "SCA", network: "devnet" });
  const p2 = store.createProject(TEST_ROOT, { name: "Scope B", symbol: "SCB", network: "devnet" });
  store.createSocialPost(TEST_ROOT, { projectId: p1, template: "custom", content: "a1" });
  store.createSocialPost(TEST_ROOT, { projectId: p1, template: "custom", content: "a2" });
  store.createSocialPost(TEST_ROOT, { projectId: p2, template: "custom", content: "b1" });

  assert.equal(store.listSocialPosts(TEST_ROOT, p1).length, 2);
  assert.equal(store.listSocialPosts(TEST_ROOT, p2).length, 1);
});

test("credentials: never round-trips secrets in the status object, and masks the key preview", () => {
  const projectId = "proj_credtest";
  assert.equal(creds.credentialsStatus(TEST_ROOT, projectId).configured, false);

  creds.saveCredentials(TEST_ROOT, projectId, {
    apiKey: "abcd1234efgh5678",
    apiSecret: "supersecretvalue",
    accessToken: "token-value-here",
    accessTokenSecret: "token-secret-here",
  });

  const status = creds.credentialsStatus(TEST_ROOT, projectId);
  assert.equal(status.configured, true);
  assert.match(status.apiKeyPreview, /^abcd.*5678$/);
  assert.doesNotMatch(JSON.stringify(status), /supersecretvalue|token-secret-here/);

  const loaded = creds.loadCredentials(TEST_ROOT, projectId);
  assert.equal(loaded.apiKey, "abcd1234efgh5678");

  creds.deleteCredentials(TEST_ROOT, projectId);
  assert.equal(creds.credentialsStatus(TEST_ROOT, projectId).configured, false);
});

test("credentials: rejects an incomplete set rather than saving a partial file", () => {
  assert.throws(() => creds.saveCredentials(TEST_ROOT, "proj_incomplete", { apiKey: "only-this-one" }));
  assert.equal(creds.credentialsStatus(TEST_ROOT, "proj_incomplete").configured, false);
});
