from flask import Flask, render_template_string, request, send_file, jsonify, after_this_request
import os, io, tempfile, shutil, time, hmac, hashlib, base64, zipfile, uuid
from threading import Timer
from werkzeug.utils import secure_filename
import fitz  # PyMuPDF
from PIL import Image
import razorpay

# -------------------- Config --------------------
FREE_MAX_PAGES = 25
FREE_MAX_MB = 25
PAID_AMOUNT_INR = 10  # ₹10
PAID_AMOUNT_PAISE = PAID_AMOUNT_INR * 100  # Razorpay works in paise

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()

# -------------------- App -----------------------
app = Flask(__name__)

# temp store: token -> info
TEMP = {}  # {"token": {"dir": tmpdir, "pdf_path": ..., "expires": ts, "fname": ...}}

def cleanup_temp():
    now = time.time()
    to_del = []
    for tok, info in list(TEMP.items()):
        if info.get("expires", 0) < now:
            to_del.append(tok)
    for tok in to_del:
        try:
            shutil.rmtree(TEMP[tok]["dir"], ignore_errors=True)
        except Exception:
            pass
        TEMP.pop(tok, None)

def human_mb(nbytes: int) -> float:
    try:
        return round(nbytes / (1024 * 1024), 2)
    except Exception:
        return 0.0

