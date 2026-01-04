import re
from flask import Flask, request, render_template_string
import fitz  # PyMuPDF

app = Flask(__name__)

BALANCE_DUE_REGEX = re.compile(
    r"\bBalance\s+due\b\s*:\s*\$?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)",
    re.IGNORECASE
)

HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
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
  </style>
</head>
<body>
  <h1>Sum “Balance due” from Paycheck PDFs</h1>
  <p class="muted">Upload PDFs (multiple allowed). The server will extract only the value next to <b>Balance due</b>.</p>

  <div class="card">
    <form method="post" enctype="multipart/form-data">
      <label><b>Select PDFs:</b></label><br>
      <input type="file" name="pdfs" accept="application/pdf" multiple required>
      <br><br>
      <button type="submit">Calculate Total</button>
    </form>
  </div>

  {% if results is not none %}
    <div class="card">
      <h2>Results</h2>
      <table>
        <tr><th>File</th><th>Balance due</th></tr>
        {% for r in results %}
          <tr>
            <td>{{ r.name }}</td>
            <td class="{{ 'good' if r.amount is not none else 'bad' }}">
              {% if r.amount is not none %}${{ "{:,.2f}".format(r.amount) }}{% else %}NOT FOUND{% endif %}
            </td>
          </tr>
        {% endfor %}
      </table>
      <h3>Total: ${{ "{:,.2f}".format(total) }}</h3>
      {% if missing > 0 %}
        <p class="bad">Warning: “Balance due” was not found in {{ missing }} file(s).</p>
      {% endif %}
    </div>
  {% endif %}
</body>
</html>
"""

def money_to_float(s: str) -> float:
    return float(s.replace(",", ""))

def extract_balance_due_from_bytes(pdf_bytes: bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text_parts = []
    for page in doc:
        text_parts.append(page.get_text("text"))
    doc.close()
    text = " ".join(" ".join(text_parts).split())
    m = BALANCE_DUE_REGEX.search(text)
    if not m:
        return None
    return money_to_float(m.group(1))

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template_string(HTML, results=None)

    files = request.files.getlist("pdfs")
    results = []
    total = 0.0
    missing = 0

    for f in files:
        data = f.read()
        amt = extract_balance_due_from_bytes(data)
        results.append({"name": f.filename, "amount": amt})
        if amt is None:
            missing += 1
        else:
            total += amt

    return render_template_string(HTML, results=results, total=total, missing=missing)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
