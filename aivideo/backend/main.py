"""Backend perantara: API key AI hanya ada di sini, tidak pernah di APK."""
import json, os, time
from collections import defaultdict
import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

app = FastAPI(title="AI Video Backend")
KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("SCRIPT_MODEL", "claude-sonnet-5-5")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
TOKEN = os.environ.get("APP_TOKEN", "")
_hits: dict[str, list[float]] = defaultdict(list)

def guard(request: Request, auth: str, limit=20, per=60):
    if not TOKEN or auth != f"Bearer {TOKEN}":
        raise HTTPException(401, "Tidak diizinkan")
    ip, now = request.client.host, time.time()
    _hits[ip] = [t for t in _hits[ip] if now - t < per]
    if len(_hits[ip]) >= limit:
        raise HTTPException(429, "Terlalu banyak permintaan")
    _hits[ip].append(now)

class ScriptReq(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    duration: int = Field(30, ge=10, le=90)
    language: str = "Indonesia"
    style: str = "Edukasi"

@app.post("/api/script")
async def script(req: ScriptReq, request: Request, authorization: str = Header(default="")):
    guard(request, authorization)
    prompt = (
        f"Buat script video pendek berdurasi {req.duration} detik tentang: {req.topic}\n"
        f"Bahasa: {req.language}. Gaya: {req.style}. Struktur: hook, 2-3 informasi, kesimpulan, CTA.\n"
        "Jangan membuat klaim kesehatan berlebihan. Isi field visual dengan 2-4 kata kunci BAHASA INGGRIS untuk mencari foto (contoh: turmeric root).\n"
        "Balas HANYA JSON valid tanpa teks lain:\n"
        '{"title":str,"description":str,"hashtags":[str],"segments":[{"start":int,"end":int,'
        '"type":"hook|info|conclusion|cta","narration":str,"visual":str}]}'
    )
    if KEY:
        text = await _anthropic(prompt)
    elif GEMINI_KEY:
        text = await _gemini(prompt)
    else:
        raise HTTPException(501, "Isi GEMINI_API_KEY (gratis) atau ANTHROPIC_API_KEY")
    text = text.strip().removeprefix("```json").removesuffix("```").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise HTTPException(502, "Format respons AI tidak valid")
    if not IMG_KEY and (PIXABAY_KEY or PEXELS_KEY):  # kredit foto stok
        sumber = "Pixabay (pixabay.com)" if PIXABAY_KEY else "Pexels (pexels.com)"
        data["description"] = (str(data.get("description", "")) + f"\n\nFoto: {sumber}").strip()
    return data

async def _anthropic(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": KEY, "anthropic-version": "2023-06-01"},
            json={"model": MODEL, "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]},
        )
    if r.status_code != 200:
        raise HTTPException(502, "Penyedia AI gagal")
    return "".join(b.get("text", "") for b in r.json()["content"])

async def _gemini(prompt: str) -> str:
    """Gratis lewat Google AI Studio (kunci tanpa kartu kredit, dibatasi laju)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(url, headers={"x-goog-api-key": GEMINI_KEY},
                         json={"contents": [{"parts": [{"text": prompt}]}],
                               "generationConfig": {"responseMimeType": "application/json"}})
    if r.status_code != 200:
        raise HTTPException(502, "Penyedia AI gagal" + (" (batas gratis tercapai, coba lagi nanti)" if r.status_code == 429 else ""))
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        raise HTTPException(502, "Respons AI kosong")
    return "".join(p.get("text", "") for p in parts)

# Tambahkan /api/tts dan /api/image dengan pola yang sama (guard + panggil provider pilihan Anda).


# ---- Suara (TTS) dan gambar: provider diatur lewat variabel lingkungan ----
# Format default mengikuti API gaya OpenAI. Untuk penyedia lain, ubah dua fungsi _call_tts/_call_image.
import base64
from fastapi import Response

TTS_URL = os.environ.get("TTS_URL", "https://api.openai.com/v1/audio/speech")
TTS_KEY = os.environ.get("TTS_KEY", "")
TTS_MODEL = os.environ.get("TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.environ.get("TTS_VOICE", "alloy")
IMG_URL = os.environ.get("IMG_URL", "https://api.openai.com/v1/images/generations")
IMG_KEY = os.environ.get("IMG_KEY", "")
IMG_MODEL = os.environ.get("IMG_MODEL", "gpt-image-1")
PEXELS_KEY = os.environ.get("PEXELS_KEY", "")
PIXABAY_KEY = os.environ.get("PIXABAY_KEY", "")
EDGE_VOICE = os.environ.get("EDGE_VOICE", "id-ID-GadisNeural")  # pria: id-ID-ArdiNeural
VOICES = {"female": ("id-ID-GadisNeural", "nova"), "male": ("id-ID-ArdiNeural", "onyx")}  # (Edge, OpenAI)
SIZES = {"9:16": "1024x1536", "4:5": "1024x1536", "16:9": "1536x1024", "1:1": "1024x1024"}

class TtsReq(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    voice: str = "default"

class ImageReq(BaseModel):
    prompt: str = Field(min_length=3, max_length=1000)
    ratio: str = "9:16"

async def _call_tts(text: str, voice: str) -> bytes:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(TTS_URL, headers={"Authorization": f"Bearer {TTS_KEY}"},
                         json={"model": TTS_MODEL, "input": text, "voice": voice})
    if r.status_code != 200:
        raise HTTPException(502, "Penyedia suara gagal")
    return r.content

async def _call_image(prompt: str, ratio: str) -> bytes:
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(IMG_URL, headers={"Authorization": f"Bearer {IMG_KEY}"},
                         json={"model": IMG_MODEL, "prompt": prompt, "size": SIZES.get(ratio, "1024x1536")})
    if r.status_code != 200:
        raise HTTPException(502, "Penyedia gambar gagal")
    return base64.b64decode(r.json()["data"][0]["b64_json"])

async def _edge_tts(text: str, voice: str) -> bytes:
    """Suara gratis lewat layanan baca-teks Microsoft Edge (tidak resmi, kadang ditolak 403)."""
    import edge_tts
    buf = bytearray()
    try:
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                buf += chunk["data"]
    except Exception:
        raise HTTPException(502, "Penyedia suara gratis menolak permintaan. Coba lagi atau isi TTS_KEY.")
    if not buf:
        raise HTTPException(502, "Suara kosong")
    return bytes(buf)

async def _pexels(query: str, ratio: str) -> tuple[bytes, str]:
    """Foto stok gratis dari Pexels. Kredit ke Pexels dan fotografer wajib ditampilkan."""
    orient = "landscape" if ratio == "16:9" else "square" if ratio == "1:1" else "portrait"
    words = query.split()
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        for q in (query, " ".join(words[:2]), "nature"):
            if not q:
                continue
            r = await c.get("https://api.pexels.com/v1/search", headers={"Authorization": PEXELS_KEY},
                            params={"query": q, "orientation": orient, "per_page": 1})
            if r.status_code != 200:
                raise HTTPException(502, "Pencarian foto gagal")
            photos = r.json().get("photos", [])
            if photos:
                img = await c.get(photos[0]["src"]["large2x"])
                if img.status_code != 200:
                    raise HTTPException(502, "Unduh foto gagal")
                return img.content, img.headers.get("content-type", "image/jpeg")
    raise HTTPException(404, "Foto tidak ditemukan")

async def _pixabay(query: str, ratio: str) -> tuple[bytes, str]:
    """Foto stok gratis dari Pixabay (kunci gratis dari pixabay.com/api/docs)."""
    orient = "horizontal" if ratio == "16:9" else "all" if ratio == "1:1" else "vertical"
    words = query.split()
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        for q in (query, " ".join(words[:2]), "nature"):
            if not q:
                continue
            r = await c.get("https://pixabay.com/api/", params={
                "key": PIXABAY_KEY, "q": q[:100], "image_type": "photo",
                "orientation": orient, "safesearch": "true", "per_page": 3})
            if r.status_code != 200:
                raise HTTPException(502, "Pencarian foto gagal")
            hits = r.json().get("hits", [])
            if hits:
                img = await c.get(hits[0]["largeImageURL"])
                if img.status_code != 200:
                    raise HTTPException(502, "Unduh foto gagal")
                return img.content, img.headers.get("content-type", "image/jpeg")
    raise HTTPException(404, "Foto tidak ditemukan")

@app.post("/api/tts")
async def tts(req: TtsReq, request: Request, authorization: str = Header(default="")):
    guard(request, authorization, limit=10)
    edge_v, oa_v = VOICES.get(req.voice, (EDGE_VOICE, TTS_VOICE))
    if TTS_KEY:
        return Response(await _call_tts(req.text, oa_v), media_type="audio/mpeg")
    return Response(await _edge_tts(req.text, edge_v), media_type="audio/mpeg")

@app.post("/api/image")
async def image(req: ImageReq, request: Request, authorization: str = Header(default="")):
    guard(request, authorization, limit=30)
    if IMG_KEY:
        return Response(await _call_image(req.prompt, req.ratio), media_type="image/png")
    if PIXABAY_KEY:
        data, ctype = await _pixabay(req.prompt, req.ratio)
        return Response(data, media_type=ctype)
    if PEXELS_KEY:
        data, ctype = await _pexels(req.prompt, req.ratio)
        return Response(data, media_type=ctype)
    raise HTTPException(501, "Isi PIXABAY_KEY (gratis), PEXELS_KEY, atau IMG_KEY")


# ---- Musik latar: file lagu BERLISENSI milik Anda di folder music/ (calm.mp3, cinematic.mp3, energetic.mp3) ----
from fastapi.responses import FileResponse

MUSIC_DIR = os.environ.get("MUSIC_DIR", "music")

@app.get("/api/music/{mood}")
async def music(mood: str, request: Request, authorization: str = Header(default="")):
    guard(request, authorization)
    if mood not in {"calm", "cinematic", "energetic"}:
        raise HTTPException(404, "Suasana musik tidak dikenal")
    path = os.path.join(MUSIC_DIR, f"{mood}.mp3")
    if not os.path.isfile(path):
        raise HTTPException(404, "Letakkan lagu berlisensi di folder music/")
    return FileResponse(path, media_type="audio/mpeg")
