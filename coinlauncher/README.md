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

## Architecture (Phase 1)

The dashboard is now organized around **projects**, each with a role-tagged
set of wallets and a persisted launch state, stored in a local SQLite
database (`coinlauncher/data/coinlauncher.db`, via Node's built-in
`node:sqlite` — no native module to compile). Pages:

- **Launch** (`index.html`) — configure and mint a token; creates the
  project plus its Owner, Funding, and Main Holding wallets, and any
  operator wallets you configure (created, not yet funded).
- **Overview** (`overview.html`) — token info, all wallets, the 12-state
  launch progress ladder, the Distribution action, and the transaction
  audit trail.
- **Liquidity** (`liquidity.html`) — pool/route status. Honest placeholder
  in Phase 1 — see below.
- **Wallets** (`console.html`) — one tab per wallet (Owner/Funding/Main
  Holding/each operator), each with Receive (address + QR), Balance, Send,
  Swap (mainnet only), and transaction history.
- **Trading**, **Analytics**, **Socials** — placeholders describing what's
  there now vs. planned.

**Wallet roles:** `owner` (holds mint authority origin), `funding`
(receives your external SOL, will perform the real market buy in Phase 2),
`main_holding` (distribution hub), `operator` (Wallets 1-N, commonly
controlled, never presented as independent holders).

**Distribution** (Main Holding → operator wallets) is hardened: percentages
apply to Main Holding's *real* on-chain balance at run time (not an assumed
total), an operations ledger means a crash mid-run resumes instead of
restarting blindly, every attempt is written to the audit trail as it
happens, and `DISTRIBUTION_COMPLETE` is only ever set once every wallet's
transfer has actually confirmed on-chain. Run it from the Overview tab.

**Milestones vs. operations** (`stateMachine.js`'s `STATE_KIND`): every one
of the 12 states is classified as a *milestone* (a pure observation of
chain/DB state — safe to recompute anywhere, any time, no side effects —
e.g. "does Funding wallet have SOL") or an *operation* (an intentional,
tracked write, gated by the operations ledger and the hardened transaction
pipeline below — e.g. `DISTRIBUTION_PENDING`/`COMPLETE` track the
distribution action's own lifecycle, not an independently-observable
condition). The Overview ladder shows a badge for each.

**Transaction crash-safety** (`chainTx.js`): every write goes through
`build → sign → persist (with signature) → broadcast → poll/confirm →
update`. A Solana signature is deterministic from signing (not assigned by
the network), so it's known and persisted *before* broadcasting — a crash
at any point after that leaves a durable, checkable record instead of an
unknowable gap. On restart (or before any new write for a wallet),
`assertWalletClearToTransact` reconciles every unresolved transaction
against the chain first: confirmed → recorded; landed-but-errored →
failed; blockhash expired without landing → `expired` (provably dead, safe
to build a *fresh* transaction for the same logical operation); still
genuinely ambiguous → blocks new writes for that wallet until it resolves.
`force` only overrides the separate "already completed" guard — it does
not bypass reconciliation, and nothing ever blindly rebroadcasts the same
signed bytes just because local confirmation wasn't recorded.

Known residual gap: the pure decision logic and the reconcile/guard
behavior are unit-tested (with an injectable fake chain response — no live
network needed); the actual `submitAndTrack` broadcast/poll loop itself has
only been verified live up to this environment's network-block boundary,
same limitation as everything else that needs real RPC access.

## Phase 2: Liquidity + real Funding Wallet swap

**Jupiter integration, verified not guessed:** before writing swap code,
pulled Jupiter's own official client (`@jup-ag/api`, checked when it was
three weeks old) via npm — registry access works here even though calling
Jupiter's/Solana's actual APIs doesn't — and read its generated source
directly. Confirmed `https://quote-api.jup.ag/v6` is still the real,
current, free default (not a deprecated legacy tier — `lite-api.jup.ag`/
`api.jup.ag` are paid-tier alternatives, not replacements), and that
`SwapResponse` includes `lastValidBlockHeight` directly. `jupiter.js`'s
`buildSwapTransaction` now returns the unsigned transaction plus that real
value instead of Jupiter-specific code doing its own separate sign+send+
confirm — swap goes through the exact same `chainTx.js` pipeline as
everything else (`submitAndTrack` gained a `prebuilt` mode for transactions
built elsewhere with their own blockhash, so it doesn't overwrite the
blockhash Jupiter priced the route against).

