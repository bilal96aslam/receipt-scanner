import csv
from datetime import datetime
import mimetypes
import time
from pathlib import Path

import httpx

API_URL = "http://127.0.0.1:8000/scan"
FOLDER = Path("test_receipts")


def norm(text):
    return str(text).strip().lower()


with open(FOLDER / "expected.csv", newline="") as f:
    expected_rows = list(csv.DictReader(f))

results = []

for row in expected_rows:
    path = FOLDER / row["filename"]
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    start = time.time()

    try:
        with open(path, "rb") as image:
            response = httpx.post(
                API_URL,
                files={"file": (path.name, image, mime)},
                timeout=180,
            )
    except Exception as e:
        results.append({"filename": row["filename"], "status": "error", "detail": str(e)})
        print(row["filename"], "ERROR (could not reach API)")
        continue

    seconds = round(time.time() - start, 1)

    if response.status_code != 200:
        results.append({"filename": row["filename"], "status": "error",
                        "detail": f"HTTP {response.status_code}", "seconds": seconds})
        print(row["filename"], "ERROR", response.status_code)
        continue

    data = response.json()
    vendor_ok = norm(data["vendor"]) == norm(row["vendor"])
    date_ok = norm(data["date"]) == norm(row["date"])
    total_ok = abs(float(data["total"]) - float(row["total"])) < 0.01
    all_ok = vendor_ok and date_ok and total_ok

    results.append({
        "filename": row["filename"],
        "status": "ok",
        "vendor_ok": vendor_ok,
        "date_ok": date_ok,
        "total_ok": total_ok,
        "all_ok": all_ok,
        "needs_review": data["needs_review"],
        "got_vendor": data["vendor"],
        "got_date": data["date"],
        "got_total": data["total"],
        "expected_vendor": row["vendor"],
        "expected_date": row["date"],
        "expected_total": row["total"],
        "seconds": seconds,
    })
    print(row["filename"], "correct" if all_ok else "MISMATCH", f"({seconds}s)")
    time.sleep(2)

ok_results = [r for r in results if r["status"] == "ok"]
n = len(ok_results)

if n:
    silent_errors = [r for r in ok_results if not r["all_ok"] and not r["needs_review"]]
    print()
    print("===== SUMMARY =====")
    print(f"Receipts tested:        {len(results)}")
    print(f"API errors:             {len(results) - n}")
    print(f"Fully correct:          {sum(r['all_ok'] for r in ok_results)} of {n}")
    print(f"Vendor correct:         {sum(r['vendor_ok'] for r in ok_results)} of {n}")
    print(f"Date correct:           {sum(r['date_ok'] for r in ok_results)} of {n}")
    print(f"Total correct:          {sum(r['total_ok'] for r in ok_results)} of {n}")
    print(f"Flagged for review:     {sum(r['needs_review'] for r in ok_results)}")
    print(f"Wrong AND not flagged:  {len(silent_errors)}  <- the dangerous ones")
    print(f"Average time:           {round(sum(r['seconds'] for r in ok_results) / n, 1)}s")

fields = sorted({key for r in results for key in r})
RESULTS_FILE = "results_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"
with open(RESULTS_FILE, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(results)
print("\nDetails saved to", RESULTS_FILE)
