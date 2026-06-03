# Install dependencies (Windows)

The pipeline needs three external tools beyond Python:

1. **Tesseract** — reads the printed labels (to anchor the totals-band crop).
2. **Poppler** — converts each PDF page to an image (`pdf2image`).
3. **Ollama + a vision model** — reads the *handwritten* numbers locally.

Everything runs on your machine; nothing is uploaded.

---

## 1. Tesseract (OCR engine)

Option A — winget (simplest):

```powershell
winget install UB-Mannheim.TesseractOCR
```

Option B — manual installer:
https://github.com/UB-Mannheim/tesseract/wiki → download the 64-bit `.exe`.

During installation, under "Additional language data" select **Spanish** (the
E14 forms are in Spanish).

Typical install path: `C:\Program Files\Tesseract-OCR\tesseract.exe`

Verify:

```powershell
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs   # should list 'spa'
```

### Spanish language data (`spa.traineddata`)

The project reads it from `data/tessdata/spa.traineddata` (this folder is **not**
committed). Get it one of two ways:

- It is installed with Tesseract if you selected Spanish above — copy
  `spa.traineddata` from `C:\Program Files\Tesseract-OCR\tessdata\` into
  `data/tessdata\`, **or**
- Download it from https://github.com/tesseract-ocr/tessdata/raw/main/spa.traineddata
  and place it in `data/tessdata\`.

```powershell
mkdir data\tessdata -Force
Copy-Item "C:\Program Files\Tesseract-OCR\tessdata\spa.traineddata" data\tessdata\
```

---

## 2. Poppler (PDF → image)

```powershell
winget install oschwartz10612.Poppler
```

Or download from https://github.com/oschwartz10612/poppler-windows/releases and
add its `Library\bin` folder to the PATH.

---

## 3. Ollama + vision model (reads handwriting)

Install Ollama from https://ollama.com/download, then pull the vision model:

```powershell
ollama pull qwen2.5vl:7b
```

Ollama serves a local API at `http://127.0.0.1:11434` — `vision.py` talks to it.
A GPU helps a lot; on CPU it still works but is slower (~minutes per sheet).

To use a different model, set `VISION_MODEL` (e.g. `qwen2.5vl:32b` if you have it):

```powershell
$env:VISION_MODEL = "qwen2.5vl:32b"
```

---

## 4. Python libraries

```powershell
pip install --user -r requirements.txt
```

(The download + index stages use only the standard library; these packages are
for the read / review stages.)

---

## 5. If Tesseract or Poppler are NOT on the PATH

You do **not** need to edit `config.py`. Point to them with environment
variables for the session:

```powershell
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
$env:POPPLER_PATH  = "C:\path\to\poppler\Library\bin"
```

---

## 6. Test

```powershell
# Read a single downloaded PDF with the local vision model:
python vision.py data\forms\16\001\001\01\001_<hash>.pdf

# Or crop just the totals band (blank/null/unmarked/total) for inspection:
python crop_totals.py data\forms\16\001\001\01\001_<hash>.pdf
```

If `vision.py` prints candidate names and numbers, the stack is working. Then
launch the review station: `python review_server.py --dept 16`.
