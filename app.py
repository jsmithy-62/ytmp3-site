import os
from flask import Flask, request, send_file, render_template
import yt_dlp

app = Flask(__name__)

# Create downloads folder if it doesn't exist
DOWNLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/download", methods=["POST"])
def download():
    url = request.form.get("url")
    if not url:
        return "No URL provided", 400

    # Clean the downloads folder BEFORE downloading
    for file in os.listdir(DOWNLOAD_FOLDER):
        os.remove(os.path.join(DOWNLOAD_FOLDER, file))

    # yt-dlp options: use video title as filename
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(DOWNLOAD_FOLDER, "%(title)s.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }
        ],
        "quiet": True,
        "no_warnings": True,
    }

    # Try downloading
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "audio")
    except Exception as e:
        return f"Error downloading: {e}", 500

    # Build the expected MP3 filename
    mp3_path = os.path.join(DOWNLOAD_FOLDER, f"{title}.mp3")

    if not os.path.exists(mp3_path):
        return "Failed to create MP3 file", 500

    # Send the MP3 file to the user
    return send_file(mp3_path, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True)