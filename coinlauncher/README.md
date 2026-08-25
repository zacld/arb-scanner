# coinlauncher

Launches a single-owner SPL token on Solana: one mint, one wallet (yours),
Metaplex metadata, fixed supply, mint/freeze authority revoked after minting
by default so supply can never be inflated later. Devnet by default; mainnet
requires an explicit typed confirmation and shows a cost estimate first.

**What this deliberately does not do:** distribute tokens to other wallets,
coordinate trades, hold or touch any wallet other than the one you point it
at, or create/manage liquidity. Liquidity is a separate step you do yourself
on Raydium's own site, with your own wallet — this tool just hands you the
pre-filled link after the mint exists.

## Setup

```bash
cd coinlauncher
npm install
```

You need a Solana keypair file (the standard format the Solana CLI
produces), but you don't need to create one yourself — both the dashboard
and the CLI **create `~/.config/solana/id.json` automatically** the first
time you launch if it doesn't already exist, and on devnet they also
**top it up with free test SOL automatically** if the balance is low.
Nothing to run beforehand.

(If you'd rather set it up yourself first, `npm run wallet:new` /
`npm run wallet:airdrop` / `npm run wallet:balance` are still there. If you
have the real Solana CLI installed, `solana-keygen new` / `solana airdrop 2
--url devnet` do the same thing.)

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
authority revocation, wallet path) is pre-filled with sensible defaults.
Leave network on **devnet** first and confirm the whole flow works before
touching mainnet. Switching to **mainnet-beta** reveals a red confirmation
box: you must type the exact phrase shown before it will submit anything real.

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
