# app.py
import os
import sys
import uuid
import json
import time
import socket
import logging
import threading
import subprocess
from pathlib import Path
from mimetypes import guess_type

from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    send_file,
    abort,
    make_response,
)

# Optional third-party imports
try:
    import qrcode
except Exception:
    qrcode = None

try:
    from yt_dlp import YoutubeDL
except Exception:
    YoutubeDL = None

# ---------- Configuration ----------
BASE_DIR = Path(__file__).parent.resolve()
DOWNLOADS_DIR = BASE_DIR / "downloads"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

PUBLIC_HOST = os.environ.get("PUBLIC_HOST")
if not PUBLIC_HOST:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        PUBLIC_HOST = f"http://{ip}:5000"
    except Exception:
        PUBLIC_HOST = "http://127.0.0.1:5000"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__, static_folder=str(STATIC_DIR), template_folder=str(TEMPLATES_DIR))

# ---------- Helpers ----------
def write_meta(job_dir: Path, meta: dict):
    job_dir.mkdir(parents=True, exist_ok=True)
    with open(job_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

def read_meta(job_dir: Path):
    try:
        with open(job_dir / "meta.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def external_url(path: str):
    return PUBLIC_HOST.rstrip("/") + path

def safe_filename(name: str):
    return "".join(c for c in name if c.isalnum() or c in " .-_()[]{}").strip()

# ---------- Routes ----------
@app.route("/", methods=["GET"])
def index():
    try:
        return render_template("index.html")
    except Exception as e:
        logging.exception("Failed to render index.html")
        return f"Index template not found or render error: {e}", 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "host": PUBLIC_HOST})

@app.route("/info", methods=["POST"])
def info():
    logging.info("INFO request Content-Type: %s", request.content_type)
    json_body = request.get_json(silent=True)
    url = request.values.get("url") or (json_body and json_body.get("url"))
    if not url:
        return jsonify({"error": "Missing url parameter"}), 400
    if YoutubeDL is None:
        return jsonify({"error": "yt-dlp not installed"}), 500
    try:
        with YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        logging.exception("Info extraction failed for %s", url)
        return jsonify({"error": str(e)}), 500
    if info is None:
        return jsonify({"error": "No info available"}), 500
    is_playlist = bool(info.get("entries"))
    title = info.get("title")
    duration = info.get("duration")
    thumbnail = info.get("thumbnail")
    return jsonify({"title": title, "duration": duration, "thumbnail": thumbnail, "is_playlist": is_playlist})

@app.route("/download", methods=["POST"])
def download():
    logging.info("DOWNLOAD request Content-Type: %s", request.content_type)
    json_body = request.get_json(silent=True)
    url = request.values.get("url") or (json_body and json_body.get("url"))
    fmt = request.values.get("format") or (json_body and json_body.get("format")) or "mp3"
    quality = request.values.get("quality") or (json_body and json_body.get("quality")) or ""
    normalize = bool(request.values.get("normalize") or (json_body and json_body.get("normalize")))
    trim = bool(request.values.get("trim") or (json_body and json_body.get("trim")))
    metadata = (request.values.get("metadata") is not None) or (json_body and json_body.get("metadata", True))

    if not url:
        return jsonify({"error": "Missing url parameter"}), 400

    job_id = uuid.uuid4().hex[:12]
    params = {"url": url, "format": fmt, "quality": quality, "normalize": normalize, "trim": trim, "metadata": metadata}
    job_dir = DOWNLOADS_DIR / job_id
    write_meta(job_dir, {"job_id": job_id, "status": "queued", "params": params, "created_at": int(time.time())})

    t = threading.Thread(target=process_job, args=(job_id, params), daemon=True)
    t.start()

    return jsonify({"job_id": job_id, "download_url": None})

@app.route("/share/<job_id>", methods=["GET"])
def share(job_id):
    job_dir = DOWNLOADS_DIR / job_id
    meta = read_meta(job_dir)
    if not meta:
        return jsonify({"error": "Job not found or not ready"}), 404
    if meta.get("status") == "done":
        return jsonify({
            "job_id": job_id,
            "status": meta.get("status"),
            "filename": meta.get("filename"),
            "title": meta.get("title"),
            "download_url": meta.get("download_url"),
            "dl_url": meta.get("dl_url"),
            "qr_url": meta.get("qr_url"),
        })
    if meta.get("status") == "error":
        return jsonify({"job_id": job_id, "status": "error", "error": meta.get("error")}), 500
    return jsonify({"job_id": job_id, "status": meta.get("status")})

@app.route("/dl/<job_id>", methods=["GET"])
def dl_job(job_id):
    job_dir = DOWNLOADS_DIR / job_id
    meta = read_meta(job_dir)
    if not meta or meta.get("status") != "done":
        return jsonify({"error": "Job not ready"}), 404
    filename = meta.get("filename")
    return serve_file(job_id, filename)

