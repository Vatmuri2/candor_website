# Deploying the Candor interview web app

This app is a **stateful, long-running Flask service** (in-memory sessions +
per-session background asyncio loops + a FAISS memory bank on disk). That means
it needs a **persistent container host**, not a serverless platform like Vercel.
These instructions use **Render**, which deploys straight from a GitHub repo and
gives you a persistent disk. (Google Cloud Run / Railway / Fly.io work too — the
`Dockerfile` is generic.)

The user flow: visitor lands on `/` → picks a **conversation type** → chats with
the AI interviewer. The OpenAI key and all storage stay server-side.

---

## 1. Push the code to the `candor_website` repo

```bash
# from the project root
git remote add candor_website https://github.com/<your-org-or-user>/candor_website.git
git push -u candor_website main
```

If the remote already has commits, either push to a fresh empty repo or
`git push -u candor_website main --force` to overwrite it (careful — this
replaces its history).

---

## 2. Deploy on Render

1. Go to <https://dashboard.render.com> → **New +** → **Blueprint**.
2. Connect GitHub and pick the **candor_website** repo.
3. Render reads [`render.yaml`](render.yaml) and provisions a Docker web service
   named `candor-website` on the **free** plan (no payment info required).
4. Before the first deploy finishes, set the secret env vars (they're marked
   `sync: false`, so Render prompts you):
   - **`OPENAI_API_KEY`** — your OpenAI key (required).
   - `FLASK_SECRET_KEY` — leave it; Render auto-generates one.
   - `GOOGLE_SERVICE_ACCOUNT_JSON` / `GDRIVE_FOLDER_ID` — optional, see §3.
5. Deploy. When it's live, open the service URL — you'll land on the
   conversation picker. `<url>/health` should return `{"status": "healthy"}`.

> **Plan note:** `render.yaml` uses the **free** plan (no payment info needed).
> The free plan has **no persistent disk**, so saved interview data is wiped on
> redeploy and the service **sleeps after ~15 min idle** (first visitor then
> waits ~30-60s for wake-up). For always-on + durable storage, change
> `plan: free` → `plan: starter` and add a `disk:` block mounted at `/var/data`
> (this requires payment info). Either way, keep **one instance / one worker** —
> sessions live in memory, so multiple instances would break them.

---

## 3. (Optional) Archive finished interviews to Google Drive

Each finished interview is uploaded as a timestamped `.zip`. Two auth options —
**OAuth is recommended** for a personal / edu account, because uploaded files are
owned by *your* account and use its real storage quota. (A service account has
**no storage quota** and can only upload into a *Shared Drive*, so it fails with
`storageQuotaExceeded` when pointed at a normal My Drive folder.)

Common setup:
- In Google Cloud Console → **APIs & Services** → enable the **Google Drive API**.
- In Drive, create a folder; its id is the last part of the URL:
  `drive.google.com/drive/folders/`**`<GDRIVE_FOLDER_ID>`**.

### Option A — OAuth (recommended)
1. **APIs & Services → Credentials → Create credentials → OAuth client ID.**
   If prompted, configure the consent screen (**External**, add your email as a
   **Test user**). Application type: **Desktop app**. Download the client secret JSON.
2. On your laptop, run the one-time helper (opens a browser — log in with the
   account whose Drive should own the files):
   ```bash
   python scripts/get_oauth_token.py /path/to/client_secret.json
   ```
3. It prints three values. In Render set them plus the folder id:
   - `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`
   - `GDRIVE_FOLDER_ID`

### Option B — Service account (only with a Shared Drive)
1. **Credentials → Create credentials → Service account**; download its JSON key.
2. Create a **Shared Drive** (Team Drive), add the account's `client_email` as
   **Content manager**, and use a folder inside it for `GDRIVE_FOLDER_ID`.
3. In Render set `GOOGLE_SERVICE_ACCOUNT_JSON` (entire JSON) and `GDRIVE_FOLDER_ID`.

Either way: if the vars are unset or wrong, upload is skipped and data still
stays on disk — it never breaks an interview. Verify a setup with
`python scripts/test_drive_upload.py <key.json> <folder_id>` (service account) or
by finishing a test interview.

---

## 4. Adding or editing conversation types

Presets live in `CONVERSATION_TYPES` in [`src/main_flask.py`](src/main_flask.py).
Each points at a topic-plan JSON in `data/configs/` (same shape as `topics.json`).
Add an entry + a JSON file, redeploy, and the new card appears on the landing
page automatically. `custom` lets users type their own topic (no file needed).

---

## Local run

```bash
pip install -r requirements.txt
cp .env_sample .env   # then set OPENAI_API_KEY and the dirs
python -m src.main_flask --port 8080
# open http://localhost:8080
```