# -------------------- UI ------------------------
INDEX_HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Mahamaya Stationery — PDF → JPG Converter</title>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<style>
  :root{--bg:#0b1220;--fg:#e7eaf1;--muted:#9aa4b2;--card:#0f172a;--stroke:#1f2a44;--accent:#4f8cff;--ok:#22c55e;--err:#ef4444;}
  *{box-sizing:border-box} body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;background:var(--bg);color:var(--fg)}
  .wrap{min-height:100svh;display:grid;place-items:center;padding:24px}
  .card{width:min(900px,100%);background:linear-gradient(180deg,#0f172a 0,#0b1220 100%);border:1px solid var(--stroke);border-radius:18px;padding:22px 20px 18px;box-shadow:0 10px 40px rgba(0,0,0,.35)}
  .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  @media(max-width:720px){.grid{grid-template-columns:1fr}}
  label{font-size:13px;color:#cdd6ea;margin-bottom:6px;display:block}
  select,input[type="text"]{width:100%;background:#0d162a;color:var(--fg);border:1px solid var(--stroke);border-radius:10px;padding:9px 12px}
  .btn{display:inline-flex;gap:8px;align-items:center;background:var(--accent);color:#fff;border:1px solid var(--stroke);padding:10px 14px;border-radius:12px;font-weight:700;cursor:pointer}
  .ghost{background:#16233f}
  .note{color:var(--muted);font-size:13px}
  .alert{margin-top:10px;padding:10px 12px;border-radius:10px;display:none;font-weight:700}
  .ok{background:rgba(34,197,94,.1);color:var(--ok);border:1px solid rgba(34,197,94,.25)}
  .err{background:rgba(239,68,68,.1);color:var(--err);border:1px solid rgba(239,68,68,.25)}
  .drop{border:2px dashed var(--stroke);border-radius:14px;padding:16px;text-align:center;background:#0e1830;cursor:pointer}
  .drop.drag{border-color:var(--accent);background:#122043}
  /* loader overlay */
  .overlay{position:fixed;inset:0;background:rgba(10,15,29,.7);backdrop-filter:blur(2px);display:none;align-items:center;justify-content:center;z-index:999}
  .loader{width:72px;height:72px;border-radius:50%;border:6px solid rgba(255,255,255,.2);border-top-color:#fff;animation:spin 1s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  .overlay .text{margin-top:14px;text-align:center;color:#e7eaf1}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h2 style="margin:0 0 6px;">PDF → JPG Converter</h2>
    <div class="note">Free up to <b>25 pages</b> or <b>25 MB</b>. Above that, <b>₹10</b> charge.</div>
    <div style="height:10px"></div>

    <div id="drop" class="drop">
      <strong>Drag & Drop</strong> <span class="note">or click to choose PDF</span>
      <input id="file" type="file" accept="application/pdf" style="display:none"/>
      <div id="chosen" class="note" style="margin-top:6px"></div>
    </div>

    <div style="height:12px"></div>
    <div class="grid">
      <div>
        <label for="dpi">DPI (Quality)</label>
        <select id="dpi">
          <option value="150">150 (Normal)</option>
          <option value="200">200</option>
          <option value="300">300 (High)</option>
        </select>
      </div>
      <div>
        <label for="format">Format</label>
        <select id="format">
          <option value="JPEG">JPG</option>
          <option value="PNG">PNG</option>
        </select>
      </div>
      <div style="grid-column:1/-1">
        <label for="range">Page Range (e.g. 1-25,27) — blank = all</label>
        <input id="range" type="text" placeholder="1-25,27"/>
      </div>
    </div>

    <div style="height:12px"></div>
    <div class="row">
      <button id="convert" class="btn">Convert & Download ZIP</button>
      <button id="choose" class="btn ghost">Choose PDF</button>
      <div id="status" class="note"></div>
    </div>

    <div id="ok" class="alert ok"></div>
    <div id="err" class="alert err"></div>
  </div>
</div>

<!-- overlay loader & tips -->
<div id="overlay" class="overlay" role="alert" aria-busy="true">
  <div style="text-align:center">
    <div class="loader" style="margin:0 auto"></div>
    <div class="text">
      <div style="font-weight:800;margin-top:10px">Converting your PDF…</div>
      <div class="note">Tip: For faster results, use 150 DPI or select a page range.</div>
    </div>
  </div>
</div>

<script>
const drop = document.getElementById('drop');
const file = document.getElementById('file');
const chosen = document.getElementById('chosen');
const choose = document.getElementById('choose');
const convertBtn = document.getElementById('convert');
const ok = document.getElementById('ok');
const err = document.getElementById('err');
const statusEl = document.getElementById('status');
const overlay = document.getElementById('overlay');

let SELECTED = null;
let LAST_TOKEN = null;

function show(el,msg){ el.textContent = msg; el.style.display = 'block'; }
function hide(el){ el.style.display = 'none'; }
function startOverlay(){ overlay.style.display='flex'; }
function stopOverlay(){ overlay.style.display='none'; }

drop.onclick = ()=> file.click();
choose.onclick = ()=> file.click();

['dragenter','dragover'].forEach(ev=>{
  drop.addEventListener(ev, e=>{ e.preventDefault(); drop.classList.add('drag'); });
});
['dragleave','drop'].forEach(ev=>{
  drop.addEventListener(ev, e=>{ e.preventDefault(); drop.classList.remove('drag'); });
});
drop.addEventListener('drop', e=>{
  e.preventDefault();
  if(e.dataTransfer.files?.length){ setFile(e.dataTransfer.files[0]); file.files=e.dataTransfer.files; }
});
file.addEventListener('change', ()=>{ if(file.files.length){ setFile(file.files[0]); } });

function setFile(f){
  SELECTED = f;
  LAST_TOKEN = null;
  hide(ok); hide(err); statusEl.textContent='';
  chosen.textContent = `Selected: ${f.name} • ${(f.size/1024/1024).toFixed(2)} MB`;
}

async function checkAndMaybePay(){
  hide(ok); hide(err); statusEl.textContent='Checking document...';
  const fd = new FormData();
  fd.append('pdf_file', SELECTED);
  fd.append('dpi', document.getElementById('dpi').value);
  fd.append('format', document.getElementById('format').value);
  fd.append('range', document.getElementById('range').value);

  const res = await fetch('/check', { method:'POST', body: fd });
  if(!res.ok){ throw new Error(await res.text() || ('HTTP '+res.status)); }
  return await res.json();
}

async function convertWithToken(token, opts){
  const res = await fetch('/convert_with_token', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ token, ...opts })
  });
  if(!res.ok){ throw new Error(await res.text() || ('HTTP '+res.status)); }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href=url; a.download='converted_images.zip'; a.click();
  URL.revokeObjectURL(url);
  show(ok, 'Done! Download started.');
}

async function payAndConvert(token, opts){
  // Create order on server
  const orRes = await fetch('/create_order', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ token })
  });
  if(!orRes.ok){ throw new Error(await orRes.text() || ('HTTP '+orRes.status)); }
  const order = await orRes.json();
  if(!order.key_id || !order.order_id){ throw new Error('Payment init failed'); }

  // Open Razorpay Checkout
  return new Promise((resolve, reject)=>{
    const rzp = new Razorpay({
      key: order.key_id,
      amount: order.amount,
      currency: order.currency || 'INR',
      name: 'Mahamaya Stationery',
      description: 'PDF conversion (25+ pages or 25MB+)',
      order_id: order.order_id,
      notes: { token },
      handler: async function (response){
        try{
          // Verify and convert on server
          const vr = await fetch('/verify_and_convert', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body: JSON.stringify({
              token,
              dpi: opts.dpi,
              format: opts.format,
              range: opts.range,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_order_id: response.razorpay_order_id,
              razorpay_signature: response.razorpay_signature
            })
          });
          if(!vr.ok){ throw new Error(await vr.text() || ('HTTP '+vr.status)); }
          const blob = await vr.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a'); a.href=url; a.download='converted_images.zip'; a.click();
          URL.revokeObjectURL(url);
          show(ok, 'Payment successful. Download started.');
          resolve();
        }catch(e){ reject(e); }
      },
      modal: { ondismiss(){ reject(new Error('Payment cancelled.')); } },
      theme: { color: '#2563eb' }
    });
    rzp.open();
  });
}

convertBtn.onclick = async ()=>{
  try{
    if(!SELECTED) throw new Error('Please choose a PDF first.');
    startOverlay();
    const opts = {
      dpi: document.getElementById('dpi').value,
      format: document.getElementById('format').value,
      range: document.getElementById('range').value
    };
    const info = await checkAndMaybePay();
    LAST_TOKEN = info.token;

    if(!info.need_payment){
      await convertWithToken(LAST_TOKEN, opts);
    }else{
      show(err, `This file is ${info.pages} pages / ${info.size_mb} MB. A ₹10 payment is required.`);
      await payAndConvert(LAST_TOKEN, opts);
    }
  }catch(e){
    show(err, e.message || 'Something went wrong.');
  }finally{
    statusEl.textContent=''; stopOverlay();
  }
};
</script>
</body>
</html>
"""

# -------------------- Helpers --------------------
def count_pages(pdf_path: str) -> int:
    with fitz.open(pdf_path) as doc:
        return doc.page_count

def allowed_pages(range_text: str, total_pages: int):
    if not range_text.strip():
        return list(range(1, total_pages+1))
    pages = set()
    import re
    for token in re.split(r"\s*,\s*", range_text.strip()):
        if not token:
            continue
        if "-" in token:
            a,b = token.split("-",1)
            if a.isdigit() and b.isdigit():
                start,end = int(a), int(b)
                if 1 <= start <= end:
                    for p in range(start, min(end, total_pages)+1):
                        pages.add(p)
        elif token.isdigit():
            p = int(token)
            if 1 <= p <= total_pages:
                pages.add(p)
    return sorted(pages) if pages else list(range(1, total_pages+1))

def render_pdf_to_images(pdf_path: str, pages: list[int], dpi: int, fmt: str, out_dir: str):
    # fmt: "JPEG" or "PNG"
    ext = "jpg" if fmt == "JPEG" else "png"
    saved = []
    with fitz.open(pdf_path) as doc:
        for idx, pno in enumerate(pages, 1):
            page = doc.load_page(pno-1)
            zoom = dpi/72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            out_name = f"page_{idx}.{ext}"
            out_path = os.path.join(out_dir, out_name)
            img.save(out_path, fmt)
            saved.append(out_path)
    return saved

def zip_paths(paths: list[str], zip_path: str):
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            z.write(p, os.path.basename(p))

# -------------------- Routes ---------------------
@app.route("/")
def home():
    return render_template_string(INDEX_HTML)

@app.route("/healthz")
def healthz():
    return "OK: Mahamaya server is running ✅"

@app.route("/check", methods=["POST"])
def check():
    cleanup_temp()
    pdf = request.files.get("pdf_file")
    if not pdf:
        return ("No file uploaded", 400)

    dpi = int(request.form.get("dpi", "150"))
    fmt = request.form.get("format", "JPEG").upper()
    rng = request.form.get("range", "").strip()

    tmp = tempfile.mkdtemp(prefix="pdfchk_")
    try:
        fname = secure_filename(pdf.filename) or "input.pdf"
        pdf_path = os.path.join(tmp, fname)
        pdf.save(pdf_path)

        size_bytes = os.path.getsize(pdf_path)
        size_mb = human_mb(size_bytes)
        pages_total = count_pages(pdf_path)
        pages_selected = allowed_pages(rng, pages_total)

        need_payment = (size_mb > FREE_MAX_MB) or (len(pages_selected) > FREE_MAX_PAGES)

        token = uuid.uuid4().hex
        TEMP[token] = {"dir": tmp, "pdf_path": pdf_path, "expires": time.time()+3600, "fname": fname,
                       "dpi": dpi, "fmt": fmt, "rng": rng, "pages_selected": pages_selected, "size_mb": size_mb,
                       "pages_total": pages_total}
        # Note: keep tmp dir; deleted after conversion or expiry

        return jsonify({
            "ok": True,
            "token": token,
            "size_mb": size_mb,
            "pages": len(pages_selected),
            "pages_total": pages_total,
            "need_payment": need_payment
        })
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return (f"Error: {e}", 500)

@app.route("/convert_with_token", methods=["POST"])
def convert_with_token():
    cleanup_temp()
    data = request.get_json(force=True, silent=True) or {}
    token = data.get("token")
    info = TEMP.get(token)
    if not token or not info:
        return ("Invalid/expired token", 400)

    dpi = int(data.get("dpi", info.get("dpi", 150)))
    fmt = (data.get("format", info.get("fmt", "JPEG")) or "JPEG").upper()
    rng = data.get("range", info.get("rng", ""))

    try:
        pages_selected = allowed_pages(rng, info["pages_total"])
        out_dir = info["dir"]
        images = render_pdf_to_images(info["pdf_path"], pages_selected, dpi, fmt, out_dir)
        zip_path = os.path.join(out_dir, "converted_images.zip")
        zip_paths(images, zip_path)

        @after_this_request
        def done(resp):
            Timer(10.0, shutil.rmtree, args=[info["dir"]], kwargs={"ignore_errors": True}).start()
            TEMP.pop(token, None)
            return resp

        return send_file(zip_path, as_attachment=True, download_name="converted_images.zip")
    except Exception as e:
        Timer(1.0, shutil.rmtree, args=[info["dir"]], kwargs={"ignore_errors": True}).start()
        TEMP.pop(token, None)
        return (f"Error: {e}", 500)

@app.route("/create_order", methods=["POST"])
def create_order():
    if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
        return ("Payment not configured. Contact admin.", 500)

    data = request.get_json(force=True, silent=True) or {}
    token = data.get("token")
    info = TEMP.get(token)
    if not token or not info:
        return ("Invalid/expired token", 400)

    # Always ₹10 for exceeding limits
    client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    order = client.order.create({
        "amount": PAID_AMOUNT_PAISE,
        "currency": "INR",
        "receipt": f"mm-{token}",
        "payment_capture": 1
    })
    return jsonify({
        "order_id": order.get("id"),
        "amount": order.get("amount"),
        "currency": order.get("currency", "INR"),
        "key_id": RAZORPAY_KEY_ID
    })

def _verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    try:
        message = f"{order_id}|{payment_id}".encode("utf-8")
        secret = RAZORPAY_KEY_SECRET.encode("utf-8")
        digest = hmac.new(secret, msg=message, digestmod=hashlib.sha256).hexdigest()
        # signature from Razorpay is hex? (usually hex digest). Sometimes base64. Try both:
        given = signature.strip()
        if given == digest:
            return True
        # If sent as base64, compare after converting
        try:
            alt = base64.b64encode(bytes.fromhex(digest)).decode("utf-8")
            return given == alt
        except Exception:
            return False
    except Exception:
        return False

@app.route("/verify_and_convert", methods=["POST"])
def verify_and_convert():
    cleanup_temp()
    data = request.get_json(force=True, silent=True) or {}
    token = data.get("token")
    info = TEMP.get(token)
    if not token or not info:
        return ("Invalid/expired token", 400)

    if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
        return ("Payment not configured. Contact admin.", 500)

    order_id = data.get("razorpay_order_id","")
    payment_id = data.get("razorpay_payment_id","")
    signature = data.get("razorpay_signature","")
    if not (order_id and payment_id and signature):
        return ("Missing payment details", 400)

    if not _verify_signature(order_id, payment_id, signature):
        return ("Payment verification failed", 400)

    # Verified → convert
    dpi = int(data.get("dpi", info.get("dpi", 150)))
    fmt = (data.get("format", info.get("fmt", "JPEG")) or "JPEG").upper()
    rng = data.get("range", info.get("rng", ""))

    try:
        pages_selected = allowed_pages(rng, info["pages_total"])
        out_dir = info["dir"]
        images = render_pdf_to_images(info["pdf_path"], pages_selected, dpi, fmt, out_dir)
        zip_path = os.path.join(out_dir, "converted_images.zip")
        zip_paths(images, zip_path)

        @after_this_request
        def done(resp):
            Timer(10.0, shutil.rmtree, args=[info["dir"]], kwargs={"ignore_errors": True}).start()
            TEMP.pop(token, None)
            return resp

        return send_file(zip_path, as_attachment=True, download_name="converted_images.zip")
    except Exception as e:
        Timer(1.0, shutil.rmtree, args=[info["dir"]], kwargs={"ignore_errors": True}).start()
        TEMP.pop(token, None)
        return (f"Error: {e}", 500)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
import os
import razorpay

client = razorpay.Client(auth=(
    os.environ.get("RAZORPAY_KEY_ID"),
    os.environ.get("RAZORPAY_KEY_SECRET")
))
