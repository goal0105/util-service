
from downloader import Download
from flask import Flask, request, jsonify, abort
import os
import tempfile
from groq import Groq
from dotenv import load_dotenv

app = Flask(__name__)
downloader = Download()

load_dotenv()  # pulls variables from .env into process env

groq_client = Groq()

@app.route("/audio_transcription", methods=["POST"])
def audio_transcription():
    """
    POST  { "audio_url": "https://www.youtube.com/watch?v=eWRfhZUzrAc" }
    └─▶  { "text": "…transcript…" }
    """
    if not request.is_json:
        abort(400, description="Body must be JSON")

    audio_url = request.get_json(silent=True, force=True).get("audio_url")
    if not audio_url:
        abort(400, description="`audio_url` is required")

    # ── 1.  Download the file safely to a temp location ────────────────
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloaded_path = downloader.download_youtube_audio(audio_url, temp_dir)
    except Exception as e:
        abort(400, description=f"Downloading Youtube Failed\n: {e}")
        
    # ── 2.  Transcribe with Whisper ────────────────────────────────────
    try:
        with open(downloaded_path, "rb") as file:
            translation = groq_client.audio.translations.create(
                file=(downloaded_path, file.read()),
                model="whisper-large-v3",
                response_format="json",  # Optional
                temperature=0.0  # Optional
                )
        
            print(translation.text)
            
    except Exception as e:
        abort(400, description=f"Couldn't transcribe: {e}")
    finally:
        os.remove(downloaded_path)   # cleanup no matter what

    return jsonify(transcription=translation.text), 200

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
