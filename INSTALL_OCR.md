# Instalar dependencias (Windows)

**🇪🇸 Español** · [🇬🇧 English](INSTALL_OCR.en.md)


El pipeline necesita tres herramientas externas además de Python:

1. **Tesseract** — lee los rótulos impresos (para anclar el recorte de la banda de totales).
2. **Poppler** — convierte cada página del PDF en imagen (`pdf2image`).
3. **Ollama + un modelo de visión** — lee los números *manuscritos* localmente.

Todo corre en tu máquina; no se sube nada.

> Nota: esto es solo para el **pipeline local** (con visión). La **app web
> pública** (`public_server.py`) NO necesita nada de esto — corre solo con la
> librería estándar de Python.

---

## 1. Tesseract (motor de OCR)

Opción A — winget (lo más simple):

```powershell
winget install UB-Mannheim.TesseractOCR
```

Opción B — instalador manual:
https://github.com/UB-Mannheim/tesseract/wiki → descarga el `.exe` de 64 bits.

Durante la instalación, en "Additional language data" selecciona **Spanish** (los
formularios E14 están en español).

Ruta típica de instalación: `C:\Program Files\Tesseract-OCR\tesseract.exe`

Verifica:

```powershell
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs   # debe listar 'spa'
```

### Datos del idioma español (`spa.traineddata`)

El proyecto lo lee desde `data/tessdata/spa.traineddata` (esa carpeta **no** se
versiona). Consíguelo de una de dos formas:

- Se instala con Tesseract si elegiste Spanish arriba — copia `spa.traineddata`
  desde `C:\Program Files\Tesseract-OCR\tessdata\` a `data/tessdata\`, **o**
- Descárgalo de https://github.com/tesseract-ocr/tessdata/raw/main/spa.traineddata
  y ponlo en `data/tessdata\`.

```powershell
mkdir data\tessdata -Force
Copy-Item "C:\Program Files\Tesseract-OCR\tessdata\spa.traineddata" data\tessdata\
```

---

## 2. Poppler (PDF → imagen)

```powershell
winget install oschwartz10612.Poppler
```

O descárgalo de https://github.com/oschwartz10612/poppler-windows/releases y
agrega su carpeta `Library\bin` al PATH.

---

## 3. Ollama + modelo de visión (lee la letra manuscrita)

Instala Ollama desde https://ollama.com/download, luego descarga el modelo de
visión:

```powershell
ollama pull qwen2.5vl:7b
```

Ollama sirve una API local en `http://127.0.0.1:11434` — `vision.py` habla con
ella. Una GPU ayuda mucho; en CPU también funciona pero es más lento (~minutos
por acta).

Para usar otro modelo, define `VISION_MODEL` (ej. `qwen2.5vl:32b` si lo tienes):

```powershell
$env:VISION_MODEL = "qwen2.5vl:32b"
```

---

## 4. Librerías de Python

```powershell
pip install --user -r requirements.txt
```

(Las etapas de descarga + índice usan solo la librería estándar; estos paquetes
son para las etapas de lectura / revisión.)

---

## 5. Si Tesseract o Poppler NO están en el PATH

**No** necesitas editar `config.py`. Apúntales con variables de entorno para la
sesión:

```powershell
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
$env:POPPLER_PATH  = "C:\ruta\a\poppler\Library\bin"
```

---

## 6. Prueba

```powershell
# Leer un PDF descargado con el modelo de visión local:
python vision.py data\forms\16\001\001\01\001_<hash>.pdf

# O recortar solo la banda de totales (blanco/nulos/no-marcados/total) para inspeccionar:
python crop_totals.py data\forms\16\001\001\01\001_<hash>.pdf
```

Si `vision.py` imprime nombres de candidatos y números, el stack funciona. Luego
lanza la estación de revisión: `python review_server.py --dept 16`.