**Pool verification** (`liquidityService.js`): still never creates a pool —
that stays on Raydium's own audited UI. Verifies one you already created by
checking the account exists and is owned by a recognized Raydium program.
Program IDs verified the same way as Jupiter's endpoints: installed
`@raydium-io/raydium-sdk-v2` (checked when it was one day old) and read the
real values out of its source rather than trusting memory, for both
mainnet and devnet.

**Route verification**: a live Jupiter quote for a small probe amount —
the actual functional gate on whether a swap can run, independent of which
DEX Jupiter routes through. Cached ~45s so page loads don't hammer their
public endpoint. Devnet short-circuits before ever calling Jupiter (they
have no devnet equivalent at all).

**Funding Wallet's real swap** (`fundingSwapService.js`): two explicit,
separately-retriable steps, not one atomic mega-transaction — a "run
funding swap" action (SOL → TOKEN via a live-verified route only; refuses
outright with the real reason if no route exists, never submits a
transaction expected to fail) and a "forward to Main Holding" action
(moves whatever TOKEN the Funding wallet actually holds afterward — real
balance, not the swap quote's predicted output). Both on the Liquidity
page, both go through the same `assertWalletClearToTransact` guard and
operations ledger as Distribution. GBP/display amounts never enter any
calculation — SOL and base units throughout.

**Configurable, not hard-coded**: swap slippage, the SOL reserved for fees
(covers both the swap's own fee and the subsequent forward's fee/rent),
and what percentage of the available balance to swap are all per-project
settings on the Liquidity page, with sensible defaults.

**Milestone wiring**: `LIQUIDITY_CREATED` now reflects a real verified pool
address (trusted once recorded — pools don't stop existing; re-verify by
re-submitting the address if ever needed). `ROUTE_CONFIRMED` is live and
fresh on every check (must be, since tradeability can genuinely change).
`FUNDING_SWAP_PENDING`/`COMPLETE` track the swap operation's ledger status.
`LAUNCH_ACTIVE` = a real route exists AND operator wallets are funded.

Known gap, unchanged from Phase 1: the mint transaction itself
(`createToken.js`) still isn't migrated onto `chainTx.js`'s hardened
pipeline — it's a single one-time operation per project rather than a
repeatable one, which is why it's lower priority, but it's still a real
write and worth closing eventually.

**What's real vs. placeholder in Phase 1:** `PROJECT_CREATED` and
`TOKEN_CREATED` are explicit (set when they actually happen).
`FUNDING_RECEIVED` and `MAIN_WALLET_FUNDED` are derived live from real
on-chain balances every time you check. `DISTRIBUTION_PENDING`/`COMPLETE`
are real, hardened, tested actions. `LIQUIDITY_*`, `ROUTE_CONFIRMED`,
`FUNDING_SWAP_*`, and `LAUNCH_ACTIVE` are modeled (visible in the progress
ladder, explained honestly) but not implemented — that's the real
market-swap/liquidity integration, which is Phase 2, and this build never
fakes it.

## Socials: generate → approve → publish

The launch/liquidity/wallet side above is the whole automated pipeline —
mint, three-tier wallets, real swap, real distribution, one-click orchestration.
This section is the marketing layer on top of it: draft content grounded in
what actually happened on-chain, reviewed by a person, then published.

**Nothing here creates or logs into an X account for you.** You make (or
already have) a real X account and a developer app at developer.x.com
yourself, generate a read/write API key + secret and access token + secret
there, and paste them into the Socials page. There is no OAuth flow run by
this tool, no CAPTCHA/email/phone-verification bypass, and no path by which
this tool could create an account on your behalf — all explicitly out of
scope from the original spec, not just an oversight.

**Credential storage matches the private-key pattern, not the DB pattern**:
saved to `secrets/social/<projectId>.json`, chmod 600, gitignored, never
written into SQLite and never echoed back — the API only ever returns a
masked preview (`credentialsStatus` in `socialsCredentials.js`). Deleting a
project's credentials removes the file.

**Content generation** (`socialsService.js`) is template-based, not an LLM
call, and every template is built only from data this tool already verified
for itself — mint address, fixed supply, a pool address that passed
`verifyPoolAddress`. There is no template for holder count, volume, or price
(nothing here tracks those, so nothing here can claim them), no
promised-returns language, no invented partnerships or endorsements, and
operator wallets are never described as independent holders. `custom` is a
blank draft for anything the templates don't cover — it still goes through
the same approve/publish gate as a generated one.

**The status machine is linear and one-directional past publish**: every
post is born `draft`, can be edited freely while `draft`, must be explicitly
`approve`d before `publish` is even reachable (the API rejects
publish-from-draft outright), and `approved` can still be sent back to
`draft` or deleted — but a `published` post can't be edited or deleted, only
viewed, since it's already real and public. A failed publish attempt (bad
credentials, X API error) reverts the post to `approved` with the error
attached, not stuck in limbo and not silently retried.

