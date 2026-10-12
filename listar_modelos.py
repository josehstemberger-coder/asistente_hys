import os
import requests
from dotenv import load_dotenv

load_dotenv()
clave = os.getenv("GEMINI_API_KEY")
if not clave:
    raise SystemExit("No encuentro GEMINI_API_KEY en el .env")

url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200"
r = requests.get(url, headers={"x-goog-api-key": clave}, timeout=30)
if r.status_code != 200:
    raise SystemExit(f"Error {r.status_code}: {r.text[:300]}")

print("Modelos que sirven para generar texto:\n")
for m in r.json().get("models", []):
    if "generateContent" in m.get("supportedGenerationMethods", []):
        print(m["name"].replace("models/", ""))