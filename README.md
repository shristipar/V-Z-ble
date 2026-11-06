# V-Z-ble

Scores a software engineer's online presence in five categories and ranks the fixes by how many points each would add.

## Run

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python server.py
```

Then open http://localhost:8765.

## Adding your profiles

The left panel is a list of links. Each row has a platform dropdown and your handle; **+ Add profile** adds a row with the next unused platform, and × removes one. Each platform can be added once (platforms already in the list are greyed out in the other dropdowns). You can paste a full profile URL or just the handle. The list is remembered in this browser.

| Platform | How it's used |
|---|---|
| GitHub, dev.to, Stack Overflow | Fetched live from their public APIs |
| Medium, Substack | Feeds fetched live through `server.py` |
| LinkedIn | Read by the browser extension (below); the row shows the import status |
| YouTube, X (Twitter), Website | Shown as links under your score; a website also counts toward Consistency |

Community checkboxes (talks, meetups, mentoring) are under **More about you**; Consistency is checked automatically (below). The AI text review sits below the report.

## LinkedIn import (browser extension)

LinkedIn has no public API for profiles, and a web page can't read another site, so a small Chrome extension in `extension/` reads your profile while you're logged in and sends it to the local server.

Install once:

1. Open `chrome://extensions` and turn on **Developer mode** (top right).
2. Click **Load unpacked** and choose the `extension/` folder.

Use:

1. Start `server.py` and open http://localhost:8765.
2. Open your own LinkedIn profile (linkedin.com/in/me) and click **Send to V-Z-ble** in the bottom-right corner. It scrolls the page once so LinkedIn loads every section, then sends it.
3. The V-Z-ble tab fills in your headline, About and Experience for the AI review and scores the Professional (LinkedIn) category from the import: photo, headline (more than a bare "Title at Company"), About length, numbers in experience, skills, Featured, 3+ recommendations, a post in the last 30 days (from the Activity section), and a custom URL. There are no LinkedIn checkboxes; until you import, the category scores 0.

The profile is saved under `data/linkedin/` (git-ignored), one file per browser; only the latest import is kept. The extension only sends to the V-Z-ble addresses in `APP_ORIGINS` (`extension/background.js`): `localhost:8765` and your Cloud Run site. If you click the button on someone else's profile it warns you first. LinkedIn's page markup changes often; the extension also sends the page's full text (`raw`), so if a section stops being detected, update the selectors in `extension/extract.js`.

## Medium and Substack

Browsers can't read these feeds directly, so `server.py` fetches them and returns each post's title, link, date and tags:

- **Medium:** `GET /api/medium?user=you` reads `https://medium.com/feed/@you`. Medium's feed only lists your latest 10 posts and has no clap counts, so 10 posts in a year shows as "10+"; engagement (claps, reactions) isn't scored. Posts on a publication's custom domain only appear if they're also in your personal feed.
- **Substack:** `GET /api/substack?user=you` reads `https://you.substack.com/feed`. A custom domain (`yournewsletter.com`) works too.

## GitHub rate limit

The audit only needs a GitHub username, since everything it reads is public. Without a token, GitHub allows 60 requests per hour per IP address, and one audit uses about 5 (roughly 12 audits an hour). If that becomes a problem (for example, several people sharing one server), add a GitHub token: create a fine-grained personal access token with no extra permissions, have `server.py` read it from a `GITHUB_TOKEN` environment variable, and make the GitHub calls from the server with an `Authorization: Bearer` header. That raises the limit to 5,000 requests per hour and keeps the token out of the browser. A token also unlocks the GraphQL contribution calendar (see Roadmap ideas).

## AI text review

`server.py` serves the page and exposes `POST /api/review`. It reviews the LinkedIn headline, About and Experience, the GitHub profile README and post titles, and returns a structured review: a 1–10 score, strengths, problems and rewrites for each section. For Experience it rewrites the weakest 3–5 bullets as strong verb + what changed + measurable result. Rewrites use only facts you provide; missing numbers come back as `[placeholders]`. The audit prefills the README from GitHub and post titles from Medium and dev.to (newest first); the LinkedIn import prefills headline, About and Experience.

Two ways the review runs:

- **With `ANTHROPIC_API_KEY`:** the server calls Claude (`claude-opus-5-5`) directly. The key stays on the server.
- **Without a key (agent mode):** the server saves the request to `reviews/<id>.request.json` (prompt, reviewer instructions and output JSON schema) and the page waits. Ask Claude Code to "run the queued V-Z-ble review"; an agent writes `reviews/<id>.result.json`, the server validates it against the schema, and the page shows it. `GET /api/review/<id>` returns 202 while pending.

## Share your score

Below "What to do next", the report draws a 1200×630 score card (the size LinkedIn and X use for previews): your score, verdict, category bars and biggest gap, in the app's dark palette. You can:

