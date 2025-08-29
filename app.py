from flask import Flask, render_template_string, request, send_file, after_this_request, jsonify
from werkzeug.utils import secure_filename
import os, io, tempfile, zipfile, shutil, hmac, hashlib, base64
from threading import Timer
import fitz  # PyMuPDF
import razorpay

# ------------ Config ------------
FREE_MAX_PAGES = 25
FREE_MAX_MB = 25
PAID_AMOUNT_INR = 10        # ₹10
CURRENCY = "INR"

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

# Razorpay client (available if creds present)
rz_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    rz_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

app = Flask(__name__)

# ------------ UI ------------
INDEX_HTML = r"""
<!doctype html>
<html lang="hi">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Mahamaya Stationery — PDF → Image Converter</title>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<style>
  :root{--bg:#0b1220;--fg:#e7eaf1;--muted:#93a2bd;--card:#0f172a;--stroke:#213154;--accent:#4f8cff;--good:#22c55e;--bad:#ef4444}
  *{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;background:var(--bg);color:var(--fg)}
  .shell{min-height:100svh;display:grid;place-items:center;padding:22px}
  .card{width:min(900px,100%);background:linear-gradient(180deg,#0f172a,#0b1220);border:1px solid var(--stroke);border-radius:18px;padding:22px;box-shadow:0 14px 40px rgba(0,0,0,.35)}
  .top{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
  .brand{display:flex;align-items:center;gap:10px;font-weight:900}
  .badge{font-size:12px;padding:2px 8px;border:1px solid var(--stroke);border-radius:999px;color:var(--muted)}
  h1{margin:8px 0 6px;font-size:22px}
  p.muted{margin:0 0 12px;color:var(--muted)}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  @media (max-width:720px){.grid{grid-template-columns:1fr}}
  label{font-size:13px;margin-bottom:6px;display:block;color:#cfe1ff}
  input,select{width:100%;background:#0e1832;color:var(--fg);border:1px solid var(--stroke);border-radius:10px;padding:10px 12px;outline:none}
  .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  button{display:inline-flex;align-items:center;gap:8px;padding:10px 14px;border-radius:12px;border:1px solid var(--stroke);background:var(--accent);color:#fff;font-weight:700;cursor:pointer}
  button.ghost{background:#18233f}
  button:disabled{opacity:.6;cursor:not-allowed}
  .drop{border:2px dashed var(--stroke);border-radius:14px;padding:16px;background:#0d162d;text-align:center}
  .note{font-size:12px;color:var(--muted)}
  .alert{margin-top:10px;padding:10px 12px;border-radius:12px;font-weight:600;display:none}
  .ok{background:rgba(34,197,94,.1);color:var(--good);border:1px solid rgba(34,197,94,.25)}
  .err{background:rgba(239,68,68,.1);color:var(--bad);border:1px solid rgba(239,68,68,.25)}
  .pill{display:inline-flex;gap:6px;align-items:center;padding:4px 10px;border-radius:999px;border:1px solid var(--stroke);background:#0f1b38;color:#9fb4ff;font-size:12px}
  .spinner{width:18px;height:18px;border:3px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:spin 1s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}

  /* cute waiting animation */
  .loader {
    display:none; margin:10px 0 0; height:24px; position:relative;
  }
  .loader > div {
    width:8px; height:8px; background:#9fb4ff; border-radius:50%; position:absolute; animation:bounce 1.2s infinite ease-in-out;
  }
  .loader .d1{left:0; animation-delay:-.24s}
  .loader .d2{left:12px; animation-delay:-.12s}
  .loader .d3{left:24px; animation-delay:0s}
  @keyframes bounce {
    0%,80%,100%{ transform:scale(0) }
    40%{ transform:scale(1.0) }
  }
</style>
</head>
<body>
<div class="shell">
  <div class="card">
    <div class="top">
      <div class="brand">
        <div style="width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,#4f8cff,#22c55e)"></div>
        <div>Mahamaya Stationery</div>
      </div>
      <div class="badge">PDF → JPG/PNG · Free up to 25 pages/25MB</div>
    </div>

    <h1>तेज़ और साफ़ PDF → इमेज कन्वर्टर</h1>
    <p class="muted">25 पेज या 25MB तक फ्री। उसके ऊपर सिर्फ ₹10। पासवर्ड-PDF भी सपोर्टेड।</p>

    <div class="drop">
      <strong>Drag & Drop</strong> <span class="note">या क्लिक कर के PDF चुनें</span><br/>
      <input id="file" type="file" accept="application/pdf" />
      <div id="chosen" class="note" style="margin-top:8px"></div>
    </div>

    <div style="height:12px"></div>

    <div class="grid">
      <div>
        <label for="dpi">क्वालिटी (DPI)</label>
        <select id="dpi">
          <option value="150">150 (Normal)</option>
          <option value="200">200</option>
          <option value="300">300 (Sharp)</option>
        </select>
      </div>
      <div>
        <label for="format">फॉर्मेट</label>
        <select id="format">
          <option value="JPEG">JPG</option>
          <option value="PNG">PNG</option>
        </select>
      </div>
      <div>
        <label for="range">पेज रेंज (उदा. 1-25,30)</label>
        <input id="range" type="text" placeholder="खाली = सभी पेज" />
      </div>
      <div>
        <label for="pdfpw">Password (अगर हो)</label>
        <input id="pdfpw" type="password" placeholder="Leave blank if none" />
      </div>
    </div>

    <div style="height:12px"></div>

    <div class="row">
      <button id="checkBtn">Check & Convert</button>
      <button id="payBtn" class="ghost" style="display:none">Pay ₹10 & Convert</button>
      <span id="status" class="note pill">Ready</span>
    </div>
    <div class="loader" id="loader">
      <div class="d1"></div><div class="d2"></div><div class="d3"></div>
    </div>

    <div id="ok" class="alert ok">Download started.</div>
    <div id="err" class="alert err">Error</div>

    <p class="muted" style="margin-top:10px">टिप: बहुत बड़े PDFs पर 150 DPI या पेज रेंज चुनना तेज़ रहता है।</p>
  </div>
</div>

<script>
const file = document.getElementById('file');
const chosen = document.getElementById('chosen');
const checkBtn = document.getElementById('checkBtn');
const payBtn = document.getElementById('payBtn');
const statusEl = document.getElementById('status');
const ok = document.getElementById('ok');
const err = document.getElementById('err');
const loader = document.getElementById('loader');

let selected = null;
let pendingOrder = null; // {order_id, amount, key_id}

function show(el, msg){ el.textContent = msg; el.style.display='block'; }
function hide(el){ el.style.display='none'; }
function busy(on){
  if(on){ loader.style.display='block'; statusEl.textContent='Working... please wait'; }
  else { loader.style.display='none'; statusEl.textContent='Ready'; }
}

file.addEventListener('change', ()=>{
  if(file.files.length){
    selected = file.files[0];
    chosen.textContent = `Selected: ${selected.name} · ${(selected.size/1024/1024).toFixed(2)} MB`;
  }
});

async function precheck(){
  if(!selected){ show(err, "कृपया PDF चुनें."); hide(ok); return; }
  hide(err); hide(ok); busy(true); payBtn.style.display='none'; pendingOrder=null;

  const fd = new FormData();
  fd.append('pdf_file', selected);
  fd.append('pdf_password', document.getElementById('pdfpw').value || '');

  const res = await fetch('/precheck', {method:'POST', body:fd});
  const data = await res.json().catch(()=>({error:"Server error"}));
  busy(false);

  if(!res.ok){
    show(err, data.error || 'Precheck failed.');
    return;
  }

  const { pages, size_mb, chargeable, amount, order_id, key_id } = data;
  if(chargeable){
    show(err, `Too many pages/size for free (pages=${pages}, size=${size_mb}MB). Please pay ₹${(amount/100).toFixed(0)}.`); // info style
    payBtn.style.display='inline-flex';
    pendingOrder = {order_id, amount, key_id};
  }else{
    hide(err);
    await doConvert(); // free directly
  }
}

async function doConvert(extra={}){
  hide(err); hide(ok); busy(true);
  const fd = new FormData();
  fd.append('pdf_file', selected);
  fd.append('dpi', document.getElementById('dpi').value);
  fd.append('format', document.getElementById('format').value);
  fd.append('range', document.getElementById('range').value);
  fd.append('pdf_password', document.getElementById('pdfpw').value || '');
  if(extra.razorpay_payment_id) fd.append('razorpay_payment_id', extra.razorpay_payment_id);
  if(extra.razorpay_order_id) fd.append('razorpay_order_id', extra.razorpay_order_id);
  if(extra.razorpay_signature) fd.append('razorpay_signature', extra.razorpay_signature);

  const res = await fetch('/convert', {method:'POST', body:fd});
  busy(false);

  if(!res.ok){
    const t = await res.text();
    show(err, t || 'Conversion failed.');
    return;
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'converted_images.zip';
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
  show(ok, 'Download started.');
}

checkBtn.addEventListener('click', precheck);

// Razorpay pay then convert
payBtn.addEventListener('click', async ()=>{
  if(!pendingOrder){ show(err, "Order not ready. कृपया फिर से Try करें."); return; }
  const {order_id, amount, key_id} = pendingOrder;

  const r = new Razorpay({
    key: key_id,
    amount: amount,
    currency: 'INR',
    name: 'Mahamaya Stationery',
    description: 'PDF Conversion',
    order_id,
    theme: { color: '#4f8cff' },
    handler: async function (resp) {
      // resp: { razorpay_payment_id, razorpay_order_id, razorpay_signature }
      await doConvert(resp);
    }
  });
  r.on('payment.failed', function (response){
    show(err, response.error && response.error.description ? response.error.description : "Payment failed.");
  });
  r.open();
});
</script>
</body>
</html>
"""

