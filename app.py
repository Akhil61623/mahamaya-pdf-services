import os, io, tempfile, zipfile, shutil, random, string
from threading import Timer
from flask import Flask, request, send_file, after_this_request, jsonify, render_template_string
import fitz  # PyMuPDF
from PIL import Image
import razorpay

# ---------- Config ----------
FREE_PAGE_LIMIT = 25
FREE_SIZE_MB    = 25
PAID_AMOUNT_RS  = 10   # ₹10
CURRENCY        = "INR"

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

app = Flask(__name__)

# Razorpay client (केवल तभी बने जब keys सही हों)
razor_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    try:
        razor_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    except Exception:
        razor_client = None

# ---------- UI (raw string so no f-string issues) ----------
INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Mahamaya Stationery — PDF → JPG Converter</title>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<style>
  :root{--bg:#0b1220;--fg:#e7eaf1;--muted:#93a2bd;--card:#10182b;--accent:#4f8cff;--ok:#22c55e;--bad:#ef4444;--stroke:#203054}
  *{box-sizing:border-box} body{margin:0;font-family:system-ui,Segoe UI,Roboto,Arial;background:var(--bg);color:var(--fg)}
  .wrap{min-height:100svh;display:grid;place-items:center;padding:24px}
  .card{width:min(880px,100%);background:linear-gradient(180deg,#0f172a,#0b1220);border:1px solid var(--stroke);border-radius:18px;padding:22px;box-shadow:0 10px 40px rgba(0,0,0,.35)}
  h1{margin:0 0 6px} .muted{color:var(--muted)} .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .drop{border:2px dashed var(--stroke);border-radius:14px;padding:16px;background:#0e1830;text-align:center}
  input,select{width:100%;background:#0d162a;color:var(--fg);border:1px solid var(--stroke);border-radius:10px;padding:10px 12px}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px} @media(max-width:720px){.grid{grid-template-columns:1fr}}
  .btn{border:1px solid var(--stroke);background:var(--accent);color:#fff;font-weight:700;padding:10px 14px;border-radius:12px;cursor:pointer}
  .ghost{background:#17233f} .sec{background:#1f2a44}
  .alert{display:none;margin-top:10px;padding:10px;border-radius:10px}
  .ok{background:rgba(34,197,94,.1);color:var(--ok);border:1px solid rgba(34,197,94,.3)}
  .err{background:rgba(239,68,68,.1);color:var(--bad);border:1px solid rgba(239,68,68,.3)}
  .spinner{width:18px;height:18px;border:3px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin 1s linear infinite;display:none}
  @keyframes spin{to{transform:rotate(360deg)}}
  .loader {display:none;margin-top:10px}
  .bar{height:6px;width:100%;background:#13203a;border-radius:999px;overflow:hidden}
  .bar>div{height:6px;width:30%;background:linear-gradient(90deg,#4f8cff,#22c55e);border-radius:999px;animation:load 1.2s infinite}
  @keyframes load{0%{margin-left:-30%}100%{margin-left:100%}}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>PDF → JPG Converter</h1>
    <p class="muted">Free up to 25 pages or 25 MB. Above that, ₹10 charge.</p>

    <div class="drop" id="drop">
      <input id="file" type="file" accept="application/pdf" />
      <div class="muted" id="chosen"></div>
    </div>

    <div style="height:10px"></div>
    <div class="grid">
      <div>
        <label>DPI (Quality):</label>
        <select id="dpi">
          <option value="150">150 (Normal)</option>
          <option value="200">200</option>
          <option value="300">300 (Sharp)</option>
        </select>
      </div>
    </div>

    <div style="height:10px"></div>
    <div class="row">
      <button class="btn" id="checkBtn"><span id="spin" class="spinner"></span> Convert</button>
      <button class="btn ghost" id="chooseBtn">Choose PDF</button>
      <div class="muted" id="status"></div>
    </div>

    <div class="loader" id="loader"><div class="bar"><div></div></div><div class="muted">Processing… please wait</div></div>

    <div id="ok" class="alert ok"></div>
    <div id="err" class="alert err"></div>
  </div>
</div>

<script>
const fileEl = document.getElementById('file');
const chooseBtn = document.getElementById('chooseBtn');
const checkBtn = document.getElementById('checkBtn');
const spin = document.getElementById('spin');
const statusEl = document.getElementById('status');
const chosen = document.getElementById('chosen');
const ok = document.getElementById('ok');
const err = document.getElementById('err');
const loader = document.getElementById('loader');

function show(el,msg){ el.textContent=msg; el.style.display='block'; }
function hide(el){ el.style.display='none'; }
function clearAlerts(){ hide(ok); hide(err); statusEl.textContent=''; }

chooseBtn.onclick = () => fileEl.click();
fileEl.onchange = () => {
  if (fileEl.files.length){
    const f = fileEl.files[0];
    show(chosen, `Selected: ${f.name} · ${(f.size/1024/1024).toFixed(2)} MB`);
  }
};

checkBtn.onclick = async () => {
  try{
    clearAlerts(); hide(loader);
    if (!fileEl.files.length) { show(err, "Please choose a PDF"); return; }
    checkBtn.disabled = true; spin.style.display='inline-block'; statusEl.textContent = "Checking file…";

    // Step 1: precheck
    const fd = new FormData();
    fd.append('pdf_file', fileEl.files[0]);
    const pre = await fetch('/precheck', { method:'POST', body: fd });
    if (!pre.ok) throw new Error(await pre.text());
    const info = await pre.json();

    if (!info.needs_payment){
      // Free path → directly convert
      await doConvert();
      return;
    }

    // Paid path → create Razorpay order
    statusEl.textContent = "Creating payment order…";
    const make = await fetch('/create-order', { method:'POST' });
    if (!make.ok) throw new Error(await make.text());
    const order = await make.json();

    const options = {
      key: order.key_id,
      amount: order.amount,
      currency: order.currency,
      name: "Mahamaya Stationery",
      description: "PDF conversion (25+ pages or 25MB+)",
      order_id: order.id,
      handler: async function (response) {
        // On success → convert
        await doConvert(response);
      },
      theme: { color: "#4f8cff" }
    };
    const rzp = new Razorpay(options);
    rzp.open();

  }catch(e){
    show(err, e.message || "Failed.");
  }finally{
    checkBtn.disabled = false; spin.style.display='none';
  }
};

async function doConvert(payment){
  try{
    statusEl.textContent = "Uploading & converting…";
    show(loader, "");
    const fd2 = new FormData();
    fd2.append('pdf_file', fileEl.files[0]);
    fd2.append('dpi', document.getElementById('dpi').value);
    if (payment){
      fd2.append('paid', 'true');
      fd2.append('razorpay_payment_id', payment.razorpay_payment_id || '');
      fd2.append('razorpay_order_id', payment.razorpay_order_id || '');
      fd2.append('razorpay_signature', payment.razorpay_signature || '');
    }
    const res = await fetch('/convert', { method:'POST', body: fd2 });
    if (!res.ok) throw new Error(await res.text());
    // download zip
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href=url; a.download='converted_images.zip'; a.click();
    URL.revokeObjectURL(url);
    show(ok, "Done! Download started.");
  }catch(e){
    show(err, e.message || "Conversion failed.");
  }finally{
    hide(loader);
    statusEl.textContent = "";
  }
}
</script>
</body>
</html>
"""

# -------- Helpers --------
def _random_receipt():
    return "rcpt_" + "".join(random.choice(string.ascii_letters + string.digits) for _ in range(10))

def count_pages_and_size(file_storage):
    # size
    try:
        file_storage.stream.seek(0, os.SEEK_END)
        size = file_storage.stream.tell()
        file_storage.stream.seek(0)
    except Exception:
        size = 0
    size_mb = size / (1024 * 1024) if size else 0.0
    # pages via PyMuPDF
    with fitz.open(stream=file_storage.read(), filetype="pdf") as doc:
        pages = doc.page_count
    file_storage.stream.seek(0)
    return pages, size_mb

def convert_pdf_to_zip(pdf_bytes: bytes, dpi: int):
    tmp = tempfile.mkdtemp(prefix="pdf2img_")
    try:
        # write temp pdf
        pdf_path = os.path.join(tmp, "input.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

        # render images
        doc = fitz.open(pdf_path)
        out_files = []
        for i, page in enumerate(doc, start=1):
            zoom = dpi / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_path = os.path.join(tmp, f"page_{i}.jpg")
            Image.frombytes("RGB", [pix.width, pix.height], pix.samples).save(img_path, "JPEG", quality=90, optimize=True)
            out_files.append(img_path)

        # zip
        zip_path = os.path.join(tmp, "converted_images.zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in out_files:
                zf.write(p, os.path.basename(p))

        # send as bytes
        with open(zip_path, "rb") as f:
            data = f.read()

        @after_this_request
        def cleanup(resp):
            Timer(3.0, shutil.rmtree, args=[tmp], kwargs={"ignore_errors": True}).start()
            return resp

        return data

    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

# -------- Routes --------
@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

@app.route("/healthz")
def health():
    return "OK"

@app.route("/precheck", methods=["POST"])
def precheck():
    pdf = request.files.get("pdf_file")
    if not pdf:
        return ("No file", 400)
    pages, size_mb = count_pages_and_size(pdf)
    needs_payment = (pages > FREE_PAGE_LIMIT) or (size_mb > FREE_SIZE_MB)
    return jsonify({
        "pages": pages,
        "size_mb": round(size_mb, 2),
        "needs_payment": needs_payment,
        "limit_pages": FREE_PAGE_LIMIT,
        "limit_mb": FREE_SIZE_MB,
        "amount_rs": PAID_AMOUNT_RS
    })

@app.route("/create-order", methods=["POST"])
def create_order():
    if not razor_client:
        return ("Payment not configured", 400)
    amount_paise = PAID_AMOUNT_RS * 100
    order = razor_client.order.create(dict(
        amount=amount_paise,
        currency=CURRENCY,
        receipt=_random_receipt(),
        payment_capture=1
    ))
    # add key for frontend
    order["key_id"] = RAZORPAY_KEY_ID
    return jsonify(order)

@app.route("/convert", methods=["POST"])
def convert_route():
    pdf = request.files.get("pdf_file")
    if not pdf:
        return ("No file uploaded", 400)
    dpi = int(request.form.get("dpi", "150"))

    # check limits again on server
    pages, size_mb = count_pages_and_size(pdf)
    requires_pay = (pages > FREE_PAGE_LIMIT) or (size_mb > FREE_SIZE_MB)
    if requires_pay:
        # (basic check) ensure client indicated paid; for production verify signature server-side
        paid = request.form.get("paid") == "true"
        if not paid:
            return ("Payment required for files above free limit.", 402)

    # do conversion
    data = convert_pdf_to_zip(pdf.read(), dpi=dpi)
    return send_file(io.BytesIO(data), as_attachment=True, download_name="converted_images.zip", mimetype="application/zip")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
