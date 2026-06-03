# Install OCR (Tesseract + Python dependencies) — Windows

## 1. Tesseract (OCR engine)

Option A — winget (simplest):

```powershell
winget install UB-Mannheim.TesseractOCR
```

Option B — manual installer:
https://github.com/UB-Mannheim/tesseract/wiki  -> download the 64-bit `.exe`.

During installation, under "Additional language data" select the language(s) of
the tally sheets you will process — for the E14 source, select **Spanish** (or
later copy `spa.traineddata` into the `tessdata` folder).

Typical install path:
`C:\Program Files\Tesseract-OCR\tesseract.exe`

Verify:

```powershell
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs   # should list 'spa'
```

## 2. Poppler (converts PDF -> image, used by pdf2image)

```powershell
winget install oschwartz10612.Poppler
```

Or download from https://github.com/oschwartz10612/poppler-windows/releases
and add its `Library\bin` folder to the PATH.

## 3. Python libraries

```powershell
pip install --user pytesseract pdf2image pillow
```

## 4. If tesseract is NOT on the PATH

Edit `config.py` and set the absolute paths in `TESSERACT_CMD` and `POPPLER_PATH`
(placeholders are already there). The scripts use them if they are defined.

## 5. Test

```powershell
python ocr.py --dept 16 --limit 1 --debug   # OCR a single PDF and print the raw text
```

Inspect that raw text: use it to tune the number-extraction regex against the
real tally sheet layout.
