import os
import io
import zipfile
from typing import List
from flask import Flask, request, send_file, render_template_string, jsonify
import fitz  # PyMuPDF
from PIL import Image
import razorpay

app = Flask(__name__)

# ----- Razorpay Keys via Environment -----
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

razorpay_client = None
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# ----- Limits -----
FREE_MAX_PAGES = 25
FREE_MAX_MB = 25
PAID_AMOUNT_INR = 10  # ₹10
PAID_AMOUNT_PAISE = PAID_AMOUNT_INR * 100  # Razorpay needs paise


# ------------------ FRONTEND ------------------
INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Mahamaya Stationery — PDF → JPG Converter</title>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<style>
  :root{
    --bg:#0b1220; --fg:#e7eaf1; --muted:#93a2bd; --card:#10182b;
    --accent:#4f8cff; --accent2:#22c55e; --danger:#ef4444; --stroke:#203054;
  }
  *{box-sizing:border-box}
  body{margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial; background:var(--bg); color:var(--fg)}
  .wrap{min-height:100svh; display:grid; place-items:center; padding:24px}
  .card{width:min(900px,100%); background:linear-gradient(180deg,#0f172a 0,#0b1220 100%);
        border:1px solid var(--stroke); border-radius:20px; padding:24px; box-shadow:0 10px 40px rgba(0,0,0,.35)}
  .top{display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap}
  .brand{display:flex; align-items:center; gap:10px; font-weight:800; letter-spacing:.2px}
  .badge{font-size:12px; padding:2px 8px; border:1px solid var(--stroke); border-radius:999px; color:var(--muted)}
  h1{margin:6px 0 8px; font-size:24px}
  p.muted{color:var(--muted); margin:0 0 18px}
  form { display:grid; gap:10px; }
  input[type="file"], input[type="number"], input[type="text"]{
    width:100%; background:#0d162a; color:var(--fg); border:1px solid var(--stroke);
    border-radius:12px; padding:10px 12px; outline:none
  }
  button.btn{display:inline-flex; align-items:center; gap:8px; padding:10px 14px; border-radius:12px;
    border:1px solid var(--stroke); background:var(--accent); color:#fff; font-weight:700; cursor:pointer}
  button:disabled{opacity:.6; cursor:not-allowed}
  .note{font-size:12px; color:var(--muted)}
  .row{display:flex; gap:10px; align-items:center; flex-wrap:wrap}

  /* Loader */
  #loader{display:none; margin-top:10px; align-items:center; gap:10px}
  .spinner{width:18px; height:18px; border:3px solid rgba(255,255,255,.25); border-top-color:white; border-radius:50%; animation:spin 1s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}

  /* progress bar illusion */
  .bar{position:relative; height:8px; background:#0e1830; border-radius:999px; overflow:hidden; width:100%}
  .bar span{position:absolute; left:-40%; width:40%; height:100%; background:linear-gradient(90deg, #4f8cff, #22c55e); animation:move 1.2s linear infinite}
  @keyframes move{0%{left:-40%} 100%{left:100%}}

  .alert{margin-top:10px; padding:10px 12px; border-radius:12px; font-weight:600; display:none}
  .alert.ok{background:rgba(34,197,94,.1); color:var(--accent2); border:1px solid rgba(34,197,94,.25)}
  .alert.err{background:rgba(239,68,68,.1); color:var(--danger); border:1px solid rgba(239,68,68,.25)}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="top">
      <div class="brand">
        <div style="width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,#4f8cff, #22c55e)"></div>
        <div>Mahamaya Stationery</div>
      </div>
      <div class="badge">PDF → JPG (Free up to 25 pages/MB)</div>
    </div>

    <h1>तेज़ और साफ़ PDF → इमेज कन्वर्टर</h1>
    <p class="muted">25 पेज या 25MB तक पूरी तरह मुफ्त। उससे ऊपर ₹10 लगेगा।</p>

    <form id="form">
      <input type="file" name="pdf_file" accept="application/pdf" required />
      <input type="number" name="dpi" value="150" min="72" max="300" placeholder="DPI (Quality)" />
      <input type="text" name="pages" placeholder="Page range (e.g. 1-5,7). Blank = all" />
      <div class="row">
        <button class="btn" type="submit" id="btnConvert">Convert</button>
        <span class="note" id="status"></span>
      </div>
      <div id="loader">
        <div class="spinner"></div>
        <div>Processing… please wait</div>
      </div>
      <div class="bar" id="bar" style="display:none"><span></span></div>
      <div class="alert ok" id="ok"></div>
      <div class="alert err" id="err"></div>
    </form>

    <p class="note" style="margin-top:10px">Tip: बहुत बड़े PDFs पर 150 DPI या पेज रेंज चुनना तेज़ रहता है।</p>
  </div>
</div>

<script>
const form = document.getElementById('form');
const btn = document.getElementById('btnConvert');
const loader = document.getElementById('loader');
const bar = document.getElementById('bar');
const ok = document.getElementById('ok');
const err = document.getElementById('err');
const statusEl = document.getElementById('status');

function show(el,msg){ el.textContent = msg; el.style.display='block'; }
function hide(el){ el.style.display='none'; }
function resetAlerts(){ hide(ok); hide(err); statusEl.textContent=''; }

async function downloadBlobAsFile(blob, filename){
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

form.addEventListener('submit', async (e)=>{
  e.preventDefault();
  resetAlerts();
  btn.disabled = true; loader.style.display='flex'; bar.style.display='block';
  try{
    const fd = new FormData(form);
    const res = await fetch('/convert', { method:'POST', body: fd });

    const ctype = res.headers.get('content-type') || '';
    if(!res.ok){
      const t = await res.text();
      throw new Error(t || ('HTTP '+res.status));
    }

    if(ctype.includes('application/json')){
      // Payment required
      const data = await res.json();
      if(data && data.error === 'payment_required'){
        await startPaymentFlow(data.order_id, data.amount, fd);
        return; // startPaymentFlow will call /convert_paid and handle download
      }else{
        throw new Error('Unexpected JSON response.');
      }
    }else{
      // Got ZIP directly
      const blob = await res.blob();
      await downloadBlobAsFile(blob, 'converted_images.zip');
      show(ok, 'हो गया! डाउनलोड शुरू हो गया।');
    }

  }catch(e){
    show(err, e.message || 'Conversion failed.');
  }finally{
    btn.disabled = false; loader.style.display='none'; bar.style.display='none';
  }
});

async function startPaymentFlow(orderId, amount, originalFormData){
  return new Promise((resolve, reject)=>{
    const options = {
      "key": "{{ key_id }}",
      "amount": amount,
      "currency": "INR",
      "name": "Mahamaya Stationery",
      "description": "PDF conversion (25+ pages or 25MB+)",
      "order_id": orderId,
      "handler": async function (response){
        try{
          // Call /convert_paid with original file + payment details
          const fd2 = new FormData();
          // copy original fields
          for (const [k,v] of originalFormData.entries()){ fd2.append(k,v); }
          fd2.append('razorpay_payment_id', response.razorpay_payment_id);
          fd2.append('razorpay_order_id', response.razorpay_order_id);
          fd2.append('razorpay_signature', response.razorpay_signature);

          const res2 = await fetch('/convert_paid', { method:'POST', body: fd2 });
          if(!res2.ok){
            const t = await res2.text();
            throw new Error(t || ('HTTP '+res2.status));
          }
          const blob = await res2.blob();
          await downloadBlobAsFile(blob, 'converted_images.zip');
          show(ok, 'Payment successful & download started!');
          resolve();
        }catch(e){
          show(err, e.message || 'Payment verify/convert failed.');
          reject(e);
        }finally{
          btn.disabled = false; loader.style.display='none'; bar.style.display='none';
        }
      },
      "theme": { "color": "#4f8cff" }
    };
    const rzp = new Razorpay(options);
    rzp.on('payment.failed', function (response){
      show(err, response.error && response.error.description ? response.error.description : 'Payment failed.');
      btn.disabled = false; loader.style.display='none'; bar.style.display='none';
      reject(new Error('Payment failed'));
    });
    rzp.open();
  });
}
</script>
</body>
</html>
"""


# ------------------ HELPERS ------------------
def parse_pages(pages_txt: str, total_pages: int) -> List[int]:
    if not pages_txt:
        return list(range(total_pages))
    out = set()
    for part in pages_txt.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                start, end = int(a), int(b)
                start = max(1, start)
                end = min(total_pages, end)
                if start <= end:
                    out.update(range(start - 1, end))
        elif part.isdigit():
            p = int(part)
            if 1 <= p <= total_pages:
                out.add(p - 1)
    return sorted(out) if out else list(range(total_pages))


def pdf_to_zip_bytes(pdf_bytes: bytes, dpi: int, page_idx_list: List[int]) -> bytes:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, "w") as zf:
        for i in page_idx_list:
            pix = doc[i].get_pixmap(dpi=dpi)
            img_bytes = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            img_io = io.BytesIO()
            img.save(img_io, format="JPEG", quality=90)
            img_io.seek(0)
            zf.writestr(f"page_{i+1}.jpg", img_io.read())
    mem_zip.seek(0)
    return mem_zip.read()


# ------------------ ROUTES ------------------
@app.route("/")
def home():
    return render_template_string(INDEX_HTML, key_id=RAZORPAY_KEY_ID)


@app.route("/healthz")
def health():
    return "OK"


@app.route("/convert", methods=["POST"])
def convert_free_or_require_payment():
    f = request.files.get("pdf_file")
    if not f:
        return ("No file uploaded", 400)

    # check size
    f.seek(0, os.SEEK_END)
    size_mb = f.tell() / (1024 * 1024)
    f.seek(0)
    pdf_bytes = f.read()

    # open pdf to count pages
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)

    if size_mb > FREE_MAX_MB or total_pages > FREE_MAX_PAGES:
        if not razorpay_client:
            return ("Payment required but Razorpay keys not configured", 500)
        order = razorpay_client.order.create(dict(
            amount=PAID_AMOUNT_PAISE,
            currency="INR",
            payment_capture="1",
            notes={"reason": "PDF>limits"}
        ))
        return jsonify({"error": "payment_required", "order_id": order["id"], "amount": PAID_AMOUNT_PAISE})

    # free path
    dpi = int(request.form.get("dpi", 150))
    pages_txt = request.form.get("pages", "").strip()
    page_idx = parse_pages(pages_txt, total_pages)

    zip_bytes = pdf_to_zip_bytes(pdf_bytes, dpi, page_idx)
    return send_file(
        io.BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name="converted_images.zip"
    )


@app.route("/convert_paid", methods=["POST"])
def convert_paid():
    # verify payment first
    if not razorpay_client:
        return ("Payment gateway not configured", 500)

    payment_id = request.form.get("razorpay_payment_id")
    order_id = request.form.get("razorpay_order_id")
    signature = request.form.get("razorpay_signature")

    if not (payment_id and order_id and signature):
        return ("Missing Razorpay parameters", 400)

    # Signature verification
    try:
        razorpay_client.utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature
        })
    except Exception as e:
        return (f"Signature verify failed: {e}", 400)

    # Optional: ensure payment is captured
    try:
        payment = razorpay_client.payment.fetch(payment_id)
        if payment.get("status") not in ("captured", "authorized"):
            return ("Payment not captured/authorized.", 400)
    except Exception:
        pass  # if fetch fails, still proceed after signature success

    # proceed to convert
    f = request.files.get("pdf_file")
    if not f:
        return ("No file uploaded", 400)

    f.seek(0, os.SEEK_END)
    f.seek(0)
    pdf_bytes = f.read()

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return ("Invalid PDF", 400)

    total_pages = len(doc)
    dpi = int(request.form.get("dpi", 150))
    pages_txt = request.form.get("pages", "").strip()
    page_idx = parse_pages(pages_txt, total_pages)

    zip_bytes = pdf_to_zip_bytes(pdf_bytes, dpi, page_idx)
    return send_file(
        io.BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name="converted_images.zip"
    )


# ------------------ MAIN ------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