**Posting itself** is a single signed `POST /2/tweets` call, OAuth 1.0a
user-context, implemented directly against X's HTTP API (`buildOauthHeader`
in `socialsService.js`) rather than pulling in an SDK for one endpoint.
Verified against the real API during development — deliberately-wrong test
credentials produced a genuine `403` back from `api.twitter.com`, confirming
the request actually reaches X and errors are surfaced rather than a network
exception crashing the request.

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

## Operator wallets and the wallet console

The "Operator wallets" section on the launch form lets you split the newly
minted supply across several wallets right at launch — e.g. a wallet for LP
reserve, one for marketing budget, one for treasury. Every one of them is:

- **Created and controlled by you**, the same as the owner wallet — this is
  internal fund organization for one operator, not distribution to third
  parties.
- **Clearly labeled as operator-controlled** everywhere it's shown (the
  launch result, the console banner, every wallet tab) — never presented as
  an independent holder.
- **Configurable, not hard-coded** — add/remove rows, set your own labels
  and percentages (must total 100% or less; anything left over stays in the
  owner wallet).

Leave every percentage at 0 (or remove all rows) to skip this and keep the
full supply in the owner wallet, same as before this feature existed.

**This is a wallet system, not a disclosure mechanism.** It doesn't publish
anywhere that these wallets are related — that's a separate decision for
you to make in how you describe the project publicly (a website, docs, a
pinned post). The wallets being commonly controlled is a fact about how
they were created; whether buyers know that fact depends on what you tell
them, not on anything this tool does automatically.

After a launch with operator wallets, open the **wallet console**
(`/console.html`, or the link in the launch result) to manage them — pick a
launch from the dropdown, click a wallet's tab to select it, and:

- **Receive** — address, copy button, QR code.
- **Balance** — SOL and token balance, refreshable.
- **Send** — SOL or the token, to any address, from whichever wallet tab is
  active (that's how you pick which wallet a transaction comes from).
- **Swap** — mainnet only (devnet has no real liquidity to swap against),
  via [Jupiter](https://jup.ag)'s public swap API. Get a quote, review it,
  confirm to execute.
- **Transaction history** — recent signatures for that wallet, linked to
  Solscan.

Every operator wallet also gets a small SOL top-up (0.01 SOL) at
distribution time so it can pay its own transaction fees later.

**Not verified against live Jupiter/Solana endpoints from this repo's build
environment** — outbound network access was blocked there entirely, so
balances/send/history/swap could only be verified up to that boundary
(request parsing, validation, and wallet/manifest wiring all confirmed
correct; the actual RPC and Jupiter calls could not be exercised). If swap
specifically errors in a way that looks like a wrong endpoint rather than a
normal failure (insufficient balance, no route, etc.), Jupiter may have
moved their API — report the exact error and it's a quick fix.

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
