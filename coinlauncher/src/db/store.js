// Typed data-access functions over the SQLite schema in db.js. No SQL
// outside this file — everything else (state machine, API routes) goes
// through these functions.

import crypto from "crypto";
import { getDb } from "./db.js";

const now = () => new Date().toISOString();
const newId = (prefix) => `${prefix}_${Date.now().toString(36)}${crypto.randomBytes(4).toString("hex")}`;

// ---- projects ----

export function createProject(root, { name, symbol, network }) {
  const db = getDb(root);
  const id = newId("proj");
  db.prepare(
    `INSERT INTO projects (id, name, symbol, network, created_at) VALUES (?, ?, ?, ?, ?)`
  ).run(id, name, symbol, network, now());
  return id;
}

export function updateProjectMint(root, projectId, { mintAddress, decimals, supply, metadataUri }) {
  const db = getDb(root);
  db.prepare(
    `UPDATE projects SET mint_address = ?, decimals = ?, supply = ?, metadata_uri = ? WHERE id = ?`
  ).run(mintAddress, decimals, String(supply), metadataUri || null, projectId);
}

export function getProject(root, projectId) {
  const db = getDb(root);
  return db.prepare(`SELECT * FROM projects WHERE id = ?`).get(projectId) || null;
}

export function listProjects(root) {
  const db = getDb(root);
  return db.prepare(`SELECT * FROM projects ORDER BY created_at DESC`).all();
}

// ---- wallets ----

export function addWallet(root, { projectId, role, label, address, keypairPath, allocationPercent }) {
  const db = getDb(root);
  const id = newId("wal");
  db.prepare(
    `INSERT INTO wallets (id, project_id, role, label, address, keypair_path, allocation_percent, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
  ).run(id, projectId, role, label, address, keypairPath, allocationPercent ?? null, now());
  return id;
}

export function getWalletsByProject(root, projectId) {
  const db = getDb(root);
  return db.prepare(`SELECT * FROM wallets WHERE project_id = ? ORDER BY created_at ASC`).all(projectId);
}

export function getWallet(root, walletId) {
  const db = getDb(root);
  return db.prepare(`SELECT * FROM wallets WHERE id = ?`).get(walletId) || null;
}

export function getWalletByRole(root, projectId, role) {
  const db = getDb(root);
  return db.prepare(`SELECT * FROM wallets WHERE project_id = ? AND role = ? ORDER BY created_at ASC LIMIT 1`).get(projectId, role) || null;
}

export function getOperatorWallets(root, projectId) {
  const db = getDb(root);
  return db.prepare(`SELECT * FROM wallets WHERE project_id = ? AND role = 'operator' ORDER BY created_at ASC`).all(projectId);
}

// ---- launch state ----

export function getState(root, projectId) {
  const db = getDb(root);
  return db.prepare(`SELECT * FROM launch_state WHERE project_id = ?`).get(projectId) || null;
}

export function setState(root, projectId, state, note) {
  const db = getDb(root);
  const ts = now();
  db.prepare(
    `INSERT INTO launch_state (project_id, state, updated_at) VALUES (?, ?, ?)
     ON CONFLICT(project_id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at`
  ).run(projectId, state, ts);
  db.prepare(
    `INSERT INTO launch_state_history (project_id, state, entered_at, note) VALUES (?, ?, ?, ?)`
  ).run(projectId, state, ts, note || null);
}

export function getStateHistory(root, projectId) {
  const db = getDb(root);
  return db.prepare(`SELECT * FROM launch_state_history WHERE project_id = ? ORDER BY id ASC`).all(projectId);
}

// ---- operations (idempotency ledger) ----

export function findOperation(root, projectId, operationType) {
  const db = getDb(root);
  return db
    .prepare(
      `SELECT * FROM operations WHERE project_id = ? AND operation_type = ? ORDER BY created_at DESC LIMIT 1`
    )
    .get(projectId, operationType);
}

export function createOperation(root, { projectId, operationType, config }) {
  const db = getDb(root);
  const id = newId("op");
  const ts = now();
  db.prepare(
    `INSERT INTO operations (id, project_id, operation_type, status, config_json, created_at, updated_at)
     VALUES (?, ?, ?, 'pending', ?, ?, ?)`
  ).run(id, projectId, operationType, JSON.stringify(config || {}), ts, ts);
  return id;
}

export function updateOperationStatus(root, operationId, status) {
  const db = getDb(root);
  db.prepare(`UPDATE operations SET status = ?, updated_at = ? WHERE id = ?`).run(status, now(), operationId);
}

export function getOperation(root, operationId) {
  const db = getDb(root);
  return db.prepare(`SELECT * FROM operations WHERE id = ?`).get(operationId) || null;
}

// ---- transactions (audit trail) ----

export function recordTransaction(root, tx) {
  const db = getDb(root);
  const result = db
    .prepare(
      `INSERT INTO transactions
        (project_id, wallet_id, wallet_role, type, input_asset, input_amount, output_asset, output_amount,
         destination, route, signature, network, status, error, operation_id, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .run(
      tx.projectId,
      tx.walletId || null,
      tx.walletRole || null,
      tx.type,
      tx.inputAsset || null,
      tx.inputAmount != null ? String(tx.inputAmount) : null,
      tx.outputAsset || null,
      tx.outputAmount != null ? String(tx.outputAmount) : null,
      tx.destination || null,
      tx.route || null,
      tx.signature || null,
      tx.network || null,
      tx.status,
      tx.error || null,
      tx.operationId || null,
      now()
    );
  return result.lastInsertRowid;
}

export function updateTransaction(root, txId, { status, signature, error }) {
  const db = getDb(root);
  db.prepare(
    `UPDATE transactions SET status = ?, signature = COALESCE(?, signature), error = COALESCE(?, error) WHERE id = ?`
  ).run(status, signature || null, error || null, txId);
}

export function getTransactionsByProject(root, projectId, limit = 100) {
  const db = getDb(root);
  return db
    .prepare(`SELECT * FROM transactions WHERE project_id = ? ORDER BY id DESC LIMIT ?`)
    .all(projectId, limit);
}

export function getTransactionsByOperation(root, operationId) {
  const db = getDb(root);
  return db.prepare(`SELECT * FROM transactions WHERE operation_id = ? ORDER BY id ASC`).all(operationId);
}
