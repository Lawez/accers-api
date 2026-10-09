# Accers prediction API

Flask API used by the Accers Android app (Predict screen).

`POST /predict` (form fields): `calorie_range`, `dream_weight`, `actual_weight`, `age`,
`duration`, `height`, `weather_conditions` (Sunny/Cloudy/Rainy), `gender` (Male/Female).

Returns JSON: `{"intensity": 4, "heart_rate": 195, "description": "..."}`

## Render settings
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`