# ------------ Helpers ------------
def parse_range(range_text: str, total_pages: int):
    """ '1-3,5,7' -> [1,2,3,5,7] (1-indexed ranges) """
    if not range_text:
        return list(range(1, total_pages + 1))
    pages = set()
    import re
    for token in re.split(r"\s*,\s*", range_text.strip()):
        if not token:
            continue
        if "-" in token:
            a, b = token.split("-", 1)
            if a.isdigit() and b.isdigit():
                start, end = int(a), int(b)
                if start > 0 and start <= end:
                    for p in range(start, min(end, total_pages) + 1):
                        pages.add(p)
        elif token.isdigit():
            p = int(token)
            if 1 <= p <= total_pages:
                pages.add(p)
    return sorted(pages) if pages else list(range(1, total_pages + 1))

def file_size_mb(file_storage) -> float:
    pos = file_storage.stream.tell()
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(pos)
    return round(size / (1024 * 1024), 2)

def verify_razorpay_signature(order_id: str, payment_id: str, signature: str) -> bool:
    if not (RAZORPAY_KEY_SECRET and order_id and payment_id and signature):
        return False
    msg = f"{order_id}|{payment_id}".encode("utf-8")
    hm = hmac.new(RAZORPAY_KEY_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(hm, signature)

# ------------ Routes ------------
@app.route("/")
def home():
    return render_template_string(INDEX_HTML)

@app.route("/healthz")
def health():
    return "OK", 200

@app.route("/precheck", methods=["POST"])
def precheck():
    pdf = request.files.get("pdf_file")
    pwd = request.form.get("pdf_password", "")
    if not pdf:
        return jsonify(error="No file uploaded"), 400

    size_mb = file_size_mb(pdf)
    # Need a temp path to examine page count
    tmp = tempfile.mkdtemp(prefix="precheck_")
    try:
        fname = secure_filename(pdf.filename) or "input.pdf"
        pdf_path = os.path.join(tmp, fname)
        pdf.save(pdf_path)

        doc = fitz.open(pdf_path)
        if doc.is_encrypted:
            if not doc.authenticate(pwd or ""):
                return jsonify(error="This PDF is password-protected. Please enter correct password."), 403
        pages = doc.page_count
        doc.close()

        chargeable = (pages > FREE_MAX_PAGES) or (size_mb > FREE_MAX_MB)
        payload = {
            "pages": pages,
            "size_mb": size_mb,
            "chargeable": chargeable,
            "amount": 0,
            "order_id": None,
            "key_id": RAZORPAY_KEY_ID if RAZORPAY_KEY_ID else None
        }

        if chargeable:
            if not rz_client:
                return jsonify(error="Payment required but Razorpay not configured"), 500
            # amount in paise
            amount_paise = PAID_AMOUNT_INR * 100
            order = rz_client.order.create({
                "amount": amount_paise,
                "currency": CURRENCY,
                "payment_capture": 1
            })
            payload.update({
                "amount": amount_paise,
                "order_id": order.get("id"),
                "key_id": RAZORPAY_KEY_ID
            })

        return jsonify(payload), 200

    finally:
        Timer(2.0, shutil.rmtree, args=[tmp], kwargs={"ignore_errors": True}).start()

@app.route("/convert", methods=["POST"])
def convert_route():
    pdf = request.files.get("pdf_file")
    if not pdf:
        return "No file uploaded", 400

    dpi = int(request.form.get("dpi", "150"))
    fmt = request.form.get("format", "JPEG").upper()
    rng = request.form.get("range", "").strip()
    pwd = request.form.get("pdf_password", "")

    # for paid flows (only required if over limits; else ignored)
    r_order_id = request.form.get("razorpay_order_id", "")
    r_payment_id = request.form.get("razorpay_payment_id", "")
    r_signature = request.form.get("razorpay_signature", "")

    tmp = tempfile.mkdtemp(prefix="pdf2img_")
    try:
        # save
        fname = secure_filename(pdf.filename) or "input.pdf"
        pdf_path = os.path.join(tmp, fname)
        pdf.save(pdf_path)

        # open and check
        size_mb = file_size_mb(pdf)
        doc = fitz.open(pdf_path)
        if doc.is_encrypted:
            if not doc.authenticate(pwd or ""):
                doc.close()
                return "Password incorrect or missing.", 403

        total_pages = doc.page_count
        pages_to_render = parse_range(rng, total_pages)

        # decide free vs paid
        needs_payment = (total_pages > FREE_MAX_PAGES) or (size_mb > FREE_MAX_MB)
        if needs_payment:
            # must verify signature
            if not (r_order_id and r_payment_id and r_signature):
                doc.close()
                return "Payment required. Please complete payment.", 402
            if not verify_razorpay_signature(r_order_id, r_payment_id, r_signature):
                doc.close()
                return "Payment signature invalid.", 403

        # render to images and zip
        zip_path = os.path.join(tmp, "converted_images.zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # dpi -> zoom
            zoom = max(1.0, dpi / 72.0)
            mat = fitz.Matrix(zoom, zoom)
            for i, pno in enumerate(pages_to_render, 1):
                page = doc.load_page(pno - 1)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                ext = "jpg" if fmt == "JPEG" else "png"
                out_name = f"page_{i}.{ext}"
                out_path = os.path.join(tmp, out_name)
                pix.save(out_path)
                zf.write(out_path, out_name)
        doc.close()

        @after_this_request
        def cleanup(response):
            Timer(5.0, shutil.rmtree, args=[tmp], kwargs={"ignore_errors": True}).start()
            return response

        return send_file(zip_path, as_attachment=True, download_name="converted_images.zip")

    except Exception as e:
        Timer(1.0, shutil.rmtree, args=[tmp], kwargs={"ignore_errors": True}).start()
        return (f"Error: {e}", 500)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
