#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask, render_template, jsonify, request, redirect, url_for, session, Response
from flask_dance.contrib.google import make_google_blueprint, google
from gevent.pywsgi import WSGIServer
import os
import json
import time

# --- App Configuration ---
app = Flask(__name__)

file_path = "config.json"
config = {}

if not os.path.exists(file_path):
    raise FileNotFoundError(f"Configuration file '{file_path}' not found.")

with open(file_path, 'r') as file:
    try:
        config = json.load(file)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format: {e}")
        
# A secret key is required for session management
app.secret_key = config.get("app_secret_key")

# --- Google OAuth 2.0 Configuration ---
# IMPORTANT: You must set these environment variables with your own Google OAuth credentials
# 1. Go to https://console.cloud.google.com/apis/credentials
# 2. Create an "OAuth 2.0 Client ID" for a "Web application".
# 3. For "Authorized redirect URIs", add: http://127.0.0.1:5000/login/google/authorized
# 4. Set the following environment variables before running the app:
#    export GOOGLE_OAUTH_CLIENT_ID="YOUR_CLIENT_ID"
#    export GOOGLE_OAUTH_CLIENT_SECRET="YOUR_CLIENT_SECRET"
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1' # Use for development only (HTTP)
google_bp = make_google_blueprint(
    client_id=config.get("client_id"),
    client_secret=config.get("client_secret"),
    scope=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ],
    redirect_to="index" # Redirect to the main page after login
)
app.register_blueprint(google_bp, url_prefix="/login")

# --- Data Storage ---
def load_buildings_data():
    """Loads the building data from a JSON file."""
    try:
        with open('buildings.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

BUILDINGS = load_buildings_data()
# This will now store progress for multiple users, keyed by their Google ID.
# In a real app, this would be a persistent database (e.g., Firestore, PostgreSQL).
user_data_store = {}

def get_current_user_progress():
    """Gets or initializes progress for the currently logged-in user."""
    if not google.authorized:
        return None
        
    resp = google.get("/oauth2/v2/userinfo")
    assert resp.ok, resp.text
    user_info = resp.json()
    user_id = user_info['id']

    if user_id not in user_data_store:
        user_data_store[user_id] = {
            'current_building_id': 1,
            'score': 0,
            'start_time': None,
            'visited': [],
            'name': user_info.get('name', 'User'),
            'email': user_info.get('email', 'No email provided')
        }
    return user_data_store[user_id]

# --- Routes ---

@app.route('/')
def index():
    """Renders the main tour page or the login page."""
    if not google.authorized:
        return render_template('login.html')

    user_progress = get_current_user_progress()
    return render_template('index.html', buildings=BUILDINGS, initial_progress=user_progress)

@app.route('/logout')
def logout():
    """Logs the user out."""
    # This token removal is a bit of a manual process for flask-dance
    # to ensure the session is cleared properly.
    if 'google_oauth_token' in session:
        del session['google_oauth_token']
    return redirect(url_for('index'))

@app.route('/api/visit_building', methods=['POST'])
def visit_building():
    """API endpoint to handle visiting a building."""
    if not google.authorized:
        return jsonify({'error': 'User not authenticated'}), 401
    
    user_progress = get_current_user_progress()
    data = request.get_json()
    building_id = data.get('building_id')

    if not building_id or not isinstance(building_id, int):
        return jsonify({'error': 'Invalid building ID'}), 400
    
    if user_progress['current_building_id'] != building_id:
        return jsonify({'error': 'Please visit the correct building on the tour route.'}), 400

    if building_id in user_progress['visited']:
        return jsonify({'error': 'You have already visited this building.'}), 400

    user_progress['start_time'] = time.time()
    return jsonify({'message': f'Welcome to {BUILDINGS[building_id-1]["name"]}! Your timer has started.'})


@app.route('/api/leave_building', methods=['POST'])
def leave_building():
    """API endpoint to handle leaving a building and calculating score."""
    if not google.authorized:
        return jsonify({'error': 'User not authenticated'}), 401
        
    user_progress = get_current_user_progress()
    data = request.get_json()
    building_id = data.get('building_id')

    if not building_id or not isinstance(building_id, int):
        return jsonify({'error': 'Invalid building ID'}), 400
        
    if user_progress['current_building_id'] != building_id:
        return jsonify({'error': 'You are not at the correct building on the tour.'}), 400

    if user_progress['start_time'] is None:
        return jsonify({'error': 'You have not started a visit to this building. Click "I\'m Here!" first.'}), 400

    time_spent = time.time() - user_progress['start_time']
    score_earned = int(time_spent / 5)
    user_progress['score'] += score_earned
    user_progress['visited'].append(building_id)
    user_progress['start_time'] = None

    if user_progress['current_building_id'] < len(BUILDINGS):
        user_progress['current_building_id'] += 1
    else:
        user_progress['current_building_id'] = None

    return jsonify({
        'message': f'You spent {int(time_spent)} seconds and earned {score_earned} points!',
        'total_score': user_progress['score'],
        'next_building_id': user_progress['current_building_id']
    })

if __name__ == '__main__':
    try:
        with open('buildings.json', 'r') as f:
            pass
    except FileNotFoundError:
        buildings_data = [
            {"id": i, "name": f"Heritage Building {i}", "course": f"Course in History {i}", "lat": 34.0522 + (i*0.001), "lng": -118.2437 + (i*0.001), "info": f"This is some information about building {i}."}
            for i in range(1, 28)
        ]
        with open('buildings.json', 'w') as f:
            json.dump(buildings_data, f, indent=4)

    server = WSGIServer(('', 5000), app)
    server.serve_forever()