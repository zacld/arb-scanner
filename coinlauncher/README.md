# coinlauncher

Launches a single-owner SPL token on Solana: one mint, one dedicated wallet
per coin, Metaplex metadata (with thumbnail), fixed supply, mint/freeze
authority revoked after minting by default so supply can never be inflated
later. Devnet by default; mainnet requires an explicit typed confirmation
and shows a cost estimate first.

**What this deliberately does not do:** distribute tokens to other wallets,
coordinate trades, hold or touch any wallet other than the one it creates
(or the one you point it at), create/manage liquidity, or create/automate
any social media account. Liquidity is a separate step you do yourself on
Raydium's own site. A social link is optional metadata you attach to an
account you already own — this tool never signs up for one on your behalf.

## Setup

```bash
cd coinlauncher
npm install
```

You need a Solana keypair file (the standard format the Solana CLI
produces), but you don't need to create one yourself. By default the
dashboard **creates a brand-new wallet for every coin you launch**
(saved under `coinlauncher/wallets/<SYMBOL>-<timestamp>.json`) — keeps each
project's funds and mint authority separate from the others. On devnet
that new wallet is also **topped up with free test SOL automatically**.
Nothing to run beforehand.

Prefer to reuse one wallet across launches instead? Switch **Wallet** to
"Use an existing wallet" in the dashboard and give it a path — same
auto-create/auto-fund behavior applies to that path too.

(If you'd rather set up the default wallet yourself first, `npm run
wallet:new` / `npm run wallet:airdrop` / `npm run wallet:balance` are still
there. If you have the real Solana CLI installed, `solana-keygen new` /
`solana airdrop 2 --url devnet` do the same thing.)

This file **never leaves your machine** — the dashboard/CLI read and write
it locally to sign transactions and nothing else touches it. Don't commit
it, don't paste its contents anywhere, don't send it to anyone (that *is*
"giving out the wallet" — the exact thing to avoid). Once it's created,
back it up somewhere safe — it's the only copy of that wallet's private key.

**Mainnet is never auto-funded** — that would mean creating real money,
which isn't possible. On mainnet the tools only create the wallet file if
missing; you still have to send it real SOL yourself (from an exchange or
another wallet) before a mainnet launch will go through.

## Run the dashboard

```bash
npm start
# open http://127.0.0.1:7777
```

Binds to `127.0.0.1` only — never reachable from another machine, same as
the rest of this repo's local dashboards.

Fill in **Name** and **Symbol** — everything else (decimals, supply,
authority revocation, wallet) is pre-filled with sensible defaults.
Optionally drop in a **thumbnail image**, a **description**, and a link to
an **X/Twitter account you already own** — these get uploaded and wired
into the token's on-chain metadata automatically (see "Thumbnails and
metadata hosting" below). Leave network on **devnet** first and confirm the
whole flow works before touching mainnet. Switching to **mainnet-beta**
reveals a red confirmation box: you must type the exact phrase shown before
it will submit anything real.

## Thumbnails and metadata hosting

Add a thumbnail/description/X link and the dashboard automatically:

1. Uploads the image to [Irys](https://irys.xyz) — permanent, content-addressed
   storage, paid for directly out of the coin's own wallet (a fraction of a
   cent for a typical image on mainnet; devnet uploads are free/cheap but
   only meant for testing the flow, not a permanent public thumbnail).
2. Builds the standard token metadata JSON (name, symbol, description,
   image, and your X link if given) and uploads that too.
3. Feeds the resulting metadata URI into the mint — no manual JSON, no
   manual hosting, no separate account/API key to set up.

If the upload fails for any reason (e.g. no network, insufficient balance),
the launch still goes through — you just get a token with no image/
description yet, plus a clear note explaining what happened. Nothing is
lost; you can always add metadata after the fact by re-uploading through
Metaplex's tools directly and updating the mint (advanced, not covered by
this dashboard).

**On the X/Twitter link:** this must be an account you already created and
own. This tool will never sign up for one on your behalf — automated
account creation violates X's own Terms of Service and reads as exactly the
kind of manufactured-legitimacy pattern that gets both the coin and the
account flagged.

## Or use the CLI directly

```bash
node src/cli.js --name "My Token" --symbol MYTKN --supply 1000000000

# when you're ready for real:
node src/cli.js --network mainnet-beta --confirm "LAUNCH MAINNET" \
  --name "My Token" --symbol MYTKN --supply 1000000000
```

Wallet defaults to `~/.config/solana/id.json` (created/funded automatically
as above); pass `--keypair <path>` to use a different one.

## After the mint exists

The result gives you a Solscan link and a pre-filled Raydium
"create pool" link. Liquidity creation is real custody of funds in a pool
contract — that step is intentionally left to Raydium's own, audited UI
rather than reimplemented here. You connect your wallet there directly and
decide the pair, amount, and (recommended) whether to lock the LP tokens.

## Costs

- **devnet**: free (fake SOL from `solana airdrop`).
- **mainnet-beta**: roughly 0.01–0.03 SOL in account rent + network fees for
  the mint + metadata + mint-to transaction, plus whatever you choose to put
  into the liquidity pool afterward. The dashboard/CLI show an estimate but
  it's not exact — check your wallet balance before confirming.

## Environment variables (optional)

Copy `.env.example` to `.env` in this directory if you want a custom mainnet
RPC endpoint (public mainnet RPC can be slow/rate-limited):

```
MAINNET_RPC_URL=https://your-rpc-provider-url
PORT=7777
```
