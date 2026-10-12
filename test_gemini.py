import os, requests
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("GEMINI_API_KEY")
print("Clave cargada:", bool(key), "| empieza con:", key[:3] if key else None)

url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
r = requests.post(
    url,
    headers={"Content-Type": "application/json", "x-goog-api-key": key},
    json={"contents": [{"parts": [{"text": "Decime hola en una frase."}]}]},
    timeout=60,
)
print(r.status_code)
print(r.text[:600])