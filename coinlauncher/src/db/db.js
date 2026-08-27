// SQLite connection + schema. Uses Node's built-in node:sqlite (stable
// enough for this, marked experimental by Node itself, no native module to
// compile — avoids node-gyp/Xcode-tools headaches on any machine running
// Node 22+, same requirement this project already has).
//
// Nothing sensitive lives here. Keypairs stay on disk as files, referenced
// by path only — this DB only ever stores public addresses, file paths,
// amounts, and transaction signatures.

import { DatabaseSync } from "node:sqlite";
import path from "path";
import fs from "fs";

let db = null;

export function getDb(root) {
  if (db) return db;
  const dataDir = path.join(root, "data");
  fs.mkdirSync(dataDir, { recursive: true });
  db = new DatabaseSync(path.join(dataDir, "coinlauncher.db"));
  db.exec("PRAGMA journal_mode = WAL;");
  db.exec("PRAGMA foreign_keys = ON;");
  migrate(db);
  return db;
}

function migrate(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS projects (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      symbol TEXT NOT NULL,
      network TEXT NOT NULL,
      mint_address TEXT,
      decimals INTEGER,
      supply TEXT,
      metadata_uri TEXT,
      pool_address TEXT,             -- user-provided, verified against a known DEX program before being trusted
      pool_verified_at TEXT,
      swap_slippage_bps INTEGER DEFAULT 100,
      funding_reserve_sol REAL DEFAULT 0.02,  -- SOL kept back from the funding swap to cover this tx + the forward-to-Main-Holding tx
      funding_swap_percent REAL DEFAULT 100,  -- % of (balance - reserve) to swap; configurable, never hard-coded to 100
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS wallets (
      id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL REFERENCES projects(id),
      role TEXT NOT NULL,           -- owner | funding | main_holding | operator
      label TEXT NOT NULL,
      address TEXT NOT NULL,
      keypair_path TEXT NOT NULL,
      allocation_percent REAL,      -- operator wallets only; informational target, not enforced on balance display
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS launch_state (
      project_id TEXT PRIMARY KEY REFERENCES projects(id),
      state TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS launch_state_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      project_id TEXT NOT NULL REFERENCES projects(id),
      state TEXT NOT NULL,
      entered_at TEXT NOT NULL,
      note TEXT
    );

    -- Idempotency ledger. One row per attempted multi-step operation
    -- (e.g. one distribution run). A crash mid-operation leaves this at
    -- 'pending'; resuming checks which individual transactions already
    -- confirmed (via the transactions table + on-chain signature status)
    -- rather than blindly re-running everything.
    CREATE TABLE IF NOT EXISTS operations (
      id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL REFERENCES projects(id),
      operation_type TEXT NOT NULL,  -- e.g. DISTRIBUTION
      status TEXT NOT NULL,          -- pending | confirmed | failed
      config_json TEXT,              -- the exact inputs (e.g. allocation %) this operation was run with
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS transactions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      project_id TEXT NOT NULL REFERENCES projects(id),
      wallet_id TEXT REFERENCES wallets(id),
      wallet_role TEXT,
      type TEXT NOT NULL,            -- FUNDING_DEPOSIT | SWAP | TRANSFER | DISTRIBUTION | WITHDRAWAL | LIQUIDITY_OPERATION | MINT
      input_asset TEXT,
      input_amount TEXT,
      output_asset TEXT,
      output_amount TEXT,
      destination TEXT,
      route TEXT,
      signature TEXT,
      last_valid_block_height INTEGER, -- the blockhash's expiry height at signing time; lets reconciliation tell "still might land" from "can never land"
      network TEXT,
      status TEXT NOT NULL,          -- submitted | confirmed | failed | expired
      error TEXT,
      operation_id TEXT REFERENCES operations(id),
      created_at TEXT NOT NULL
    );

    -- Marketing/socials drafts. Deliberately holds nothing sensitive (no API
    -- keys/tokens -- those live in a chmod-600 file per project, same
    -- pattern as wallet keypairs, see socialsCredentials.js) and nothing
    -- fabricated -- content is generated only from real project/state data.
    -- Every row is born 'draft' and can only reach 'published' by passing
    -- through 'approved' first; nothing here auto-posts.
    CREATE TABLE IF NOT EXISTS social_posts (
      id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL REFERENCES projects(id),
      platform TEXT NOT NULL DEFAULT 'twitter',
      template TEXT,                 -- which generator built the first draft, if any ('custom' if hand-written)
      content TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'draft', -- draft | approved | published | failed
      remote_post_id TEXT,           -- tweet id, once published
      remote_url TEXT,
      error TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      approved_at TEXT,
      published_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_wallets_project ON wallets(project_id);
    CREATE INDEX IF NOT EXISTS idx_tx_project ON transactions(project_id);
    CREATE INDEX IF NOT EXISTS idx_tx_operation ON transactions(operation_id);
    CREATE INDEX IF NOT EXISTS idx_tx_wallet_status ON transactions(wallet_id, status);
    CREATE INDEX IF NOT EXISTS idx_ops_project ON operations(project_id);
    CREATE INDEX IF NOT EXISTS idx_social_posts_project ON social_posts(project_id);
  `);

  // Safe migrations for DBs created before these columns existed. Nothing
  // in this project has been used with real funds yet, but guard it
  // properly anyway rather than requiring anyone to manually delete their
  // local DB.
  addColumnIfMissing(db, "transactions", "last_valid_block_height", "INTEGER");
  addColumnIfMissing(db, "projects", "pool_address", "TEXT");
  addColumnIfMissing(db, "projects", "pool_verified_at", "TEXT");
  addColumnIfMissing(db, "projects", "swap_slippage_bps", "INTEGER DEFAULT 100");
  addColumnIfMissing(db, "projects", "funding_reserve_sol", "REAL DEFAULT 0.02");
  addColumnIfMissing(db, "projects", "funding_swap_percent", "REAL DEFAULT 100");
}

function addColumnIfMissing(db, table, column, ddlType) {
  const columns = db.prepare(`PRAGMA table_info(${table})`).all();
  if (!columns.some((c) => c.name === column)) {
    db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${ddlType};`);
  }
}
