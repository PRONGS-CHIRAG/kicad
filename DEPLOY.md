# Deploying KiCAD Mitos

Backend on **Fly.io** (one machine, one volume), frontend on **Vercel**.

The backend cannot go on Vercel, Lambda, or anything serverless, and this is not
a preference:

- `_runs` in [`backend/app/api/team.py`](backend/app/api/team.py) is a
  module-level dict fed by a module-level `ThreadPoolExecutor`, and
  `SessionStore` in [`backend/app/workflow.py`](backend/app/workflow.py) is
  in-memory. The UI polls `GET /api/team/runs/{id}`. A poll that lands on a
  second instance gets a 404.
- Uploads, checkpoints and team evidence are files under
  `settings.workspace_dir`, so it needs a persistent disk.
- The image ships `kicad`, `kicad-symbols` and `kicad-footprints` (~1.8 GB) and
  shells out to `kicad-cli` for real ERC/DRC.

So: **one machine, one uvicorn worker, never scaled to zero, one volume pinned
to it.** Fly's 8 GB image limit is comfortably above 1.8 GB.

Two things people get wrong on the first attempt, and the reason the steps below
are in this order:

- `NEXT_PUBLIC_API_BASE` is inlined into the frontend bundle **at build time**.
  Changing it in Vercel does nothing until you redeploy.
- `KICAD_MITOS_CORS_ORIGINS` must name the frontend's origin, and
  `allow_credentials=True` in [`backend/app/main.py`](backend/app/main.py) means
  `["*"]` is **not** a valid escape hatch — browsers reject wildcard-plus-credentials.

Hence: backend first (to get its URL) -> frontend (built against that URL) ->
back to the backend to set CORS.

---

## 0. Prerequisites

Accounts only — no CLI install is needed for either service:

- A [Fly.io](https://fly.io) account with a payment method (a machine with a
  volume is not on the free allowance).
- A [Vercel](https://vercel.com) account.
- This repo pushed to GitHub, with Actions enabled.

### A note on `backend/.env`

`backend/.env` holds your live Devin key. It is git-ignored, but the Docker
build **context is the filesystem, not git**, and `app/config.py` sets
`env_file = ".env"` resolved against the image's `WORKDIR` (`/app/backend`) — so
a local `docker build` would bake the key into the image and load it at runtime.
`.dockerignore` now excludes `**/.env`.

The browser path in step 1 is not affected: the GitHub Action builds from a git
checkout, where the file does not exist at all. Run this only if you also build
locally (`docker compose`, or `fly deploy` from your laptop):

```bash
grep -n '\*\*/\.env' .dockerignore     # must print a match

# This build must FAIL with "not found" — that means .env is excluded from the
# build context. If it succeeds, your key would ship in the image.
printf 'FROM alpine\nCOPY backend/.env /tmp/leaked\n' > /tmp/leak-test.Dockerfile
docker build --no-cache -f /tmp/leak-test.Dockerfile .
```

---

## 1. Backend -> Fly.io (browser)

Fly has no dashboard deploy for a Dockerfile app — `fly apps create` and
`fly deploy` have no web equivalent — so the browser path routes through a
GitHub Actions workflow that runs them for you. If you want genuinely
click-only with no YAML at all, Render is the platform that does that; say the
word and I will write that version instead.

Everything below happens in two browser tabs: the Fly dashboard and GitHub.
Nothing is typed into a terminal.

### 1a. Pick the app name

Fly app names are globally unique. Open [`fly.toml`](fly.toml) and edit the
first line if `kicad-mitos-api` is likely taken:

```toml
app = "kicad-mitos-api"
```

You can edit it straight from GitHub's web editor (press `.` on the repo, or
use the pencil icon). Everything else in that file is already set — do not
change `auto_stop_machines`, `min_machines_running`, or the `[[mounts]]` block.

### 1b. Create a deploy token (Fly dashboard)