@app.route("/file/<job_id>/<path:filename>", methods=["GET"])
def serve_file(job_id, filename):
    job_dir = DOWNLOADS_DIR / job_id
    file_path = job_dir / filename
    if not file_path.exists():
        return abort(404)

    mime, _ = guess_type(str(file_path))
    if not mime:
        mime = "application/octet-stream"

    # Use send_file to let Flask handle conditional/range if supported
    try:
        resp = send_file(str(file_path), mimetype=mime, as_attachment=True, download_name=filename, conditional=True)
    except TypeError:
        resp = send_file(str(file_path), mimetype=mime, as_attachment=True, attachment_filename=filename)

    response = make_response(resp)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "no-store"

    # Ensure Content-Length is present (some send_file paths may omit it)
    if "Content-Length" not in response.headers:
        try:
            response.headers["Content-Length"] = str(file_path.stat().st_size)
        except Exception:
            pass

    if "Content-Disposition" not in response.headers:
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

# ---------- Background worker ----------
def process_job(job_id: str, params: dict):
    job_dir = DOWNLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    meta = {"job_id": job_id, "status": "queued", "created_at": int(time.time()), "params": params}
    write_meta(job_dir, meta)

    url = params.get("url")
    fmt = params.get("format", "mp3")
    quality = params.get("quality", "")
    logging.info("Job %s: starting processing for %s", job_id, url)
    meta.update({"status": "processing", "started_at": int(time.time())})
    write_meta(job_dir, meta)

    if YoutubeDL is None:
        err = "yt-dlp not available"
        logging.error(err)
        meta.update({"status": "error", "error": err})
        write_meta(job_dir, meta)
        return

    out_template = str(job_dir / "%(title).200s.%(ext)s")
    ydl_opts = {
        "outtmpl": out_template,
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best" if fmt == "mp3" else "bestvideo+bestaudio/best",
        "merge_output_format": "mp4" if fmt == "mp4" else None,
        "writethumbnail": True,
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        logging.exception("Job %s: yt-dlp failed: %s", job_id, e)
        meta.update({"status": "error", "error": str(e)})
        write_meta(job_dir, meta)
        return

    try:
        files = list(job_dir.glob("*"))
        media_file = None
        for ext in (".mp3", ".m4a", ".mp4", ".webm", ".mkv", ".opus"):
            for f in files:
                if f.suffix.lower() == ext:
                    media_file = f
                    break
            if media_file:
                break
        if not media_file and files:
            media_file = max(files, key=lambda p: p.stat().st_size)

        if not media_file:
            raise RuntimeError("No media file found after yt-dlp")

        title = info.get("title") if info else media_file.stem
        final_filename = media_file.name

        if fmt == "mp3" and media_file.suffix.lower() != ".mp3":
            out_file = job_dir / (safe_filename(title) + ".mp3")
            ffmpeg_cmd = ["ffmpeg", "-y", "-i", str(media_file), "-vn", "-ab", f"{quality or '320'}k", str(out_file)]
            logging.info("Job %s: ffmpeg convert: %s", job_id, " ".join(ffmpeg_cmd))
            subprocess.run(ffmpeg_cmd, check=False)
            if out_file.exists():
                final_filename = out_file.name
        elif fmt == "mp4" and media_file.suffix.lower() != ".mp4":
            out_file = job_dir / (safe_filename(title) + ".mp4")
            ffmpeg_cmd = ["ffmpeg", "-y", "-i", str(media_file), "-c", "copy", str(out_file)]
            logging.info("Job %s: ffmpeg remux: %s", job_id, " ".join(ffmpeg_cmd))
            subprocess.run(ffmpeg_cmd, check=False)
            if out_file.exists():
                final_filename = out_file.name

        dl_url = external_url(f"/dl/{job_id}")
        download_url = external_url(f"/file/{job_id}/{final_filename}")

        qr_path = job_dir / "qr.png"
        if qrcode is not None:
            try:
                qr = qrcode.QRCode(box_size=6, border=2)
                qr.add_data(dl_url)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                img.save(qr_path)
            except Exception:
                logging.exception("Failed to generate QR for job %s", job_id)

        meta.update({
            "status": "done",
            "finished_at": int(time.time()),
            "title": title,
            "filename": final_filename,
            "is_playlist": bool(info.get("entries")) if info else False,
            "download_url": download_url,
            "dl_url": dl_url,
            "qr_url": external_url(f"/file/{job_id}/qr.png") if qr_path.exists() else None,
        })
        write_meta(job_dir, meta)
        logging.info("Job %s finished: %s", job_id, final_filename)
    except Exception as e:
        logging.exception("Job %s processing failed: %s", job_id, e)
        meta.update({"status": "error", "error": str(e)})
        write_meta(job_dir, meta)

# ---------- Run ----------
if __name__ == "__main__":
    logging.info("Starting app with PUBLIC_HOST=%s", PUBLIC_HOST)
    app.run(host="0.0.0.0", port=5000, debug=False)