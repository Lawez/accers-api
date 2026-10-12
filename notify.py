"""Push notifications for Intensity's chat, kudos and comments (Firebase Cloud Messaging).

The app calls POST /notify {type, id} after sending a message, giving kudos or commenting.
The server checks the caller is really involved, then pushes to the other people's phones.

Set FIREBASE_SERVICE_ACCOUNT on Render to the full JSON of a service-account key
(Firebase console → Project settings → Service accounts → Generate new private key).
"""
import json
import os
import time
from collections import defaultdict, deque

from flask import Blueprint, jsonify, request

notify = Blueprint("notify", __name__)

_app = None
_recent = defaultdict(deque)  # uid -> timestamps, for rate limiting


def _firebase():
    """Initialises firebase-admin once from the FIREBASE_SERVICE_ACCOUNT env var (None if missing)."""
    global _app
    if _app is not None:
        return _app
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not raw:
        return None
    import firebase_admin
    from firebase_admin import credentials
    _app = firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
    return _app


def _caller_uid():
    from firebase_admin import auth
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        return auth.verify_id_token(header[len("Bearer "):])["uid"]
    except Exception:
        return None


def _rate_limited(uid, limit=60, window=600):
    now = time.time()
    q = _recent[uid]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def _send(db, uids, title, body, data):
    """Sends to every saved device of each user, and forgets tokens that are no longer valid."""
    from firebase_admin import messaging
    from google.cloud.firestore import ArrayRemove
    sent = 0
    for uid in set(uids):
        ref = db.collection("users").document(uid)
        tokens = (ref.get().to_dict() or {}).get("fcmTokens", [])[-5:]
        if not tokens:
            continue
        message = messaging.MulticastMessage(
            tokens=tokens,
            notification=messaging.Notification(title=title, body=body[:200]),
            data={k: str(v) for k, v in data.items()},
            android=messaging.AndroidConfig(priority="high", notification=messaging.AndroidNotification(channel_id="social")),
        )
        result = messaging.send_each_for_multicast(message)
        sent += result.success_count
        dead = [tokens[i] for i, r in enumerate(result.responses)
                if not r.success and getattr(r.exception, "code", "") in ("NOT_FOUND", "INVALID_ARGUMENT", "UNREGISTERED")]
        if dead:
            ref.update({"fcmTokens": ArrayRemove(dead)})
    return sent


@notify.route("/notify", methods=["POST"])
def send_notification():
    if _firebase() is None:
        return jsonify({"error": "Push isn't set up yet (missing FIREBASE_SERVICE_ACCOUNT on the server)."}), 503
    uid = _caller_uid()
    if uid is None:
        return jsonify({"error": "Please sign in again."}), 401
    if _rate_limited(uid):
        return jsonify({"error": "Too many notifications. Slow down a little."}), 429

    from firebase_admin import firestore
    db = firestore.client()
    body = request.get_json(silent=True) or {}
    kind, item_id = body.get("type"), str(body.get("id", ""))
    if not item_id:
        return jsonify({"error": "Missing id."}), 400

    if kind == "message":
        snap = db.collection("conversations").document(item_id).get()
        conv = snap.to_dict() if snap.exists else None
        if not conv or uid not in conv.get("members", []):
            return jsonify({"error": "Not a member of this chat."}), 403
        sender = conv.get("memberNames", {}).get(uid, "Someone")
        title = f"{sender} · {conv['title']}" if conv.get("isGroup") else sender
        recipients = [m for m in conv.get("members", []) if m != uid]
        sent = _send(db, recipients, title, conv.get("lastMessage", "New message"), {"type": "message", "id": item_id})
    elif kind in ("kudos", "comment"):
        snap = db.collection("activities").document(item_id).get()
        act = snap.to_dict() if snap.exists else None
        if not act or act.get("uid") == uid:
            return jsonify({"sent": 0})
        me = (db.collection("users").document(uid).get().to_dict() or {}).get("name", "Someone")
        what = act.get("exercise", "workout").lower()
        if kind == "kudos":
            # Only notify if the kudos really exists (stops spoofed notifications)
            if not db.collection("activities").document(item_id).collection("kudos").document(uid).get().exists:
                return jsonify({"sent": 0})
            title, text = f"👍 {me} gave you kudos", f"For your {what}"
        else:
            # Newest comments, filtered here (a where + order_by on different fields would need an index)
            recent = (db.collection("activities").document(item_id).collection("comments")
                      .order_by("time", direction="DESCENDING").limit(10).get())
            mine = [c.to_dict() for c in recent if c.to_dict().get("uid") == uid]
            if not mine:
                return jsonify({"sent": 0})
            title, text = f"💬 {me} commented on your {what}", mine[0].get("text", "")
        sent = _send(db, [act["uid"]], title, text, {"type": kind, "id": item_id})
    else:
        return jsonify({"error": "Unknown type."}), 400
    return jsonify({"sent": sent})
