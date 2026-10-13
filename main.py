from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Depends
from google import genai
from google.genai import types
from dotenv import load_dotenv
from pydantic import BaseModel
from database import engine, init_db, Receipt, ScanLog
from datetime import datetime
from typing import Optional
import asyncio
import os
import io
import time
from sqlmodel import Session, select
from fastapi.responses import StreamingResponse
import csv
import io as io_module
import httpx

N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL")

load_dotenv()
key = os.getenv("GEMINI_API_KEY")
API_SECRET_KEY = os.getenv("API_SECRET_KEY")

client = genai.Client(
    api_key=key,
    http_options=types.HttpOptions(timeout=60000),
)

MODELS = ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.1-flash-lite"]
ATTEMPTS_PER_MODEL = 2
RETRYABLE_CODES = {500, 503, 504}
PLACEHOLDERS = {"", "n/a", "na", "none", "unknown", "null", "-"}

app = FastAPI()


class ReceiptData(BaseModel):
    vendor: str
    date: str
    total: float


def require_api_key(x_api_key: Optional[str] = Header(default=None)):
    if not API_SECRET_KEY or x_api_key != API_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def check_receipt(data: ReceiptData):
    reasons = []
    if data.vendor.strip().lower() in PLACEHOLDERS:
        reasons.append("vendor is empty or a placeholder")
    if data.total <= 0:
        reasons.append("total is zero or negative")
    if data.total > 10000:
        reasons.append("total is unusually large")
    try:
        parsed_date = datetime.strptime(data.date, "%Y-%m-%d")
    except ValueError:
        reasons.append("date is not in YYYY-MM-DD format")
    else:
        today = datetime.now()
        if parsed_date > today:
            reasons.append("date is in the future")
        elif (today - parsed_date).days > 365 * 3:
            reasons.append("date is more than 3 years old, check for a misread year")
    return reasons
    return reasons


async def extract_with_retry(uploaded):
    last_error = None
    for model in MODELS:
        for attempt in range(1, ATTEMPTS_PER_MODEL + 1):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[
                        uploaded,
                        "Extract the vendor name, the transaction date, and the total amount from this receipt. "
                        "Read the date character by character — do not guess based on typical formats. "
                        "Return the date as YYYY-MM-DD."
                    ],
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": ReceiptData,
                    },
                )
                return response.parsed
            except Exception as e:
                last_error = e
                code = getattr(e, "code", None)
                if code == 429:
                    print(f"{model} quota exhausted, switching model")
                    break
                if code not in RETRYABLE_CODES:
                    raise
                print(f"{model} attempt {attempt} failed (code {code})")
                if attempt < ATTEMPTS_PER_MODEL:
                    await asyncio.sleep(2 ** attempt)
    raise last_error


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/scan")
async def scan_receipt(file: UploadFile = File(...), _=Depends(require_api_key)):
    start_time = time.time()


    try:
        image_bytes = await file.read()

        uploaded = client.files.upload(
            file=io.BytesIO(image_bytes),
            config={"mime_type": file.content_type},
        )

        result = await extract_with_retry(uploaded)

        if result is None:
            raise ValueError("AI reply did not match the expected format")

        reasons = check_receipt(result)
        needs_review = len(reasons) > 0
        review_reason = "; ".join(reasons) if needs_review else None

        duration = time.time() - start_time

        with Session(engine) as session:
            session.add(ScanLog(status="success", duration_seconds=duration))
            session.commit()

        return {
            "id": None,
            "vendor": result.vendor,
            "date": result.date,
            "total": result.total,
            "needs_review": needs_review,
            "review_reason": review_reason,
        }

    except Exception as e:
        duration = time.time() - start_time
        with Session(engine) as session:
            session.add(ScanLog(status="failed", error_message=str(e), duration_seconds=duration))
            session.commit()
        raise HTTPException(status_code=502, detail=str(e))

class ConfirmReceipt(BaseModel):
    vendor: str
    date: str
    total: float
    needs_review: bool = False
    review_reason: str | None = None


@app.post("/receipts")
def confirm_receipt(data: ConfirmReceipt, _=Depends(require_api_key)):
    with Session(engine) as session:
        receipt = Receipt(
            vendor=data.vendor,
            date=data.date,
            total=data.total,
            needs_review=data.needs_review,
            review_reason=data.review_reason,
        )
        session.add(receipt)
        session.commit()
        session.refresh(receipt)
        if N8N_WEBHOOK_URL:
            try:
                httpx.post(N8N_WEBHOOK_URL, json={
                    "vendor": receipt.vendor,
                    "date": receipt.date,
                    "total": receipt.total,
                    "needs_review": receipt.needs_review,
                }, timeout=10)
            except Exception as e:
                print(f"n8n webhook failed: {e}")
        return receipt


@app.get("/receipts")
def list_receipts(_=Depends(require_api_key)):
    with Session(engine) as session:
        receipts = session.exec(
            select(Receipt).order_by(Receipt.created_at.desc())
        ).all()
        return receipts

@app.get("/receipts/export")
def export_receipts(_=Depends(require_api_key)):
    with Session(engine) as session:
        receipts = session.exec(
            select(Receipt).order_by(Receipt.created_at.desc())
        ).all()

    output = io_module.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Vendor", "Date", "Total", "Needs Review", "Review Reason", "Created At"])
    for r in receipts:
        writer.writerow([r.id, r.vendor, r.date, r.total, r.needs_review, r.review_reason or "", r.created_at])

    output.seek(0)
    return StreamingResponse(
        io_module.StringIO(output.getvalue()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=receipts.csv"}
    )
