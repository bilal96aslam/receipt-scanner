from google import genai
from dotenv import load_dotenv
import os

load_dotenv() # load the environment variables from the .env file

key = os.getenv("GEMINI_API_KEY")
print("Key loaded:", bool(key))

client = genai.Client(api_key=key)

response = client.models.generate_content(
    model="gemini-3.6-flash",
    contents="Say hello and tell me one fact about receipts."
)

print("Response text:", response.text)
