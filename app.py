from flask import Flask, request, send_file, render_template_string, after_this_request
import fitz  # PyMuPDF
from werkzeug.utils import secure_filename
import os, tempfile, zipfile, shutil
from threading import Timer

app = Flask(__name__)

# ---------------------- HTML (UI) ----------------------
INDEX_HTML = """
<!doctype html>
<html>
<head>
  <title>Mahamaya Stationery — PDF → JPG Converter</title>
</head>
<body>
  <h1>PDF → JPG Converter</h1>
  <p>Free up to 25 pages or 25 MB. Above that, ₹10 charge.</p>
  <form method="post" action="/convert" enctype="multipart/form-data">
    <input type="file" name="pdf_file" accept="application/pdf" required />
    <br><br>
    <label>DPI (Quality):</label>
    <select name="dpi">
      <option value="150">150 (Normal)</option>
      <option value="200">200</option>
      <option value="300">300 (High)</option>
    </select>
    <br><br>
    <button type="submit">Convert & Download ZIP</button>
  </form>
</body>
</html>
"""

# ---------------------- Routes ----------------------
@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

@app.route("/healthz")
def health():
    return "OK: Mahamaya server is running ✅"

@app.route("/convert", methods=["POST"])
def convert():
    pdf = request.files.get("pdf_file")
    if not pdf:
        return "No PDF uploaded", 400

    # File size check (25MB)
    pdf.stream.seek(0, os.SEEK_END)
    size = pdf.stream.tell()
    pdf.stream.seek(0)
    if size > 25 * 1024 * 1024:
        return "File too large (Free limit 25MB)", 400

    # Save temp
    tmp = tempfile.mkdtemp(prefix="pdf2img_")
    fname = secure_filename(pdf.filename)
    pdf_path = os.path.join(tmp, fname)
    pdf.save(pdf_path)

    try:
        doc = fitz.open(pdf_path)
        total_pages = len(doc)

        if total_pages > 25:
            return "Too many pages (Free limit 25)", 400

        dpi = int(request.form.get("dpi", "150"))
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)

        zip_path = os.path.join(tmp, "converted.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for i in range(total_pages):
                pix = doc[i].get_pixmap(matrix=mat)
                img_name = f"page_{i+1}.jpg"
                out_path = os.path.join(tmp, img_name)
                pix.save(out_path)
                zf.write(out_path, img_name)

        @after_this_request
        def cleanup(resp):
            Timer(5.0, shutil.rmtree, args=[tmp], kwargs={"ignore_errors": True}).start()
            return resp

        return send_file(zip_path, as_attachment=True, download_name="converted_images.zip")

    except Exception as e:
        return f"Error: {e}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)

