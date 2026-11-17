from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Depends
from google import genai
from google.genai import types
from dotenv import load_dotenv
from pydantic import BaseModel
from database import engine, init_db, Receipt, ScanLog, User
from auth import hash_password, verify_password, create_token, decode_token
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
from database import engine, init_db, Receipt, ScanLog, User, utc_now

N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL")

load_dotenv()
key = os.getenv("GEMINI_API_KEY")

client = genai.Client(
    api_key=key,
    http_options=types.HttpOptions(timeout=60000),
)

MODELS = ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.1-flash-lite"]
ATTEMPTS_PER_MODEL = 2
RETRYABLE_CODES = {500, 503, 504}
PLACEHOLDERS = {"", "n/a", "na", "none", "unknown", "null", "-"}
DAILY_CALL_LIMIT = int(os.getenv("DAILY_CALL_LIMIT", "20"))

app = FastAPI()


class ReceiptData(BaseModel):
    vendor: str
    date: str
    total: float

class AuthRequest(BaseModel):
    email: str
    password: str    

def get_current_user(authorization: Optional[str] = Header(default=None)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    token = authorization.removeprefix("Bearer ").strip()
    user_id = decode_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Missing or invalid token")
        return user

def check_and_increment_calls(user_id: int):
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Missing or invalid token")

        now = utc_now()
        if user.calls_reset_at.date() < now.date():
            user.daily_calls = 0
            user.calls_reset_at = now

        if user.daily_calls >= DAILY_CALL_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=f"Daily limit of {DAILY_CALL_LIMIT} AI calls reached. Resets tomorrow.",
            )

        user.daily_calls += 1
        session.add(user)
        session.commit()        

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
async def scan_receipt(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    check_and_increment_calls(user.id)
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
            session.add(ScanLog(status="success", duration_seconds=duration, user_id=user.id))
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
            session.add(ScanLog(status="failed", error_message=str(e), duration_seconds=duration, user_id=user.id))
            session.commit()
        raise HTTPException(status_code=502, detail=str(e))

class ConfirmReceipt(BaseModel):
    vendor: str
    date: str
    total: float
    needs_review: bool = False
    review_reason: str | None = None


@app.post("/receipts")
def confirm_receipt(data: ConfirmReceipt, user: User = Depends(get_current_user)):
    with Session(engine) as session:
        receipt = Receipt(
            user_id=user.id,
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
def list_receipts(user: User = Depends(get_current_user)):
    with Session(engine) as session:
        receipts = session.exec(
            select(Receipt)
            .where(Receipt.user_id == user.id)
            .order_by(Receipt.created_at.desc())
        ).all()
        return receipts

@app.get("/receipts/export")
def export_receipts(user: User = Depends(get_current_user)):
    with Session(engine) as session:
        receipts = session.exec(
            select(Receipt)
            .where(Receipt.user_id == user.id)
            .order_by(Receipt.created_at.desc())
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

@app.post("/register")
def register(data: AuthRequest):
    with Session(engine) as session:
        existing = session.exec(select(User).where(User.email == data.email)).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

        user = User(email=data.email, password_hash=hash_password(data.password))
        session.add(user)
        session.commit()
        session.refresh(user)

        return {"token": create_token(user.id)}


@app.post("/login")
def login(data: AuthRequest):
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == data.email)).first()
        if not user or not verify_password(data.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        return {"token": create_token(user.id)}    
