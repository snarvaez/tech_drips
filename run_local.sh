python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# to ingest youtube videos
python -m search_app.youtube_loop --max-new 40 --delay 8 --batch-pause 600

# to run the web ui
flask --app wsgi:app run --host 127.0.0.1 --port 5000