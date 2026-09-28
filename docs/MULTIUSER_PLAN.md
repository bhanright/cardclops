# Plan: accounts, one server, many users (not started)

Written 2026-09-27 at the owner's request and parked: Cardclops isn't being distributed yet. Nothing here
is built. Revisit before starting; check the code against it first, since the app will have moved on.

## Shape

**One database file per user, plus the shared card database.** Each account gets
`users/<id>/cardclops.sqlite` (holdings, decks, watchlist, alerts, meta, its backups); card data,
images and price history are shared. The alternative, one database with a user column on every
table, means rewriting nearly every query and makes a missed filter a cross-user leak. Per-user files
keep the modules as they are (each takes a `connection`), and make export and deletion trivial.

**Clients** are the Windows app window, the Android app and the browser pointed at the server. Each
app gets a first-run choice: "On this device" (today's local engine) or "Connect to a server"
(address, then login). An offline client that syncs is a separate, much larger project; leave it until
the online version exists.

## Phase 1: the server holds many users (about 2–3 sessions)

1. Move `price_series` (public data, today copied into every user database; see `USER_TABLES` in
   gallery/db.py) into the shared database. Users keep only what is theirs.
2. Replace the global `Handler.gallery` (gallery/server.py) with a registry: a user's `Gallery` loads on
   their first request and unloads after ~30 minutes idle.
3. Share card data in memory: one read-only card dict per printing, referenced by every user's
   entries (today each collection holds its own; that is most of the ~650 MB for 35k copies).
4. Split refresh: card data and prices once, server-wide, on the existing timer; then per-user work
   (alerts, backups).
5. Isolation tests: user A can't reach user B's decks, lines, holdings, alerts or watchlist through any
   endpoint, including by guessing ids.

## Phase 2: accounts and login (about 2 sessions)

- **A. Cloudflare Access (recommended).** cardclops.com is already behind Access. Add friends to the
  policy (email one-time PIN); the server verifies the `Cf-Access-Jwt-Assertion` header against the
  team's certificates and maps the email to a user folder. No passwords stored; Cloudflare does rate
  limiting, resets and 2FA. The apps sign in through Access's page inside their window.
- **B. Built-in accounts.** An accounts database (stdlib `hashlib.scrypt` hashes), sessions table,
  secure HttpOnly cookie for browsers, a revocable per-device token for the apps (Android encrypted
  storage, Windows Credential Manager), invite-only sign-up from an admin page, login rate limits, a
  "your devices" list, resets by the admin until there is email.

Start with A: about half the work, the most secure, and the multi-user parts don't change if B
comes later.

## Phase 3: clients (about 1–2 sessions)

- Windows (gallery/app.py): local or server at first launch; server mode loads the server address in
  the window instead of starting an engine.
- Android: the same choice; server mode skips the bundled Python (instant start, little memory).
- Web: login/logout and a user menu.

## Phase 4: things that assume one user

- The Ask box runs the `claude` CLI on the server: off for other users, or each brings their own key.
- Alerts in-app for everyone; Windows notifications stay local-mode; email later.
- Archidekt: one rate limit shared across users.
- Admin page: users, storage, last seen, disable/delete, invites.
- Per-user backups, export (the decks file exists), and account deletion.
- Capacity: the CX23 has 4 GB; after phase 1's memory change it holds a handful of active users. A
  larger server (about €8–15/month) gives headroom.

## Decisions to make first

1. Identity: Cloudflare Access or built-in accounts.
2. Keep local mode in the apps (recommended: yes).
3. Ask box for other users: off, or bring-your-own key.
4. Roughly how many people (sets the memory budget and server size).

## Risks

- Phase 1's shared card data changes how collections load: the riskiest step; most tests go there.
- Everyone gets each server deploy at once: add a staging copy before deploys.
- Holding other people's data: keep sign-up invite-only, with backups and deletion working from day one.

Estimate: 5–8 working sessions, in phase order, each phase shippable on its own.
