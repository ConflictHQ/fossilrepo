# fossilrepo — bootstrap

Omnibus-style installer for a self-hosted Fossil forge. One command gets you a full-stack code hosting platform: VCS, issues, wiki, timeline, web UI, SSL, and continuous backups — all powered by Fossil SCM.

Think GitLab Omnibus, but for Fossil.

---

## Why Fossil

A Fossil repo is a single SQLite file. It contains the full VCS history, issue tracker, wiki, forum, and timeline. No external services. No rate limits. Portable — hand the file to someone and they have everything.

For teams running CI agents or automation:
- Agents commit, file tickets, and update the wiki through one CLI and one protocol
- No API rate limits when many agents are pushing simultaneously
- The `.fossil` file IS the project artifact — a self-contained archive
- Litestream replicates it to S3 continuously — backup and point-in-time recovery for free

Fossil also has a built-in web UI (skinnable), autosync, peer-to-peer sync, and unversioned content storage (like Git LFS but built-in).

---

## What fossilrepo Does

fossilrepo packages everything needed to run a production Fossil server into one installable unit:

- **Fossil server** — serves all repos from a single process
- **Caddy** — SSL termination, subdomain-per-repo routing (`reponame.your-domain.com`)
- **Litestream** — continuous SQLite replication to S3/MinIO (backup + point-in-time recovery)
- **CLI** — repo lifecycle management (create, list, delete) and sync tooling
- **Sync bridge** — mirror Fossil repos to GitHub/GitLab as downstream read-only copies

New project = `fossil init`. No restart, no config change. Litestream picks it up automatically.

---

## Architecture

```
fossilrepo/
├── server/      # Fossil server infra — Docker, Caddy, Litestream
├── sync/        # Fossil → GitHub/GitLab mirror
├── cli/         # fossilrepo CLI wrapper
└── docs/        # Architecture, guides
```

### Server Stack

```
Caddy  (SSL termination, routing, subdomain per repo)
  └── fossil server --repolist /data/repos/
        └── /data/repos/
              ├── projecta.fossil
              ├── projectb.fossil
              └── ...

Litestream → S3/MinIO  (continuous replication, point-in-time recovery)
```

One binary serves all repos. The whole platform is: repo creation + subdomain provisioning + Litestream config.

### Sync Bridge

Mirrors Fossil to GitHub/GitLab as a downstream copy. Fossil is the source of truth.

Maps:
- Fossil commits → Git commits
- Fossil tickets → GitHub/GitLab Issues (optional, configurable)
- Fossil wiki → repo docs (optional, configurable)

Triggered on demand or on schedule.

---

## Platform Vision (fossilrepos.com)

GitLab model:
- **Self-hosted** — open source, run it yourself. fossilrepo is the tool.
- **Managed** — fossilrepos.com, hosted for you. Subdomain per repo, modern UI, billing.

The platform is Fossil's built-in web UI with a modern skin + thin API wrapper + authentication. Not a rewrite — Fossil already does the hard parts. The value is the hosting and UX polish.

Not being built yet — get the self-hosted tool right first.

---

## License

MIT.
