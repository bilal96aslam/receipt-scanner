from google import genai
from dotenv import load_dotenv
from pydantic import BaseModel
import os

load_dotenv()
key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=key)

# This defines the exact shape we want back
class Receipt(BaseModel):
    vendor: str
    date: str
    total: float

# Upload the image
image_file = client.files.upload(file="sample_receipt.png")

# Ask Gemini to read it and return that exact shape
response = client.models.generate_content(
    model="gemini-3.6-flash",
    contents=[
        image_file,
        "Extract the vendor name, date, and total amount from this receipt."
    ],
    config={
        "response_mime_type": "application/json",
        "response_schema": Receipt,
    },
)

print("Raw text:", response.text)
print("Parsed:", response.parsed)
print("Vendor:", response.parsed.vendor)
print("Total:", response.parsed.total)