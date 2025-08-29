import os
import io
import zipfile
from flask import Flask, request, send_file, render_template_string, jsonify
import fitz  # PyMuPDF
from PIL import Image
import razorpay

app = Flask(__name__)

# Razorpay keys (from Render Environment Variables)
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")

razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


# ------------------ FRONTEND HTML ------------------
INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Mahamaya Stationery — PDF → JPG Converter</title>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<style>
  body { font-family: Arial, sans-serif; background:#f4f4f9; margin:0; padding:0; }
  .container { max-width:600px; margin:50px auto; background:white; padding:20px; border-radius:10px; box-shadow:0 2px 6px rgba(0,0,0,0.2);}
  h1 { text-align:center; color:#333; }
  .muted { text-align:center; color:#666; font-size:14px; }
  form { margin-top:20px; text-align:center; }
  input[type=file], input[type=number], input[type=text] { margin:10px 0; padding:8px; width:90%; }
  button { padding:10px 20px; background:#007bff; color:white; border:none; border-radius:5px; cursor:pointer; }
  button:hover { background:#0056b3; }
  #loading { display:none; text-align:center; font-size:16px; color:#007bff; margin-top:15px; }
</style>
</head>
<body>
<div class="container">
  <h1>Mahamaya Stationery — PDF → JPG Converter</h1>
  <p class="muted">Free up to 25 pages or 25 MB. Above that, ₹10 charge.</p>
  
  <form id="uploadForm" method="post" enctype="multipart/form-data" action="/convert">
    <input type="file" name="pdf_file" required />
    <br>
    DPI (Quality): <input type="number" name="dpi" value="150" />
    <br>
    <input type="text" name="pages" placeholder="Page range (e.g. 1-5,7)" />
    <br>
    <button type="submit">Convert & Download ZIP</button>
  </form>

  <div id="loading">⏳ Converting... Please wait...</div>
</div>

<script>
document.getElementById("uploadForm").addEventListener("submit", function(){
    document.getElementById("loading").style.display = "block";
});

// Razorpay Checkout Example
function startPayment(orderId, amount) {
    var options = {
        "key": "{{ key_id }}", 
        "amount": amount, 
        "currency": "INR",
        "name": "Mahamaya Stationery",
        "description": "PDF conversion (25+ pages or 25MB+)",
        "order_id": orderId,
        "handler": function (response){
            alert("✅ Payment successful! Payment ID: " + response.razorpay_payment_id);
            window.location.reload();
        },
        "theme": { "color": "#007bff" }
    };
    var rzp1 = new Razorpay(options);
    rzp1.open();
}
</script>
</body>
</html>
"""


# ------------------ ROUTES ------------------

@app.route("/")
def index():
    return render_template_string(INDEX_HTML, key_id=RAZORPAY_KEY_ID)


@app.route("/convert", methods=["POST"])
def convert_pdf():
    pdf_file = request.files["pdf_file"]
    dpi = int(request.form.get("dpi", 150))
    pages = request.form.get("pages", "").strip()

    # Limit check: 25 MB
    pdf_file.seek(0, os.SEEK_END)
    size_mb = pdf_file.tell() / (1024 * 1024)
    pdf_file.seek(0)

    # Open PDF
    doc = fitz.open(stream=pdf_file.read(), filetype="pdf")
    total_pages = len(doc)

    if size_mb > 25 or total_pages > 25:
        # Create Razorpay Order for ₹10
        order = razorpay_client.order.create(dict(amount=1000, currency="INR", payment_capture="1"))
        return jsonify({"error": "payment_required", "order_id": order["id"], "amount": 1000})

    # Convert Pages
    if pages:
        page_numbers = []
        for part in pages.split(","):
            if "-" in part:
                start, end = map(int, part.split("-"))
                page_numbers.extend(range(start - 1, end))
            else:
                page_numbers.append(int(part) - 1)
    else:
        page_numbers = list(range(total_pages))

    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, "w") as zf:
        for i in page_numbers:
            pix = doc[i].get_pixmap(dpi=dpi)
            img_bytes = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            img_io = io.BytesIO()
            img.save(img_io, format="JPEG")
            img_io.seek(0)
            zf.writestr(f"page_{i+1}.jpg", img_io.read())

    mem_zip.seek(0)
    return send_file(mem_zip, mimetype="application/zip", as_attachment=True, download_name="converted_images.zip")


# ------------------ MAIN ------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