- **Download image** or **Copy image**, then paste it into a post (LinkedIn and X don't attach images from links).
- **Copy post text**, or open **Post on LinkedIn** / **Post on X** with the text filled in.
- Untick **Show my name on the card** to post it anonymously ("My online presence"). Only your first name is ever shown.

When hosted, the card and post text show the site's address (`PUBLIC_URL`, set by `deploy.sh`), so each shared card links back to the free audit.

## Deploy to Google Cloud Run

`deploy.sh` builds the `Dockerfile` with Cloud Build and deploys it to Cloud Run with safe defaults: at most 1 instance (caps cost and keeps the daily AI review limit accurate), scales to zero when idle, 512 MB, and the Anthropic API key in Secret Manager.

1. Install the Google Cloud CLI (https://cloud.google.com/sdk/docs/install), create a project at console.cloud.google.com, and attach a billing account (needed even for the free tier).
2. Run:

   ```
   ./deploy.sh
   ```

   It signs you in if needed, asks for the project ID, turns on the needed services, asks for your Anthropic API key (hidden; press Enter to keep the AI review off), deploys, and prints the address (`https://v-z-ble-….run.app`). Run the same command to deploy updates.
3. Optional budget alert (recommended, since Google Cloud has no hard spending cap): run the first deploy as `BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX ./deploy.sh` to get emails at $1, $2.50 and $5 a month. Find the ID with `gcloud billing accounts list`.
4. The live site is https://v-z-ble-qukdgtc2ca-uc.a.run.app and is already in the extension. For a new address, put it in `extension/manifest.json` (`matches` and `host_permissions`) and in `APP_ORIGINS` in `extension/background.js`, then reload the extension. With a custom domain, use that instead and deploy with `PUBLIC_URL=https://yourdomain ./deploy.sh`.

Cloud Run has no disk, so the server only holds each visitor's LinkedIn import briefly (in `/tmp`); the page keeps its own copy in the visitor's browser, so restarts don't lose it. Hosted mode (`HOSTED=1`) also serves only the page and the API, stores imports per visitor (keyed by a random visitor ID kept in their browser), and turns off the local agent review queue. Daily review counts are kept in memory and reset when the instance restarts.

**Extension pairing:** opening the V-Z-ble site with the extension installed pairs them automatically (`extension/pair.js` reads the visitor ID); after that, "Send to V-Z-ble" on LinkedIn sends to that site. The extension only sends to the addresses listed in `APP_ORIGINS`.

## Consistency checks

Consistency is checked automatically from the profiles in your links:

- **Same handle:** compares your handles (GitHub, LinkedIn, Medium, Substack, dev.to, YouTube, X), ignoring case and punctuation.
- **Same name:** compares the display names each platform returns (GitHub, the LinkedIn import, Medium, Substack, dev.to, Stack Overflow) by first and last name, ignoring case, accents and middle names.
- **Same photo:** `server.py` fingerprints each profile photo at `GET /api/photo-hash?url=…` (a 64-bit difference hash, which needs Pillow) and treats photos within 10 bits of each other as the same picture, so resized copies still match. It only fetches from known profile-photo hosts. LinkedIn photo links expire, so the photo is fingerprinted when you import and only the fingerprint is kept.

Each check scores the share of profiles in the biggest matching group, and the advice names the platforms that differ. A check needs at least two profiles to compare. "Googling your name finds you" isn't checked: it would need scraping Google or a paid search API.

## How scoring works

Each category is a list of checks: `{ label, pts, got (0..1), advice }`. A category score is the points earned divided by the points available. The overall score is a weighted sum of the categories, and the weights come from your goals (`WEIGHTS` in `index.html`). Pick one or more goal chips; with several, each category's weight is the average across them, and the AI review is told all of them.

| Category | Source | Main signals |
|---|---|---|
| Open source | GitHub API (live) | profile fields, profile README, followers, stars, repo descriptions, active days (90d), PRs to others' repos |
| Writing | Medium and Substack feeds via `server.py` + dev.to API (all live) | posts in last 12 months, recency |
| Professional | LinkedIn import (browser extension) | photo, headline, About, quantified bullets, skills, Featured, recommendations, recent posts, custom URL |
| Community | Stack Exchange API (live) + checklist | SO reputation, talks, community activity, mentoring |
| Consistency | GitHub profile, Website link, and handles, names and photos from every profile (automatic) | cross-links, personal site, same handle, same name, same photo |

Recommendations come from every unmet check, ranked by `(1 - got) * pts / categoryTotal * 100 * weight`.

## Roadmap ideas

- LinkedIn: let users upload their LinkedIn data export (Settings > Get a copy of your data) or a profile PDF and parse it, instead of the checklist.
- YouTube: score channel activity (needs a YouTube Data API key on the server); for now it's a link.
- Save audits over time to show progress.
- GitHub GraphQL API (needs a token) for the full contribution calendar instead of the last ~300 public events.
