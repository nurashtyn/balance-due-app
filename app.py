import re
import uuid
from io import BytesIO
from datetime import datetime

from flask import Flask, request, render_template_string, send_file
import fitz  # PyMuPDF

# Optional background cleanup (works even if Pillow is NOT installed)
try:
    from PIL import Image
except Exception:
    Image = None

app = Flask(__name__, static_folder="static", static_url_path="/static")

# -----------------------------
# Regex
# -----------------------------
BALANCE_DUE_REGEX = re.compile(
    r"\bBalance\s+due\b\s*:\s*"
    r"(?P<paren_open>\()?\s*"
    r"(?P<sign>-)?\s*"
    r"\$?\s*"
    r"(?P<amount>[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)\s*"
    r"(?P<paren_close>\))?",
    re.IGNORECASE
)

SUBTOTAL_EMPTY_LOADED_REGEX = re.compile(
    r"\bSubtotal\s*:\s*([0-9]{1,6})\s+([0-9]{1,6})\b",
    re.IGNORECASE
)

LOAD_ROW_PICKUP_DELIVERY_REGEX = re.compile(
    r"\b\d{4,6}\s+(?P<pickup>\d{2}/\d{2}/\d{2})\s+(?P<delivery>\d{2}/\d{2}/\d{2})\b"
)

TOLLS_SUBTOTAL_REGEX = re.compile(
    r"\bTolls\b.*?\bSubtotal\b\s*(\(?-?\$?\s*[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?\)?)",
    re.IGNORECASE
)

# NEW: Deductions subtotal (Truck expenses)
DEDUCTIONS_SUBTOTAL_REGEX = re.compile(
    r"\bDeductions\b.*?\bSubtotal\b\s*:\s*(\(?-?\$?\s*[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?\)?)",
    re.IGNORECASE
)

# Store uploaded PDFs in memory so files persist when switching buttons
UPLOAD_STORE = {}  # upload_id -> list[{"name": str, "bytes": bytes}]

# -----------------------------
# Logo: render static/truck.pdf -> PNG image (non-interactive)
# -----------------------------
LOGO_PNG_BYTES = None

def build_logo_png_bytes():
    """
    Render static/truck.pdf (page 1) to a PNG. Best-effort remove neutral-gray background.
    """
    global LOGO_PNG_BYTES
    if LOGO_PNG_BYTES is not None:
        return

    pdf_path = "static/truck.pdf"
    try:
        doc = fitz.open(pdf_path)
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=True)
        raw_png = pix.tobytes("png")
        doc.close()

        # Optional: try to remove neutral gray background
        if Image is not None:
            img = Image.open(BytesIO(raw_png)).convert("RGBA")
            px = img.getdata()
            new_px = []
            for r, g, b, a in px:
                # Detect neutral gray-ish pixels and make transparent (tuned for typical PDF logo gray boxes)
                if a > 0 and abs(r - g) < 12 and abs(g - b) < 12 and 90 < r < 235:
                    new_px.append((r, g, b, 0))
                else:
                    new_px.append((r, g, b, a))
            img.putdata(new_px)

            out = BytesIO()
            img.save(out, format="PNG")
            LOGO_PNG_BYTES = out.getvalue()
        else:
            LOGO_PNG_BYTES = raw_png

    except Exception:
        LOGO_PNG_BYTES = None


@app.route("/logo.png")
def logo_png():
    build_logo_png_bytes()
    if not LOGO_PNG_BYTES:
        return ("", 404)
    return send_file(BytesIO(LOGO_PNG_BYTES), mimetype="image/png")


HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TruckTotals</title>
  <style>
    body { font-family: system-ui, Arial; max-width: 980px; margin: 40px auto; padding: 0 16px; }
    .card { border: 1px solid #ddd; border-radius: 14px; padding: 18px; margin-top: 18px; background:#fff; }
    .muted { color: #666; }
    button { padding: 10px 16px; border-radius: 10px; border: 1px solid #222; background:#111; color:#fff; cursor:pointer; }
    button:disabled { opacity: .6; cursor:not-allowed; }
    input { margin-top: 10px; }
    table { width:100%; border-collapse: collapse; margin-top: 12px; }
    td, th { border-bottom: 1px solid #eee; text-align:left; padding: 10px 6px; }
    .bad { color: #b00020; }
    .good { color: #0a7a3b; }
    .btnrow { display:flex; gap: 10px; flex-wrap: wrap; }
    .tablewrap { overflow-x:auto; -webkit-overflow-scrolling: touch; }

    .toprow { display:flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
    .danger { background:#b00020; border-color:#b00020; }

    /* Brand header (logo + title) */
    .brand{
      display:flex;
      align-items:center;
      gap:14px;
      margin: 0 0 18px;
      flex-wrap: wrap;
    }
    .brandLogo{
      height:56px;
      width:auto;
      display:block;

      /* make it NOT interactive */
      pointer-events:none;
      user-select:none;
      -webkit-user-drag:none;
    }
    .brandTitle{
      margin:0;
      font-size: 34px;
      line-height: 1.05;
    }
    .brandTagline{
      margin: 6px 0 0;
    }

    /* Right panel (calendar drawer) */
    .drawer {
      position: fixed;
      top: 0;
      right: 0;
      height: 100vh;
      width: 360px;
      max-width: 90vw;
      background: #fff;
      border-left: 1px solid #ddd;
      box-shadow: -12px 0 28px rgba(0,0,0,.08);
      transform: translateX(110%);
      transition: transform .22s ease;
      z-index: 50;
      display:flex;
      flex-direction: column;
    }
    .drawer.open { transform: translateX(0); }

    .drawerHeader {
      padding: 16px;
      border-bottom: 1px solid #eee;
      display:flex;
      justify-content: space-between;
      align-items:center;
      gap: 10px;
    }
    .drawerHeader b { font-size: 16px; }
    .drawerBody { padding: 16px; overflow:auto; }
    .drawerFooter { padding: 16px; border-top: 1px solid #eee; }

    .pill {
      border: 1px solid #ddd;
      border-radius: 12px;
      padding: 12px;
      background: #fafafa;
      margin-top: 10px;
    }

    .row { display:flex; gap: 10px; align-items:center; flex-wrap: wrap; }
    .select, .textin {
      border: 1px solid #ddd;
      border-radius: 12px;
      padding: 10px 12px;
      font-size: 14px;
      width: 100%;
      box-sizing: border-box;
    }
    .mini { width: 100px; }
    .month { flex: 1; min-width: 160px; }

    .ghostBtn {
      background:#fff;
      color:#111;
      border:1px solid #222;
    }

    .hint { font-size: 13px; color: #666; margin-top: 8px; line-height: 1.35; }

    /* overlay when drawer open */
    .overlay {
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,.25);
      opacity: 0;
      pointer-events: none;
      transition: opacity .22s ease;
      z-index: 40;
    }
    .overlay.show { opacity: 1; pointer-events: auto; }

    /* Open The Calendar button (clearly visible, different style) */
    #openCalendarBtn{
      position: fixed;
      right: 14px;
      top: 40%;
      transform: translateY(-50%);
      z-index: 60;

      background: #fff;
      color: #111;
      border: 1px solid #bbb;
      border-radius: 14px;
      padding: 12px 14px;

      font-weight: 700;
      letter-spacing: .2px;
      box-shadow: 0 10px 22px rgba(0,0,0,.10);
      cursor: pointer;
    }

    /* When drawer opens, slide this button left a bit */
    #openCalendarBtn.shiftLeft{
      transform: translate(calc(-340px), -50%);
      transition: transform .22s ease;
    }

    /* Nudge animation when user presses Total gross / Miles / Tolls */
    @keyframes nudgeLeft {
      0% { transform: translateY(-50%) translateX(0); }
      40% { transform: translateY(-50%) translateX(-16px); }
      100% { transform: translateY(-50%) translateX(0); }
    }
    #openCalendarBtn.nudge{
      animation: nudgeLeft .28s ease;
    }

    /* Mobile friendly */
    @media (max-width: 560px) {
      body { margin: 18px auto; padding: 0 12px; }
      .card { padding: 14px; }
      button { width: 100%; }
      td, th { padding: 10px 6px; font-size: 14px; }

      .brandLogo{ height:44px; }
      .brandTitle{ font-size: 26px; }

      /* Drawer becomes bottom sheet */
      .drawer {
        top: auto;
        bottom: 0;
        right: 0;
        left: 0;
        width: 100%;
        height: 72vh;
        border-left: none;
        border-top: 1px solid #ddd;
        box-shadow: 0 -12px 28px rgba(0,0,0,.10);
        transform: translateY(110%);
      }
      .drawer.open { transform: translateY(0); }

      /* Open Calendar becomes bottom bar on mobile */
      #openCalendarBtn{
        top: auto;
        bottom: 14px;
        left: 12px;
        right: 12px;
        width: calc(100% - 24px);
        transform: none;
        border-radius: 16px;
        padding: 14px 16px;
      }
      #openCalendarBtn.shiftLeft{ transform: none; }

      @keyframes nudgeLeftMobile {
        0% { transform: translateY(0); }
        40% { transform: translateY(-6px); }
        100% { transform: translateY(0); }
      }
      #openCalendarBtn.nudge{
        animation: nudgeLeftMobile .28s ease;
      }
    }
  </style>
</head>
<body>

  <div class="brand">
    <img src="/logo.png" alt="TruckTotals logo" class="brandLogo" draggable="false">
    <div class="brandText">
      <h1 class="brandTitle">TruckTotals</h1>
      <p class="muted brandTagline">This is a website made specifically for truck drivers that can save tons of time</p>
    </div>
  </div>

  <div class="card">
    <form method="post" enctype="multipart/form-data" id="mainForm">
      <input type="hidden" name="upload_id" id="uploadId" value="{{ upload_id or '' }}">
      <input type="hidden" name="removed" id="removedFlag" value="0">

      <!-- Date filter hidden fields (submitted to server) -->
      <input type="hidden" name="range_start" id="rangeStart" value="{{ range_start or '' }}">
      <input type="hidden" name="range_end" id="rangeEnd" value="{{ range_end or '' }}">

      <div class="toprow">
        <label><b>Select PDFs:</b></label>
        <button type="button" class="danger" id="removeBtn">Remove files</button>
      </div>

      <input id="pdfInput" type="file" name="pdfs" accept="application/pdf" multiple required>
      <br><br>

      <div class="btnrow">
        <button type="submit" name="action" value="balance" class="actionBtn">Total gross</button>
        <button type="submit" name="action" value="miles" class="actionBtn">Total Miles Driven</button>
        <button type="submit" name="action" value="tolls" class="actionBtn">Tolls</button>
        <button type="submit" name="action" value="expenses" class="actionBtn">Truck expenses</button>
      </div>
    </form>
  </div>

  {% if show_no_files %}
    <div class="card">
      <p class="bad"><b>No files selected.</b></p>
    </div>
  {% endif %}

  {% if filter_message %}
    <div class="card">
      <p class="{{ 'good' if filter_ok else 'bad' }}"><b>{{ filter_message }}</b></p>
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
              {% elif mode == 'expenses' %}
                Truck expenses
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
      {% elif mode == 'expenses' %}
        <h3>Total truck expenses: ${{ "{:,.2f}".format(total) }}</h3>
        {% if missing > 0 %}
          <p class="bad">Warning: deductions subtotal was not found in {{ missing }} file(s).</p>
        {% endif %}
      {% else %}
        <h3>Total gross: ${{ "{:,.2f}".format(total) }}</h3>
        {% if missing > 0 %}
          <p class="bad">Warning: “Balance due” was not found in {{ missing }} file(s).</p>
        {% endif %}
      {% endif %}
    </div>
  {% endif %}

  <button type="button" id="openCalendarBtn" aria-controls="drawer" aria-expanded="false">Open The Calendar</button>

  <!-- Overlay + Drawer -->
  <div class="overlay" id="overlay"></div>

  <div class="drawer" id="drawer">
    <div class="drawerHeader">
      <b>Date filter</b>
      <button type="button" class="ghostBtn" id="closeDrawer">Close</button>
    </div>

    <div class="drawerBody">
      <div class="pill">
        <b>Pick the dates</b>
        <div class="row" style="margin-top:10px;">
          <select id="monthSel" class="select month">
            <option>January</option><option>February</option><option>March</option><option>April</option>
            <option>May</option><option>June</option><option>July</option><option>August</option>
            <option>September</option><option>October</option><option>November</option><option>December</option>
          </select>
          <select id="daySel" class="select mini"></select>
          <select id="yearSel" class="select mini"></select>
        </div>

        <div class="row" style="margin-top:10px;">
          <button type="button" id="pickDateBtn">Select start date</button>
          <button type="button" class="danger" id="removeDateBtn">Remove date</button>
        </div>

        <div class="hint">
          Use the dropdowns to choose a date, then press <b>Select start date</b> / <b>Select end date</b>.
        </div>
      </div>

      <div class="pill">
        <div class="row" style="justify-content:space-between;">
          <b>Or type a date</b>
          <button type="button" class="ghostBtn" id="toggleTypeBtn">Type out the date</button>
        </div>

        <input id="typeInput" class="textin" placeholder="00/00/00" inputmode="text" autocomplete="off" style="margin-top:10px; display:none;">
        <div class="hint" id="typeHint" style="display:none;">
          Examples: <b>12/15/25</b>, <b>12/15/2025</b>, <b>December 15 2025</b>, <b>dec 15 25</b>
        </div>
      </div>

      <div class="pill">
        <b>Range from</b>
        <div id="rangeBox" style="margin-top:10px; font-weight:600;">
          {% if range_start and range_end %}
            {{ range_start }} <span class="muted">to</span> {{ range_end }}
          {% elif range_start %}
            {{ range_start }} <span class="muted">to</span> <span class="muted">(choose end)</span>
          {% else %}
            <span class="muted">Choose start and end dates</span>
          {% endif %}
        </div>
        <div class="hint" id="nextHint" style="margin-top:8px;">
          Next: <b id="nextTargetLabel">start</b>
        </div>
      </div>
    </div>

    <div class="drawerFooter">
      <form method="post" id="filterForm">
        <input type="hidden" name="upload_id" value="{{ upload_id or '' }}">
        <input type="hidden" name="action" value="filter_dates">
        <input type="hidden" name="range_start" id="filterRangeStart" value="{{ range_start or '' }}">
        <input type="hidden" name="range_end" id="filterRangeEnd" value="{{ range_end or '' }}">
        <button type="submit" id="applyFilterBtn">Select files between those dates</button>
      </form>
      <div class="hint">
        This will remove files outside the date range from your current selection.
      </div>
    </div>
  </div>

  <script>
    (function () {
      const uploadId = document.getElementById("uploadId").value;
      const input = document.getElementById("pdfInput");
      if (uploadId) input.required = false;
    })();

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
      document.getElementById("mainForm").submit();
    });

    const drawer = document.getElementById("drawer");
    const overlay = document.getElementById("overlay");
    const closeDrawer = document.getElementById("closeDrawer");
    const openCalendarBtn = document.getElementById("openCalendarBtn");

    function openDrawer() {
      drawer.classList.add("open");
      overlay.classList.add("show");
      openCalendarBtn.classList.add("shiftLeft");
      openCalendarBtn.setAttribute("aria-expanded", "true");
    }
    function hideDrawer() {
      drawer.classList.remove("open");
      overlay.classList.remove("show");
      openCalendarBtn.classList.remove("shiftLeft");
      openCalendarBtn.setAttribute("aria-expanded", "false");
    }

    overlay.addEventListener("click", hideDrawer);
    closeDrawer.addEventListener("click", hideDrawer);

    openCalendarBtn.addEventListener("click", function () {
      if (drawer.classList.contains("open")) hideDrawer();
      else openDrawer();
    });

    document.querySelectorAll(".actionBtn").forEach(btn => {
      btn.addEventListener("click", function () {
        openCalendarBtn.classList.remove("nudge");
        void openCalendarBtn.offsetWidth;
        openCalendarBtn.classList.add("nudge");
      });
    });

    const monthSel = document.getElementById("monthSel");
    const daySel = document.getElementById("daySel");
    const yearSel = document.getElementById("yearSel");

    for (let d = 1; d <= 31; d++) {
      const opt = document.createElement("option");
      opt.value = String(d);
      opt.textContent = String(d);
      daySel.appendChild(opt);
    }

    for (let y = 2000; y <= 2035; y++) {
      const opt = document.createElement("option");
      opt.value = String(y);
      opt.textContent = String(y);
      yearSel.appendChild(opt);
    }

    yearSel.value = "2025";

    const rangeBox = document.getElementById("rangeBox");
    const nextTargetLabel = document.getElementById("nextTargetLabel");
    const filterRangeStart = document.getElementById("filterRangeStart");
    const filterRangeEnd = document.getElementById("filterRangeEnd");

    let startVal = (filterRangeStart.value || "").trim();
    let endVal = (filterRangeEnd.value || "").trim();

    let nextTarget = startVal && !endVal ? "end" : "start";
    nextTargetLabel.textContent = nextTarget;

    function renderRange() {
      if (startVal && endVal) {
        rangeBox.innerHTML = `${startVal} <span class="muted">to</span> ${endVal}`;
      } else if (startVal && !endVal) {
        rangeBox.innerHTML = `${startVal} <span class="muted">to</span> <span class="muted">(choose end)</span>`;
      } else {
        rangeBox.innerHTML = `<span class="muted">Choose start and end dates</span>`;
      }
      nextTargetLabel.textContent = nextTarget;
      filterRangeStart.value = startVal;
      filterRangeEnd.value = endVal;
    }

    function pad2(n) { return n < 10 ? "0" + n : "" + n; }

    function monthToNumber(monthName) {
      const map = {
        january:1, february:2, march:3, april:4, may:5, june:6,
        july:7, august:8, september:9, october:10, november:11, december:12
      };
      return map[(monthName||"").toLowerCase()] || 0;
    }

    function setNext(dateStr) {
      if (nextTarget === "start") {
        startVal = dateStr;
        endVal = "";
        nextTarget = "end";
      } else {
        endVal = dateStr;
        nextTarget = "start";
      }
      renderRange();
    }

    function blockEnter(e) {
      if (e.key === "Enter") {
        e.preventDefault();
      }
    }
    monthSel.addEventListener("keydown", blockEnter);
    daySel.addEventListener("keydown", blockEnter);
    yearSel.addEventListener("keydown", blockEnter);

    const pickDateBtn = document.getElementById("pickDateBtn");
    const removeDateBtn = document.getElementById("removeDateBtn");

    function getSelectDateStr() {
      const monthNum = monthToNumber(monthSel.value);
      const dayNum = parseInt(daySel.value || "1", 10);
      const yearNum = parseInt(yearSel.value || "2025", 10);
      const yy = String(yearNum).slice(-2);
      return `${pad2(monthNum)}/${pad2(dayNum)}/${yy}`;
    }

    function updatePickButtonLabel() {
      pickDateBtn.textContent = (nextTarget === "start") ? "Select start date" : "Select end date";
    }

    pickDateBtn.addEventListener("click", function () {
      setNext(getSelectDateStr());
      updatePickButtonLabel();
    });

    removeDateBtn.addEventListener("click", function () {
      if (endVal) {
        endVal = "";
        nextTarget = "end";
      } else if (startVal) {
        startVal = "";
        nextTarget = "start";
      }
      renderRange();
      updatePickButtonLabel();
    });

    const toggleTypeBtn = document.getElementById("toggleTypeBtn");
    const typeInput = document.getElementById("typeInput");
    const typeHint = document.getElementById("typeHint");

    let typingMode = false;

    toggleTypeBtn.addEventListener("click", () => {
      typingMode = !typingMode;
      if (typingMode) {
        typeInput.style.display = "block";
        typeHint.style.display = "block";
        typeInput.value = "";
        typeInput.placeholder = "/  / ";
        typeInput.focus();
      } else {
        typeInput.style.display = "none";
        typeHint.style.display = "none";
      }
    });

    typeInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
      }
    });

    renderRange();
    updatePickButtonLabel();
  </script>
