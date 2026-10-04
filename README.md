# Perceptify

*See how they see it.*

Perceptify reads a YouTube comment section in one click and tells you what the audience actually feels: mood, themes with receipts, anger at the creator, whether the video kept its promise, and the exact moments people reacted to.

## How it works
1. A button appears next to the **Sort by** control in the comments header (floating fallback if YouTube changes its layout). The toolbar icon also opens the panel.
2. A local FastAPI backend collects the title, description, top ~500 comments (weighted by likes) and the transcript.
3. Timestamps people mention ("3:42") are clustered into reaction moments and matched to what was said at that second.
4. Gemini returns structured JSON. Every quote shown is mapped back to a **real comment**, never model-written.

## Setup (macOS)
```bash
cd backend
cp .env.example .env     # open .env and paste your keys
chmod +x run.sh
./run.sh                 # first run installs everything, serves on http://127.0.0.1:8000
```
Check http://127.0.0.1:8000/health.

Load the extension: `brave://extensions` (or `chrome://extensions`) → Developer mode → **Load unpacked** → select the `extension` folder.

Keys: a Gemini API key (free, Google AI Studio). A YouTube Data API key is optional; without it a no-key scraper is used.
`MOCK_MODE=1` in `.env` shows demo data without any keys.

## Stack
Chrome MV3 extension (vanilla JS, Shadow DOM, bundled Geist + Instrument Serif fonts) · FastAPI · Gemini · YouTube Data API v3 / youtube-comment-downloader · youtube-transcript-api
