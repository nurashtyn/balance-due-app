import re
import uuid
from flask import Flask, request, render_template_string
import fitz  # PyMuPDF

app = Flask(__name__)

BALANCE_DUE_REGEX = re.compile(
    r"\bBalance\s+due\b\s*:\s*\$?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)",
    re.IGNORECASE
)

# Tries to capture the settlement table subtotal line like:
# "Subtotal: 524 2427 ..."
# where 524 = Empty, 2427 = Loaded
SUBTOTAL_EMPTY_LOADED_REGEX = re.compile(
    r"\bSubtotal\s*:\s*([0-9]{1,6})\s+([0-9]{1,6})\b",
    re.IGNORECASE
)

# Captures rows like:
# 36088 12/15/25 12/16/25 ...
# to pull Pickup dates (first + last)
LOAD_ROW_PICKUP_DELIVERY_REGEX = re.compile(
    r"\b\d{4,6}\s+(?P<pickup>\d{2}/\d{2}/\d{2})\s+(?P<delivery>\d{2}/\d{2}/\d{2})\b"
)

# Captures the Tolls box subtotal, e.g. "Tolls ...5809 ... Subtotal -$124.55"
TOLLS_SUBTOTAL_REGEX = re.compile(
    r"\bTolls\b.*?\bSubtotal\b\s*(\(?-?\$?\s*[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?\)?)",
    re.IGNORECASE
)

# Store uploaded PDFs in memory so files persist when switching buttons
UPLOAD_STORE = {}  # upload_id -> list[{"name": str, "bytes": bytes}]

HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Balance Due Sum</title>
  <style>
    body { font-family: system-ui, Arial; max-width: 900px; margin: 40px auto; padding: 0 16px; }
    .card { border: 1px solid #ddd; border-radius: 14px; padding: 18px; margin-top: 18px; }
    .muted { color: #666; }
    button { padding: 10px 16px; border-radius: 10px; border: 1px solid #222; background:#111; color:#fff; cursor:pointer; }
    input { margin-top: 10px; }
    table { width:100%; border-collapse: collapse; margin-top: 12px; }
    td, th { border-bottom: 1px solid #eee; text-align:left; padding: 10px 6px; }
    .bad { color: #b00020; }
    .good { color: #0a7a3b; }
    .btnrow { display:flex; gap: 10px; flex-wrap: wrap; }
    .tablewrap { overflow-x:auto; -webkit-overflow-scrolling: touch; }

    /* New: remove button style + header row for file picker */
    .toprow { display:flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
    .danger { background:#b00020; border-color:#b00020; }

    /* Mobile friendly */
    @media (max-width: 560px) {
      body { margin: 18px auto; padding: 0 12px; }
      h1 { font-size: 22px; }
      .card { padding: 14px; }
      button { width: 100%; }
      td, th { padding: 10px 6px; font-size: 14px; }
    }
  </style>
</head>
<body>
  <h1>TruckTotals</h1>
  <p class="muted">This is a website made specifically for truck drivers that can save tons of time</p>

  <div class="card">
    <form method="post" enctype="multipart/form-data">
      <input type="hidden" name="upload_id" id="uploadId" value="{{ upload_id or '' }}">
      <input type="hidden" name="removed" id="removedFlag" value="0">

      <div class="toprow">
        <label><b>Select PDFs:</b></label>
        <button type="button" class="danger" id="removeBtn">Remove files</button>
      </div>

      <input id="pdfInput" type="file" name="pdfs" accept="application/pdf" multiple required>
      <br><br>
      <div class="btnrow">
        <button type="submit" name="action" value="balance">Total gross</button>
        <button type="submit" name="action" value="miles">Total Miles Driven</button>
        <button type="submit" name="action" value="tolls">Tolls</button>
      </div>
    </form>
  </div>

  {% if show_no_files %}
    <div class="card">
      <p class="bad"><b>No files selected.</b></p>
    </div>
  {% endif %}

  {% if results is not none %}
    <div class="card" id="resultsCard">
      <h2>Results</h2>
      <div class="tablewrap">
        <table>
          <tr>
            <th>File</th>
            <th>Date</th>
            <th>
              {% if mode == 'miles' %}
                Miles driven
              {% elif mode == 'tolls' %}
                Tolls
              {% else %}
                Balance due
              {% endif %}
            </th>
          </tr>
          {% for r in results %}
            <tr>
              <td>{{ r.name }}</td>
              <td class="{{ 'good' if r.daterange is not none else 'bad' }}">
                {% if r.daterange is not none %}{{ r.daterange }}{% else %}NOT FOUND{% endif %}
              </td>
              <td class="{{ 'good' if r.amount is not none else 'bad' }}">
                {% if r.amount is not none %}
                  {% if mode == 'miles' %}
                    {{ "{:,}".format(r.amount|int) }}
                  {% else %}
                    ${{ "{:,.2f}".format(r.amount) }}
                  {% endif %}
                {% else %}
                  NOT FOUND
                {% endif %}
              </td>
            </tr>
          {% endfor %}
        </table>
      </div>

      {% if mode == 'miles' %}
        <h3>Total miles: {{ "{:,}".format(total|int) }}</h3>
        {% if missing > 0 %}
          <p class="bad">Warning: miles (Empty + Loaded) were not found in {{ missing }} file(s).</p>
        {% endif %}
      {% elif mode == 'tolls' %}
        <h3>Total tolls: ${{ "{:,.2f}".format(total) }}</h3>
        {% if missing > 0 %}
          <p class="bad">Warning: tolls subtotal was not found in {{ missing }} file(s).</p>
        {% endif %}
      {% else %}
        <h3>Total gross: ${{ "{:,.2f}".format(total) }}</h3>
        {% if missing > 0 %}
          <p class="bad">Warning: “Balance due” was not found in {{ missing }} file(s).</p>
        {% endif %}
      {% endif %}
    </div>
  {% endif %}

  <script>
    // If we already have uploaded files on the server, don't force user to re-pick files
    (function () {
      const uploadId = document.getElementById("uploadId").value;
      const input = document.getElementById("pdfInput");
      if (uploadId) input.required = false;
    })();

    // Remove files ONLY when this button is pressed:
    // - clears selected files
    // - clears server-stored upload id
    // - hides Results box immediately
    // - shows "No files selected." only after pressing this button (server render)
    document.getElementById("removeBtn").addEventListener("click", function () {
      const input = document.getElementById("pdfInput");
      const uploadId = document.getElementById("uploadId");
      const removed = document.getElementById("removedFlag");
      const resultsCard = document.getElementById("resultsCard");

      input.value = "";
      uploadId.value = "";
      removed.value = "1";
      input.required = true;

      if (resultsCard) resultsCard.remove();
    });
  </script>
</body>
</html>
"""

def money_to_float(s: str) -> float:
    return float(s.replace(",", ""))

def extract_text_from_bytes(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text_parts = []
    for page in doc:
        text_parts.append(page.get_text("text"))
    doc.close()
    # Normalize whitespace so regex is more reliable
    return " ".join(" ".join(text_parts).split())

def extract_pickup_date_range_from_bytes(pdf_bytes: bytes):
    text = extract_text_from_bytes(pdf_bytes)
    pickups = [m.group("pickup") for m in LOAD_ROW_PICKUP_DELIVERY_REGEX.finditer(text)]
    if not pickups:
        return None
    return f"{pickups[0]} to {pickups[-1]}"

def extract_balance_due_from_bytes(pdf_bytes: bytes):
    text = extract_text_from_bytes(pdf_bytes)
    m = BALANCE_DUE_REGEX.search(text)
    if not m:
        return None
    return money_to_float(m.group(1))

def extract_total_miles_from_bytes(pdf_bytes: bytes):
    """
    Sums the settlement's 'Empty' + 'Loaded' miles.
    Best-effort approach: uses the 'Subtotal:' line (common in these PDFs).
    """
    text = extract_text_from_bytes(pdf_bytes)

    m = SUBTOTAL_EMPTY_LOADED_REGEX.search(text)
    if not m:
        return None

    empty_miles = int(m.group(1))
    loaded_miles = int(m.group(2))
    return empty_miles + loaded_miles

def signed_money_to_float(s: str) -> float:
    s = s.strip().replace(" ", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    s = s.replace("$", "").replace(",", "")
    return float(s)

def extract_tolls_subtotal_from_bytes(pdf_bytes: bytes):
    text = extract_text_from_bytes(pdf_bytes)
    m = TOLLS_SUBTOTAL_REGEX.search(text)
    if not m:
        return None
    return signed_money_to_float(m.group(1))

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template_string(HTML, results=None, upload_id="", show_no_files=False)

    action = request.form.get("action", "balance")  # "balance" or "miles" or "tolls"
    upload_id = (request.form.get("upload_id") or "").strip()
    removed_flag = (request.form.get("removed") or "0").strip() == "1"

    files = request.files.getlist("pdfs")

    # Detect whether user actually selected files this submit
    selected_files = []
    for f in files:
        if f and getattr(f, "filename", ""):
            name = f.filename.strip()
            if name:
                selected_files.append({"name": name, "bytes": f.read()})

    # If new files were selected, store them (new upload_id if needed)
    if selected_files:
        if not upload_id:
            upload_id = uuid.uuid4().hex
        UPLOAD_STORE[upload_id] = selected_files

    # If the user pressed "Remove files", drop the stored set
    if removed_flag:
        if upload_id and upload_id in UPLOAD_STORE:
            del UPLOAD_STORE[upload_id]
        upload_id = ""
        return render_template_string(HTML, results=None, upload_id="", show_no_files=True)

    # Otherwise, reuse previous uploaded set (so files "stay" when switching buttons)
    stored = UPLOAD_STORE.get(upload_id, []) if upload_id else []

    # Do NOT show "No files selected" when switching between buttons
    if not stored and not selected_files:
        return render_template_string(HTML, results=None, upload_id=upload_id, show_no_files=False)

    results = []
    total = 0.0
    missing = 0

    for item in stored:
        name = item["name"]
        data = item["bytes"]
        daterange = extract_pickup_date_range_from_bytes(data)

        if action == "miles":
            amt = extract_total_miles_from_bytes(data)
            results.append({"name": name, "daterange": daterange, "amount": amt})
            if amt is None:
                missing += 1
            else:
                total += float(amt)  # keep total numeric for template
        elif action == "tolls":
            amt = extract_tolls_subtotal_from_bytes(data)
            results.append({"name": name, "daterange": daterange, "amount": amt})
            if amt is None:
                missing += 1
            else:
                total += amt
        else:
            amt = extract_balance_due_from_bytes(data)
            results.append({"name": name, "daterange": daterange, "amount": amt})
            if amt is None:
                missing += 1
            else:
                total += amt

    return render_template_string(
        HTML,
        results=results,
        total=total,
        missing=missing,
        mode=("tolls" if action == "tolls" else ("miles" if action == "miles" else "balance")),
        upload_id=upload_id,
        show_no_files=False,
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