</body>
</html>
"""

# -----------------------------
# Helpers
# -----------------------------
def extract_text_from_bytes(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text_parts = []
    for page in doc:
        text_parts.append(page.get_text("text"))
    doc.close()
    return " ".join(" ".join(text_parts).split())

def signed_money_to_float(s: str) -> float:
    s = (s or "").strip().replace(" ", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    s = s.replace("$", "").replace(",", "")
    return float(s)

def extract_pickups_from_bytes(pdf_bytes: bytes):
    text = extract_text_from_bytes(pdf_bytes)
    return [m.group("pickup") for m in LOAD_ROW_PICKUP_DELIVERY_REGEX.finditer(text)]

def extract_pickup_date_range_from_bytes(pdf_bytes: bytes):
    pickups = extract_pickups_from_bytes(pdf_bytes)
    if not pickups:
        return None
    return f"{pickups[0]} to {pickups[-1]}"

def extract_first_pickup_date_obj(pdf_bytes: bytes):
    pickups = extract_pickups_from_bytes(pdf_bytes)
    if not pickups:
        return None
    try:
        return datetime.strptime(pickups[0], "%m/%d/%y").date()
    except ValueError:
        return None

def extract_balance_due_from_bytes(pdf_bytes: bytes):
    text = extract_text_from_bytes(pdf_bytes)
    m = BALANCE_DUE_REGEX.search(text)
    if not m:
        return None

    amt = m.group("amount")
    sign = m.group("sign")
    paren_open = m.group("paren_open")
    paren_close = m.group("paren_close")

    s = amt
    if sign:
        s = "-" + s
    if paren_open and paren_close:
        s = "-" + s

    return signed_money_to_float(s)

def extract_total_miles_from_bytes(pdf_bytes: bytes):
    text = extract_text_from_bytes(pdf_bytes)
    m = SUBTOTAL_EMPTY_LOADED_REGEX.search(text)
    if not m:
        return None
    return int(m.group(1)) + int(m.group(2))

def extract_tolls_subtotal_from_bytes(pdf_bytes: bytes):
    text = extract_text_from_bytes(pdf_bytes)
    m = TOLLS_SUBTOTAL_REGEX.search(text)
    if not m:
        return None
    return signed_money_to_float(m.group(1))

# NEW
def extract_deductions_subtotal_from_bytes(pdf_bytes: bytes):
    text = extract_text_from_bytes(pdf_bytes)
    m = DEDUCTIONS_SUBTOTAL_REGEX.search(text)
    if not m:
        return None
    return signed_money_to_float(m.group(1))

MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

def parse_user_date(s: str):
    if not s:
        return None
    raw = s.strip()
    if not raw:
        return None

    if "/" in raw:
        parts = [p.strip() for p in raw.replace("-", "/").split("/") if p.strip()]
        if len(parts) == 3:
            a, b, c = parts
            try:
                month = int(a)
            except ValueError:
                month = MONTH_MAP.get(a.lower()[:3], 0)
            try:
                day = int(b)
            except ValueError:
                return None
            try:
                year = int(c)
            except ValueError:
                return None
            if year < 100:
                year = 2000 + year
            try:
                return datetime(year, month, day).date()
            except ValueError:
                return None

    parts = re.split(r"\s+", raw.replace(",", " ").strip())
    if len(parts) >= 3:
        mtxt = parts[0].lower()
        month = MONTH_MAP.get(mtxt[:3], 0)
        try:
            day = int(parts[1])
            year = int(parts[2])
            if year < 100:
                year = 2000 + year
            return datetime(year, month, day).date()
        except Exception:
            return None

    return None

# -----------------------------
# Route
# -----------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        build_logo_png_bytes()  # <-- FORCE logo generation on first load
        return render_template_string(
            HTML,
            results=None,
            upload_id="",
            show_no_files=False,
            range_start="",
            range_end="",
            filter_message="",
            filter_ok=True,
        )

    action = request.form.get("action", "balance").strip()
    upload_id = (request.form.get("upload_id") or "").strip()
    removed_flag = (request.form.get("removed") or "0").strip() == "1"

    range_start_raw = (request.form.get("range_start") or "").strip()
    range_end_raw = (request.form.get("range_end") or "").strip()

    files = request.files.getlist("pdfs")

    selected_files = []
    for f in files:
        if f and getattr(f, "filename", ""):
            name = f.filename.strip()
            if name:
                selected_files.append({"name": name, "bytes": f.read()})

    if selected_files:
        if not upload_id:
            upload_id = uuid.uuid4().hex
        UPLOAD_STORE[upload_id] = selected_files

    if removed_flag:
        if upload_id and upload_id in UPLOAD_STORE:
            del UPLOAD_STORE[upload_id]
        upload_id = ""
        return render_template_string(
            HTML,
            results=None,
            upload_id="",
            show_no_files=True,
            range_start="",
            range_end="",
            filter_message="",
            filter_ok=True,
        )

    stored = UPLOAD_STORE.get(upload_id, []) if upload_id else []

    if not stored and not selected_files and action != "filter_dates":
        return render_template_string(
            HTML,
            results=None,
            upload_id=upload_id,
            show_no_files=False,
            range_start=range_start_raw,
            range_end=range_end_raw,
            filter_message="",
            filter_ok=True,
        )

    filter_message = ""
    filter_ok = True

    if action == "filter_dates":
        if not upload_id or not stored:
            filter_message = "No stored files to filter. Upload PDFs first."
            filter_ok = False
        else:
            start_date = parse_user_date(range_start_raw)
            end_date = parse_user_date(range_end_raw)

            if not start_date or not end_date:
                filter_message = "Please enter BOTH a valid start and end date."
                filter_ok = False
            else:
                if end_date < start_date:
                    start_date, end_date = end_date, start_date
                    range_start_raw, range_end_raw = range_end_raw, range_start_raw

                kept = []
                removed = 0
                for item in stored:
                    d0 = extract_first_pickup_date_obj(item["bytes"])
                    if d0 is None:
                        kept.append(item)
                        continue
                    if start_date <= d0 <= end_date:
                        kept.append(item)
                    else:
                        removed += 1

                UPLOAD_STORE[upload_id] = kept
                stored = kept
                filter_message = f"Filtered files. Removed {removed} file(s) outside the range."
                filter_ok = True

    if action == "filter_dates":
        return render_template_string(
            HTML,
            results=None,
            upload_id=upload_id,
            show_no_files=False,
            range_start=range_start_raw,
            range_end=range_end_raw,
            filter_message=filter_message,
            filter_ok=filter_ok,
        )

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
                total += float(amt)
        elif action == "tolls":
            amt = extract_tolls_subtotal_from_bytes(data)
            results.append({"name": name, "daterange": daterange, "amount": amt})
            if amt is None:
                missing += 1
            else:
                total += amt
        elif action == "expenses":
            amt = extract_deductions_subtotal_from_bytes(data)
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

    mode = (
        "expenses" if action == "expenses"
        else ("tolls" if action == "tolls" else ("miles" if action == "miles" else "balance"))
    )

    return render_template_string(
        HTML,
        results=results,
        total=total,
        missing=missing,
        mode=mode,
        upload_id=upload_id,
        show_no_files=False,
        range_start=range_start_raw,
        range_end=range_end_raw,
        filter_message=filter_message,
        filter_ok=filter_ok,
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
