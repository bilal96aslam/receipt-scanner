# Receipt Scanner — AI-Powered Receipt Extraction API

An AI backend that extracts structured data (vendor, date, total) from photographed receipts using Google Gemini's vision API, with a save-on-confirm workflow, automatic validation, and a Google Sheets automation pipeline via n8n.

**Live API:** https://receipt-scanner-uk8c.onrender.com

**API docs:** https://receipt-scanner-uk8c.onrender.com/docs

**Mobile app repo:** https://github.com/bilal96aslam/ReceiptScanner

## What it does

1. A user photographs a receipt in the companion mobile app (Android/iOS, built with Compose Multiplatform).
2. The image is sent to this backend, which calls Gemini's vision API to extract the vendor, date, and total as structured JSON.
3. The user reviews and can edit the extracted data before confirming.
4. On confirm, the receipt is saved to the database — nothing is saved before this step, by design (see "Save-on-confirm" below).
5. A webhook notifies an n8n automation, which appends the confirmed receipt as a row in a Google Sheet.

## Architecture

```
Mobile App (CMP: Android + iOS)
        │
        ▼
FastAPI Backend (Render) ──► Gemini Vision API (extraction)
        │
        ▼
SQLite Database (receipts + scan logs)
        │
        ▼
n8n Webhook (Render) ──► Google Sheets (automation)
```

## Why save-on-confirm

Early versions saved the AI's output immediately on scan. That meant unverified, possibly wrong data entered the database before a human ever looked at it. This was redesigned so `/scan` only returns data for review — nothing is persisted until the user explicitly confirms (or edits and then confirms) via `/receipts`. This is a deliberate data-integrity decision, not an oversight.

## Accuracy

Measured against a 12-receipt human-verified test set, using a repeatable eval script (`run_eval.py`):

| Field  | Accuracy |
|--------|----------|
| Vendor | 12/12 |
| Total  | 10–11/12 |
| Date   | 3–4/12 exact match |

Date extraction has a known limitation — the model occasionally misreads digits in the printed date (e.g., 2025 misread as 2023). Rather than chase this with more prompt engineering, a plausibility check was added to `check_receipt()`: implausible dates (future-dated, or more than 3 years old) are automatically flagged `needs_review`, rather than silently passing as correct. This cut undetected date errors from 9/12 to 3/12 in testing. The remaining misses are near-plausible values (e.g., off by one month) that no simple validation rule can distinguish from a correct date — a real, acknowledged limitation rather than a hidden one.

## Tech stack

- **Backend:** FastAPI, SQLModel (SQLite), Python
- **AI:** Google Gemini (`gemini-3.6-flash` with model-fallback and retry logic for reliability)
- **Automation:** n8n (self-hosted on Render), Google Sheets API
- **Deployment:** Docker, Render (backend and n8n run as two separate Render services)
- **Mobile:** Kotlin Multiplatform / Compose Multiplatform, Ktor client, MVVM

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/scan` | Upload a receipt image, get back extracted data (not saved) |
| POST | `/receipts` | Confirm (and optionally edit) extracted data — this is what actually saves it |
| GET | `/receipts` | List all saved receipts |
| GET | `/receipts/export` | Export all receipts as CSV |
| GET | `/health` | Health check |

All endpoints except `/health` require an `x-api-key` header.

## Running locally

```bash
git clone https://github.com/bilal96aslam/receipt-scanner
cd receipt-scanner
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt --break-system-packages
```

Create a `.env` file:

```
GEMINI_API_KEY=your_key_here
API_SECRET_KEY=your_chosen_secret
N8N_WEBHOOK_URL=your_n8n_webhook_url
```

Run:

```bash
fastapi dev main.py
```

## Running with Docker

```bash
docker build -t receipt-scanner .
docker run -p 8000:8000 --env-file .env receipt-scanner
```

## Testing accuracy

```bash
python3 run_eval.py
```

Compares extraction output against `test_receipts/expected.csv` and reports per-field accuracy, flags, and timing.

## What's not included (by design)

This project intentionally does not include Play Store publishing, closed testing, or store listing assets — the focus was building and deploying a working product end to end, not a public release.
