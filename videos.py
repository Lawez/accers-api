"""Workout videos for the Intensity app's Fitness screen, from the YouTube Data API.

Set YOUTUBE_API_KEY on Render (Google Cloud → enable "YouTube Data API v3" → Credentials → API key).
Optionally set YOUTUBE_CHANNEL_ID to show only videos from your own channel.
Results are cached for 12 hours: each search costs 100 of the free 10,000 daily quota units.
"""
import os
import time

import requests
from flask import Blueprint, jsonify, request

from coach import _verify_user

videos = Blueprint("videos", __name__)

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
CACHE_SECONDS = 12 * 3600

# Category id -> (label, search terms)
CATEGORIES = {
    "beginner": ("Beginner", "beginner full body workout at home no equipment"),
    "cardio": ("Cardio", "cardio workout at home"),
    "hiit": ("HIIT", "HIIT workout 20 minutes"),
    "strength": ("Strength", "full body strength workout dumbbells"),
    "running": ("Running", "running tips technique beginners"),
    "yoga": ("Yoga", "yoga for beginners"),
    "stretching": ("Stretching", "stretching mobility routine"),
    "healthy-eating": ("Healthy eating", "healthy meal prep nutrition tips"),
    "sleep": ("Sleep & recovery", "sleep better recovery tips relaxation"),
}

_cache = {}  # category -> (expires_at, items)


@videos.route("/videos/categories")
def categories():
    return jsonify({"categories": [{"id": k, "label": v[0]} for k, v in CATEGORIES.items()]})


@videos.route("/videos")
def list_videos():
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        return jsonify({"error": "Videos aren't set up yet (missing YOUTUBE_API_KEY on the server)."}), 503
    if _verify_user() is None:
        return jsonify({"error": "Please sign in again to see videos."}), 401

    category = request.args.get("category", "beginner")
    if category not in CATEGORIES:
        return jsonify({"error": "Unknown category."}), 400

    cached = _cache.get(category)
    if cached and cached[0] > time.time():
        return jsonify({"videos": cached[1], "cached": True})

    params = {
        "part": "snippet", "type": "video", "maxResults": 20, "q": CATEGORIES[category][1],
        "safeSearch": "strict", "videoEmbeddable": "true", "relevanceLanguage": "en", "key": api_key,
    }
    channel = os.environ.get("YOUTUBE_CHANNEL_ID")
    if channel:
        params["channelId"] = channel
        params["order"] = "date"
    else:
        params["videoDuration"] = "medium"  # 4–20 minutes: full workouts, not shorts

    try:
        response = requests.get(SEARCH_URL, params=params, timeout=20)
    except requests.RequestException:
        return jsonify({"error": "Couldn't reach YouTube. Please try again."}), 502
    if response.status_code != 200:
        print("YouTube error", response.status_code, response.text[:300])
        return jsonify({"error": "YouTube didn't return videos right now. Please try again later."}), 502

    items = []
    for item in response.json().get("items", []):
        video_id = item.get("id", {}).get("videoId")
        snippet = item.get("snippet", {})
        if not video_id:
            continue
        thumbs = snippet.get("thumbnails", {})
        thumb = (thumbs.get("medium") or thumbs.get("high") or thumbs.get("default") or {}).get("url", "")
        items.append({
            "id": video_id,
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "thumbnail": thumb,
            "published": snippet.get("publishedAt", ""),
        })
    _cache[category] = (time.time() + CACHE_SECONDS, items)
    return jsonify({"videos": items, "cached": False})