1. Go to [fly.io/dashboard](https://fly.io/dashboard).
2. Choose your organization from the **org dropdown** at the top.
3. Click **Tokens** in the sidebar.
4. Create a new **org-scoped** token. It must be org-scoped, not app-scoped —
   the app does not exist yet, and the workflow needs permission to create it.
5. Copy the whole value, including the `FlyV1` prefix **and the trailing space
   after it**. Dropping that prefix is the single most common cause of
   `Error: could not resolve token`.

Note your **organization slug** while you are here (it is in the URL:
`fly.io/dashboard/<slug>`). It is usually `personal`. You will need it in 1d.

### 1c. Store the token in GitHub

In the repo: **Settings -> Secrets and variables -> Actions -> New repository
secret**.

| Name | Value |
|---|---|
| `FLY_API_TOKEN` | the token from 1b, `FlyV1 ` prefix included |

### 1d. Run the deploy

The workflow is already committed at
[`.github/workflows/deploy-backend.yml`](.github/workflows/deploy-backend.yml).

1. Repo -> **Actions** tab.
2. Select **Deploy backend to Fly** in the left sidebar.
3. Click **Run workflow**, enter your org slug (default `personal`), and confirm.

It is `workflow_dispatch` only — deliberately not on push, because a redeploy
restarts the single machine and kills every in-flight run. You deploy when you
choose to.

The run creates the app, builds the image on Fly's remote builder, and starts
one machine. **The first run takes 8-15 minutes** because the image installs
KiCAD from apt; watch the live log in the Actions tab. Later runs are much
faster thanks to layer caching.

The volume is created automatically on that first deploy, from
`initial_size = "3gb"` in the `[[mounts]]` block — no `fly volumes create`
needed. Afterwards, check **your app -> Volumes** in the dashboard and confirm
it landed in the same region as the machine (`iad`, per `primary_region`). A
region mismatch is the one thing here worth fixing by hand.

### 1e. Verify

In the Fly dashboard, open the app:

- **Machines** — exactly one, state `started`, health check passing.
- **Monitoring** — the uvicorn startup log, no tracebacks.

Then in any browser tab:

```
https://<your-app>.fly.dev/api/health
https://<your-app>.fly.dev/api/projects
```

`/api/projects` should list the fixture projects. If it returns an empty list,
`KICAD_MITOS_PROJECTS_DIR` in `fly.toml` is not matching where the image put
`fixtures/`.

### 1f. Secrets (Fly dashboard)

App -> **Secrets** -> add each one. Set these **before** your first deploy if
you can: secrets added beforehand are staged and applied when the machine is
first created, whereas secrets added afterwards **restart the machine** — which
kills any in-flight run, exactly like a redeploy.

Read the security note at the bottom of this file before putting a Devin key on
a publicly reachable instance.

| Name | Value |
|---|---|
| `KICAD_MITOS_DEVIN_API_KEY` | `cog_...` |
| `KICAD_MITOS_AUTO_RESOLVE` | `true` |
| `KICAD_MITOS_DEVIN_MODE` | `normal` |
| `KICAD_MITOS_DEVIN_MAX_ACU` | `5` |

Quirk worth knowing: secrets set through the CLI do not always render in the
dashboard list, so a Secrets page that looks empty is not proof the value did
not take.

## 2. Frontend -> Vercel

The Vercel project points at the `frontend/` subdirectory of the same repo.

**Dashboard:** Add New -> Project -> import the repo -> click **Edit** next to
**Root Directory** and select `frontend`. Framework preset auto-detects as
Next.js. Before clicking Deploy, expand **Environment Variables** and add:

| Name | Value | Environments |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | `https://kicad-mitos-api.fly.dev` | Production, Preview, Development |

**Or CLI**, from the repo root:

```bash
cd frontend
vercel link                    # creates the project
vercel env add NEXT_PUBLIC_API_BASE production
# paste: https://kicad-mitos-api.fly.dev
vercel --prod
```

Note the production URL it prints, e.g. `https://kicad-mitos.vercel.app`.

`output: "standalone"` in [`frontend/next.config.mjs`](frontend/next.config.mjs)
exists only for the Docker image — Vercel does its own bundling and normally
ignores it. Leave the line in place; if a Vercel build ever errors on it, remove
it and rely on `docker compose` for the self-hosted path instead.

Every component that calls the backend is `"use client"`, so nothing fetches the
API during the build. The Vercel build does not need the backend to be reachable
— only the correct string in `NEXT_PUBLIC_API_BASE`.

---

## 3. Close the loop: CORS

Fly dashboard -> your app -> **Secrets** -> add:

| Name | Value |
|---|---|
| `KICAD_MITOS_CORS_ORIGINS` | `["https://kicad-mitos.vercel.app"]` |

Saving it restarts the machine. That takes a few seconds and does not rebuild
the image.

The value is parsed as JSON by pydantic-settings, so the brackets, the quotes
and the `https://` are all required, and there must be no trailing slash. A
custom domain added later is a **new origin** — add it to the list too, or the
`.vercel.app` URL will work and your own domain will not.

To verify from the browser: open your Vercel URL, open devtools -> Network, and
reload. The request to `/api/projects` must show an
`access-control-allow-origin` response header matching your Vercel origin. A red
CORS error in the console means the string does not match exactly.

---

## 4. End-to-end check

Open the Vercel URL, pick a fixture project, and run a request through. If the
UI says "Request failed", it is almost always one of:

| Symptom | Cause |
|---|---|
| Browser console: blocked by CORS | Step 3 not done, or the origin string does not match exactly |
| Browser console: mixed content | `NEXT_PUBLIC_API_BASE` is `http://`; it must be `https://` |
| Requests go to `localhost:8000` | The env var was added after the build — redeploy Vercel |
| 404 on `/api/team/runs/{id}` | More than one Fly machine — check app -> **Machines** in the dashboard; there must be exactly one |

---

## Redeploying

**Backend:** GitHub -> **Actions** -> **Deploy backend to Fly** -> **Run
workflow**. Same button as the first deploy.

**Frontend:** Vercel dashboard -> **Deployments** -> **Redeploy** on the latest,
or just push to the branch the project tracks.

A backend redeploy restarts the machine, so **every in-flight run and every open
session is lost** — the workspace files survive on the volume, but the session
IDs and run IDs that referenced them do not. Deploy when nothing is running.

There is no zero-downtime option here. An app with a volume can only use the
`rolling` or `immediate` strategies — `canary` and `bluegreen` both need a second
machine, and there is only one volume for it to claim. Default `rolling` on a
single machine means it stops, updates in place, and comes back; expect a short
outage.

---

## Things to know about this deployment

**One machine, on purpose.** Do not add `--workers` to the uvicorn command, do
not scale the machine count above one (dashboard app -> Machines, or
`fly scale count`), do not turn `auto_stop_machines` back on. Each of those
breaks run polling in a way that looks like a random intermittent 404.

**60-second proxy timeout.** Fly closes a connection after 60s with no bytes
flowing. Team runs are fine — `POST /api/team/runs` returns 202 immediately and
the UI polls. But `POST /api/sessions/{id}/plan` and `/execute` are synchronous,
and `/execute` runs ERC/DRC on the board. On a large board or with the LLM
planner enabled (`KICAD_MITOS_OPENAI_API_KEY`), those can cross 60s and the
browser sees a dropped connection even though the work completes server-side.
If you hit it, the fix is on Fly's side (Pro plan / support raises the timeout),
not in the app.

**Volume sizing.** Every session clones the project into `/workspace`, and
nothing garbage-collects them. 3 GB is generous for a demo; watch usage under
app -> **Volumes** in the dashboard, and grow it with `fly volumes extend` (this
one has no dashboard equivalent).

**No authentication on any route.** This matters once it is public:
`POST /api/projects` is an unauthenticated file upload, and if
`KICAD_MITOS_DEVIN_API_KEY` is set with `KICAD_MITOS_AUTO_RESOLVE=true`, anyone
who finds the URL can spend your ACU. Cheapest mitigations, pick one:

- Leave the Devin key off the deployed instance and keep `AUTO_RESOLVE=false`
  (the stub runner gives deterministic team runs with no spend).
- Put the Fly app behind [Fly's private networking](https://fly.io/docs/networking/private-networking/)
  and reach it over WireGuard instead of a public hostname.
- Protect the Vercel side with Deployment Protection — note this guards the UI
  only, not the Fly hostname.

**Vercel preview deployments get unique URLs**, none of which are in
`KICAD_MITOS_CORS_ORIGINS`, so previews will be CORS-blocked against the
production backend. Either add each preview origin to the list or test on the
production URL.

**Verification status of this document.** The compose stack these images come
from builds and runs clean locally (`kicad-backend` 1.77 GB, healthy), and the
existing local image was confirmed clean of `backend/.env`. Every `fly.toml` key
was checked against Fly's current configuration reference, and both `fly.toml`
and the workflow YAML parse. Not executed against a live account: the Fly
dashboard steps, the Actions run, and the Vercel import. The `.dockerignore`
leak test in step 0 has also not been run here — it only matters if you build
locally.

---

## Appendix: the CLI equivalents

You do not need these to deploy, but `fly logs` and `fly status` have no useful
dashboard equivalent, so they are the debugging path when an Actions run fails.

```bash
brew install flyctl && fly auth login

fly apps create kicad-mitos-api --org personal
fly deploy --remote-only                    # reads fly.toml, creates the volume
fly status --app kicad-mitos-api            # must show exactly ONE machine
fly logs --app kicad-mitos-api              # live tail
fly secrets set --app kicad-mitos-api KICAD_MITOS_CORS_ORIGINS='["https://...vercel.app"]'
fly volumes list --app kicad-mitos-api
fly volumes extend <id> --size 10
```

Do **not** run `fly launch` — it prompts interactively and rewrites
[`fly.toml`](fly.toml), undoing the settings that keep this app to one machine.
