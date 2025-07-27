from flask import Flask, render_template, jsonify, request, redirect, url_for, session, Response
from flask_dance.contrib.google import make_google_blueprint, google
import os
import json
import time

# --- App Configuration ---
app = Flask(__name__)
# A secret key is required for session management
app.secret_key = ""

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
    client_id="",
    client_secret="",
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


@app.route('/manifest.json')
def manifest():
    """Serves the web app manifest."""
    return jsonify({
      "short_name": "Heritage Tour",
      "name": "University Heritage Tour",
      "icons": [
        {
          "src": "/icons/192.png",
          "type": "image/png",
          "sizes": "192x192",
          "purpose": "any maskable"
        },
        {
          "src": "/icons/512.png",
          "type": "image/png",
          "sizes": "512x512",
          "purpose": "any maskable"
        }
      ],
      "start_url": "/",
      "background_color": "#f4f4f9",
      "display": "standalone",
      "scope": "/",
      "theme_color": "#6200EE"
    })

@app.route('/icons/<size>.png')
def icon(size):
    """Serves placeholder icons for the PWA."""
    # In a real app, you would serve static files. This redirects to a placeholder service.
    # The service worker will cache this external resource.
    return redirect(f"https://placehold.co/{size}x{size}/6200EE/FFFFFF?text=HT")

@app.route('/sw.js')
def service_worker():
    """Serves the service worker JavaScript file."""
    js = """
    const CACHE_NAME = 'heritage-tour-cache-v1';
    // These are the files that make up the "app shell" and will be cached.
    const urlsToCache = [
      '/',
      '/manifest.json',
      '/icons/192.png',
      '/icons/512.png',
      'https://unpkg.com/leaflet@1.7.1/dist/leaflet.css',
      'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css',
      'https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap',
      'https://unpkg.com/leaflet@1.7.1/dist/leaflet.js'
    ];

    // Install event: opens a cache and adds the app shell files to it.
    self.addEventListener('install', event => {
      event.waitUntil(
        caches.open(CACHE_NAME)
          .then(cache => {
            console.log('Opened cache');
            // Create Request objects to handle potential redirects for icons
            const requests = urlsToCache.map(url => new Request(url, { redirect: 'follow' }));
            return cache.addAll(requests);
          })
      );
    });

    // Fetch event: serves assets from the cache first.
    // If the asset is not in the cache, it fetches from the network,
    // caches it, and then returns it.
    self.addEventListener('fetch', event => {
      // We only handle GET requests for caching.
      if (event.request.method !== 'GET') {
          return;
      }
      event.respondWith(
        caches.match(event.request)
          .then(response => {
            // Cache hit - return response
            if (response) {
              return response;
            }
            return fetch(event.request).then(
              networkResponse => {
                // Check if we received a valid response to cache
                if(!networkResponse || networkResponse.status !== 200) {
                  return networkResponse;
                }
                // Only cache responses from our own origin or safe cross-origin resources
                if(networkResponse.type === 'basic' || networkResponse.type === 'cors') {
                    const responseToCache = networkResponse.clone();
                    caches.open(CACHE_NAME)
                      .then(cache => {
                        cache.put(event.request, responseToCache);
                      });
                }
                return networkResponse;
              }
            );
          })
      );
    });

    // Activate event: cleans up old caches.
    self.addEventListener('activate', event => {
      const cacheWhitelist = [CACHE_NAME];
      event.waitUntil(
        caches.keys().then(cacheNames => {
          return Promise.all(
            cacheNames.map(cacheName => {
              if (cacheWhitelist.indexOf(cacheName) === -1) {
                return caches.delete(cacheName);
              }
            })
          );
        })
      );
    });
    """
    return Response(js, mimetype='application/javascript')

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

    app.run("0.0.0.0", debug=True)