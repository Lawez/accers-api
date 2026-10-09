"""AI fitness coach chat for the Intensity app, powered by Groq.

The Groq API key lives only on the server (GROQ_API_KEY environment variable on Render),
never in the Android app. Each request must carry a Firebase ID token from a signed-in
user of our Firebase project, so the endpoint can't be used by anyone else.
"""
import os
import time
from collections import defaultdict, deque

import requests
from flask import Blueprint, jsonify, request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

coach = Blueprint("coach", __name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Change on Render (GROQ_MODEL) if Groq retires this model; see console.groq.com/docs/models
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "accers-71aca")

MAX_HISTORY = 12          # messages of conversation sent to the model
MAX_MESSAGE_CHARS = 2000
RATE_LIMIT = 30           # requests per user...
RATE_WINDOW_SECONDS = 600  # ...per 10 minutes

SYSTEM_PROMPT = """You are "Coach", the friendly AI coach inside Intensity, a fitness app.
You help with exercise, training plans, running/walking/cycling, strength and mobility,
recovery and sleep, hydration, nutrition for an active lifestyle, motivation and healthy habits.

Style:
- Warm, encouraging and practical. Use the user's name occasionally if you know it.
- Keep answers short: usually 2–5 sentences or a few bullet points. Replies may be read aloud,
  so avoid tables, long lists and heavy markdown.
- Use the app data in CONTEXT to personalise answers (their stats, goals, recent workouts,
  achievements, and any workout in progress: time, distance, % of goal, what's remaining).
  Never invent numbers that aren't in CONTEXT.

Scope and safety:
- If asked about something unrelated to fitness or health, briefly say you're a fitness coach
  and steer back to how you can help.
- You are not a doctor. For pain, injury, chest pain, dizziness, pregnancy, eating disorders,
  medication or medical conditions, give general guidance only and recommend a doctor or
  physiotherapist. For emergencies, tell them to call emergency services.
- Never recommend extreme diets, dangerous weight loss, or supplements/drugs beyond basics.

App actions: when the user clearly asks you to do one of these, add the tag on its own final
line (the app performs it and hides the tag). Use at most one tag.
[[ACTION:play_music]]  - play workout music on Spotify
[[ACTION:start_workout:<Activity>]] - start tracking; Activity is one of Walking, Brisk walking,
  Running, Jogging, Cycling, Hiking, HIIT, Strength training, Yoga, Stretching, Skipping rope
[[ACTION:pause_workout]]  [[ACTION:resume_workout]]  [[ACTION:finish_workout]]
[[ACTION:open:<screen>]] - screen is one of workout_log, track, achievements, together, music, profile, reminders, graphs
"""

_token_cache = {}  # token -> (uid, expires_at)
_requests_by_user = defaultdict(deque)
_google_request = google_requests.Request()


def _verify_user():
    """Returns the Firebase uid of the caller, or None if the token is missing/invalid."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[len("Bearer "):]
    cached = _token_cache.get(token)
    if cached and cached[1] > time.time():
        return cached[0]
    try:
        claims = id_token.verify_firebase_token(token, _google_request, audience=FIREBASE_PROJECT_ID)
    except Exception:
        return None
    uid = claims.get("user_id") or claims.get("sub")
    if len(_token_cache) > 1000:
        _token_cache.clear()
    _token_cache[token] = (uid, min(claims.get("exp", 0), time.time() + 300))
    return uid


def _rate_limited(uid):
    now = time.time()
    recent = _requests_by_user[uid]
    while recent and recent[0] < now - RATE_WINDOW_SECONDS:
        recent.popleft()
    if len(recent) >= RATE_LIMIT:
        return True
    recent.append(now)
    return False


@coach.route("/chat", methods=["POST"])
def chat():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return jsonify({"error": "The coach isn't set up yet (missing GROQ_API_KEY on the server)."}), 503

    uid = _verify_user()
    if uid is None:
        return jsonify({"error": "Please sign in again to use the coach."}), 401
    if _rate_limited(uid):
        return jsonify({"error": "You're sending messages very quickly. Please wait a few minutes."}), 429

    body = request.get_json(silent=True) or {}
    history = []
    for m in (body.get("messages") or [])[-MAX_HISTORY:]:
        role = m.get("role")
        content = str(m.get("content", ""))[:MAX_MESSAGE_CHARS]
        if role in ("user", "assistant") and content.strip():
            history.append({"role": role, "content": content})
    if not history or history[-1]["role"] != "user":
        return jsonify({"error": "No message to answer."}), 400

    context = str(body.get("context", ""))[:4000]
    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\nCONTEXT (from the app):\n" + context}] + history

    try:
        response = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": GROQ_MODEL, "messages": messages, "temperature": 0.6, "max_tokens": 600},
            timeout=45,
        )
    except requests.RequestException:
        return jsonify({"error": "Couldn't reach the AI service. Please try again."}), 502

    if response.status_code != 200:
        # Log details server-side only; never send the key or raw upstream errors to the app
        print("Groq error", response.status_code, response.text[:500])
        message = "The AI service is busy. Please try again in a moment." if response.status_code == 429 \
            else "The AI service returned an error. Please try again."
        return jsonify({"error": message}), 502

    reply = response.json()["choices"][0]["message"]["content"].strip()
    return jsonify({"reply": reply})
