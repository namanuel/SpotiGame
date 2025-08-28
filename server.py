import warnings
warnings.filterwarnings("ignore", message="This is a development server. Do not use it in a production deployment.")
import json
# Import Flask and related modules for web server and session management
from flask import Flask, render_template, request, flash, session, redirect, url_for, abort, jsonify
# Import Spotipy for Spotify API interaction
import spotipy
from spotipy.oauth2 import SpotifyOAuth
# For URL parsing and cleaning
from urllib.parse import urlparse, urlunparse
# For generating secure random state tokens
import secrets as pysecrets
import random
import os
from threading import Lock
import socket

# Load secrets (Spotify credentials and Flask secret key) from a JSON file
with open("secrets.json") as f:
    secrets = json.load(f)

# Initialize Flask app and configure session security
app = Flask(__name__)
app.secret_key = secrets["FLASK_SECRET_KEY"]  # Used to sign session cookies
app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent JavaScript access to cookies
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' # Mitigate CSRF
app.config['SESSION_COOKIE_SECURE'] = False   # Set to True if using HTTPS in production

# Spotify API credentials and required OAuth scope
SPOTIFY_CLIENT_ID = secrets["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = secrets["SPOTIFY_CLIENT_SECRET"]
# Detect local IPv4 address for dynamic Spotify redirect URI
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    LOCAL_IP = s.getsockname()[0]
    s.close()
except Exception:
    LOCAL_IP = "127.0.0.1"
SPOTIFY_REDIRECT_URI = f'http://{LOCAL_IP}:5000/callback'
SCOPE = 'user-library-read playlist-read-private playlist-modify-private playlist-modify-public user-top-read user-read-playback-state'

# Global variable to store the SpotifyGame playlist object (for the current session)
spotify_game_playlist = None
# In-memory dictionary to track which users added which songs: {track_url: [user1, user2, ...]}
added_songs_db = {}
# In-memory dictionary to store all players' top tracks: {user_id: [track_url, ...]}
all_top_tracks = {}

# Leaderboard file and lock for thread safety
LEADERBOARD_FILE = 'leaderboard.json'
leaderboard_lock = Lock()

def load_leaderboard():
    if not os.path.exists(LEADERBOARD_FILE):
        return {}
    with leaderboard_lock:
        with open(LEADERBOARD_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except Exception:
                return {}

def save_leaderboard(data):
    with leaderboard_lock:
        with open(LEADERBOARD_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)

# Helper function: Get a Spotipy client for the current user session
# Handles token refresh if needed
# Returns a Spotipy client authenticated for the current user
# Returns None if not authenticated

def get_spotify_client():
    if 'token_info' not in session:
        return None
    token_info = session['token_info']
    if not token_info or not token_info.get('access_token'):
        return None
    # Create a SpotifyOAuth object to check/refresh token
    sp_oauth = SpotifyOAuth(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET, redirect_uri=SPOTIFY_REDIRECT_URI, scope=SCOPE)
    # Refresh token if expired
    if sp_oauth.is_token_expired(token_info):
        token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
        session['token_info'] = token_info
    # Check if the token has the required scopes
    scopes_granted = set(token_info.get('scope', '').split())
    required_scopes = set(SCOPE.split())
    if not required_scopes.issubset(scopes_granted):
        session.pop('token_info', None)
        flash('Your Spotify login is missing required permissions. Please log in again.', 'danger')
        return None
    return spotipy.Spotify(auth=token_info['access_token'])

# Decorator to require Spotify login for protected routes
# Redirects to /login if user is not authenticated

def login_required(f):
    def decorated_function(*args, **kwargs):
        if 'token_info' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

# --- Force redirect to login if not logged in, for all protected routes ---
@app.before_request
def require_login_for_protected_routes():
    # Only enforce for routes that require login and are not static or login/callback/logout
    protected_paths = [
        '/', '/add-song', '/add-top-tracks', '/manual-top-tracks', '/playlist-data', '/current-song', '/guess'
    ]
    if request.path in protected_paths:
        if 'token_info' not in session:
            return redirect(url_for('login'))

# Function to get or create a playlist named with today's date (YYYY-MM-DD)
def get_or_create_spotify_game_playlist(sp):
    global spotify_game_playlist
    from datetime import date
    today_str = date.today().isoformat()
    playlist_name = f"Spotify-GuessWho-{today_str}"
    playlists = sp.current_user_playlists()
    for playlist in playlists['items']:
        if playlist['name'] == playlist_name:
            spotify_game_playlist = playlist
            # If playlist is not public, make it public
            if not playlist.get('public', False):
                sp.playlist_change_details(playlist['id'], public=True)
            return playlist
    # If not found, create the playlist as public
    user = sp.current_user()
    spotify_game_playlist = sp.user_playlist_create(user['id'], playlist_name, public=True)
    return spotify_game_playlist

# Function to check if a song is already in the "SpotifyGame" playlist
# Returns True if the song is present, False otherwise

def is_song_in_playlist(track_url, sp):
    playlist_id = spotify_game_playlist['id']
    # Extract track ID from the URL
    if 'track' in track_url:
        track_id = track_url.split('track/')[-1].split('?')[0]
    else:
        return False  # Invalid track URL
    # Retrieve all tracks from the playlist (handle pagination)
    offset = 0
    track_ids = []
    while True:
        response = sp.playlist_tracks(playlist_id, offset=offset)
        track_ids.extend([track['track']['id'] for track in response['items'] if track['track']])
        if len(response['items']) < 100:
            break
        offset += 100
    # Check if the extracted track ID is in the playlist
    return track_id in track_ids

# Function to clean a Spotify track URL (remove query parameters/fragments)
def clean_url(track_url):
    parsed_url = urlparse(track_url)
    cleaned_url = urlunparse(parsed_url._replace(query='', fragment=''))
    return cleaned_url

# Function to add a track to the "SpotifyGame" playlist
# Also tracks users who tried to add the same song in added_songs_db
# Returns True if song was added, False if already present

def add_song_to_playlist(track_url, user_id, sp):
    if not spotify_game_playlist:
        get_or_create_spotify_game_playlist(sp)
    track_url = clean_url(track_url)
    if is_song_in_playlist(track_url, sp):
        # Song already in playlist, update who tried to add it
        if track_url in added_songs_db:
            if user_id not in added_songs_db[track_url]:
                added_songs_db[track_url].append(user_id)
        else:
            added_songs_db[track_url] = [user_id]
        return False
    else:
        # Add the song to the playlist and track the user
        sp.playlist_add_items(spotify_game_playlist['id'], [track_url])
        added_songs_db[track_url] = [user_id]
        return True

# Route: Start Spotify OAuth login flow
# Generates a random state for CSRF protection
@app.route('/login')
def login():
    sp_oauth = SpotifyOAuth(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET, redirect_uri=SPOTIFY_REDIRECT_URI, scope=SCOPE)
    state = pysecrets.token_urlsafe(16)
    session['oauth_state'] = state
    auth_url = sp_oauth.get_authorize_url(state=state)
    return redirect(auth_url)

# Route: Spotify OAuth callback
# Validates state, exchanges code for token, stores user info in session
@app.route('/callback')
def callback():
    state = request.args.get('state')
    # Debug: log the state in session and the one received
    print('DEBUG: session[oauth_state]=', session.get('oauth_state'))
    print('DEBUG: received state=', state)
    if not state or state != session.get('oauth_state'):
        abort(400, description=f'Invalid state parameter. Possible CSRF attack. (session: {session.get('oauth_state')}, received: {state})')
    sp_oauth = SpotifyOAuth(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET, redirect_uri=SPOTIFY_REDIRECT_URI, scope=SCOPE)
    code = request.args.get('code')
    token_info = sp_oauth.get_access_token(code, as_dict=True)
    session['token_info'] = token_info
    sp = spotipy.Spotify(auth=token_info['access_token'])
    user = sp.current_user()
    session['user_id'] = user['id']
    session['display_name'] = user.get('display_name', user['id'])
    session.pop('oauth_state', None)
    flash(f"Logged in as {session['display_name']}", 'success')
    return redirect(url_for('home'))

# Route: Logout and clear session
@app.route('/logout')
def logout():
    session.clear()  # This will remove all session data and log the user out
    return redirect(url_for('login'))

# Route: Home page (requires login)
@app.route('/')
@login_required
def home():
    # Clear guessed tracks and leaderboard score for this user at the start
    session['guessed_tracks'] = []
    display_name = session.get('display_name', session.get('user_id', 'Unknown'))
    leaderboard = load_leaderboard()
    if display_name in leaderboard:
        leaderboard[display_name] = 0
        save_leaderboard(leaderboard)
    return render_template('index.html', display_name=display_name)

# Route: Add a song to the playlist (requires login)
@app.route('/add-song', methods=['POST'])
@login_required
def add_song():
    track_url = request.form['track_url']
    user_id = session.get('user_id')
    sp = get_spotify_client()
    if not sp:
        flash("Spotify authentication error. Please log in again.", 'danger')
        return redirect(url_for('login'))
    # Always refresh playlist object to ensure it's up to date
    get_or_create_spotify_game_playlist(sp)
    if add_song_to_playlist(track_url, user_id, sp):
        flash("Song added to playlist!", 'success')
    else:
        flash(f"Song is already in the playlist. User {user_id} tried adding it again.", 'info')
    return redirect(url_for('home'))

# Route: Add the current user's top 5 tracks to the playlist in random order
@app.route('/add-top-tracks', methods=['POST'])
@login_required
def add_top_tracks():
    sp = get_spotify_client()
    if not sp:
        flash("Spotify authentication error. Please log in again.", 'danger')
        return redirect(url_for('login'))
    get_or_create_spotify_game_playlist(sp)
    try:
        top_tracks = sp.current_user_top_tracks(limit=5, time_range='short_term').get('items', [])
    except Exception as e:
        flash("Could not fetch your top tracks from Spotify. Please enter 5 tracks manually.", 'warning')
        return redirect(url_for('manual_top_tracks'))
    if not top_tracks:
        flash("No top tracks found for your account. Please enter 5 tracks manually.", 'warning')
        return redirect(url_for('manual_top_tracks'))
    # Save as a list of track URLs in the global all_top_tracks
    user_id = session.get('user_id')
    global all_top_tracks
    all_top_tracks[user_id] = [track['external_urls']['spotify'] for track in top_tracks]
    flash("Your top 5 tracks have been saved for shuffling. When all players have submitted, an admin can shuffle and add all tracks to the playlist.", 'success')
    return redirect(url_for('home'))

# Route: Shuffle and add all players' top tracks to the playlist in interleaved order
@app.route('/shuffle-add-all', methods=['POST'])
@login_required
def shuffle_add_all():
    sp = get_spotify_client()
    if not sp:
        flash("Spotify authentication error. Please log in again.", 'danger')
        return redirect(url_for('login'))
    get_or_create_spotify_game_playlist(sp)
    global all_top_tracks
    if not all_top_tracks:
        flash("No top tracks from any player to add.", 'danger')
        return redirect(url_for('home'))
    # Interleave tracks from all players
    interleaved = []
    max_len = max(len(tracks) for tracks in all_top_tracks.values())
    for i in range(max_len):
        for user, tracks in all_top_tracks.items():
            if i < len(tracks):
                interleaved.append((tracks[i], user))
    # Shuffle the interleaved list for extra randomness
    random.shuffle(interleaved)
    added_count = 0
    for track_url, user_id in interleaved:
        if add_song_to_playlist(track_url, user_id, sp):
            added_count += 1
    flash(f"Added {added_count} tracks from all players to the playlist in shuffled order!", 'success')
    # Clear the global all_top_tracks after adding
    all_top_tracks = {}
    return redirect(url_for('home'))

# Route: Manual entry form for 5 tracks (always accessible)
@app.route('/manual-top-tracks', methods=['GET', 'POST'])
@login_required
def manual_top_tracks():
    sp = get_spotify_client()
    if request.method == 'POST':
        user_id = session.get('user_id')
        added_count = 0
        for i in range(1, 6):
            track_url = request.form.get(f'track_url_{i}', '').strip()
            if track_url and 'open.spotify.com/track/' in track_url:
                # Clean and convert to Spotify URI
                track_url = clean_url(track_url)
                if add_song_to_playlist(track_url, user_id, sp):
                    added_count += 1
        if added_count == 0:
            flash("No valid new tracks were added. Please check your links.", 'danger')
        else:
            flash("Songs were added.", 'success')
        return redirect(url_for('home'))
    return render_template('manual_top_tracks.html')

# Route: Return playlist data as JSON for the frontend (requires login)
@app.route('/playlist-data')
@login_required
def playlist_data():
    sp = get_spotify_client()
    if not sp:
        return json.dumps([])
    if not spotify_game_playlist:
        get_or_create_spotify_game_playlist(sp)
    playlist_id = spotify_game_playlist['id']
    tracks = sp.playlist_tracks(playlist_id)['items']
    formatted_tracks = []
    for track in tracks:
        track_id = track['track']['id']
        track_url = track['track']['external_urls']['spotify']
        added_by_users = added_songs_db.get(track_url, [])
        if not added_by_users:
            added_by_users = ["Not recorded"]
        formatted_tracks.append({
            'id': track_id,
            'name': track['track']['name'],
            'artist': ', '.join(artist['name'] for artist in track['track']['artists']),
            'url': track_url,
            'added_by': added_by_users
        })
    return json.dumps(formatted_tracks)

# Route: Game page (guess who added which song)
@app.route('/game')
@login_required
def game():
    sp = get_spotify_client()
    if not sp:
        flash('Spotify authentication error. Please log in again.', 'danger')
        return redirect(url_for('login'))
    # Get currently playing song
    try:
        playback = sp.current_playback()
    except spotipy.SpotifyException as e:
        if 'Permissions missing' in str(e):
            session.pop('token_info', None)
            flash('Your Spotify login is missing playback permissions. Please log in again.', 'danger')
            return redirect(url_for('login'))
        flash('Spotify API error.', 'danger')
        return redirect(url_for('home'))
    if not playback or not playback.get('item'):
        flash('No song currently playing.', 'warning')
        return render_template('game.html', song=None, users=[])
    track = playback['item']
    track_url = track['external_urls']['spotify']
    # Check if the currently playing song is in the game playlist
    get_or_create_spotify_game_playlist(sp)
    playlist_id = spotify_game_playlist['id']
    context_uri = playback.get('context', {}).get('uri')
    expected_uri = f'spotify:playlist:{playlist_id}'
    if context_uri and context_uri != expected_uri:
        flash('Warning: The currently playing song is not from the SpotifyGame playlist! Please play the correct playlist for the game to work.', 'danger')
    # Find who added this song (if known)
    all_users = set()
    for users in added_songs_db.values():
        all_users.update(users)
    all_users = sorted(list(all_users))
    # If no users, show empty dropdown
    return render_template('game.html', song={
        'name': track['name'],
        'artist': ', '.join(artist['name'] for artist in track['artists']),
        'url': track_url,
        'album_image': track['album']['images'][0]['url'] if track.get('album') and track['album'].get('images') else None
    }, users=all_users)

# Route: Accept a guess for who added the current song (form POST)
@app.route('/guess-song', methods=['POST'])
@login_required
def guess_song():
    sp = get_spotify_client()
    if not sp:
        flash('Spotify authentication error. Please log in again.', 'danger')
        return redirect(url_for('login'))
    # Always refresh playlist object to ensure it's up to date
    get_or_create_spotify_game_playlist(sp)
    playback = sp.current_playback()
    if not playback or not playback.get('item'):
        flash('No song currently playing.', 'warning')
        return redirect(url_for('game'))
    track = playback['item']
    track_url = track['external_urls']['spotify']
    guess_user = request.form.get('guess_user')
    actual_users = added_songs_db.get(track_url, [])
    display_name = session.get('display_name', session.get('user_id', 'Unknown'))
    leaderboard = load_leaderboard()
    # Only count if the user hasn't already guessed this song this session
    if 'guessed_tracks' not in session:
        session['guessed_tracks'] = []
    if track_url not in session['guessed_tracks']:
        if guess_user in actual_users:
            leaderboard[display_name] = leaderboard.get(display_name, 0) + 1
            save_leaderboard(leaderboard)
            flash(f'Correct! {guess_user} added this song.', 'success')
        else:
            flash(f'Incorrect. This song was added by: {", ".join(actual_users) if actual_users else "Unknown"}', 'danger')
        session['guessed_tracks'].append(track_url)
    else:
        flash('You have already guessed for this song.', 'info')
    return redirect(url_for('game'))

@app.route('/leaderboard')
def get_leaderboard():
    leaderboard = load_leaderboard()
    # Return sorted leaderboard
    sorted_lb = sorted(leaderboard.items(), key=lambda x: x[1], reverse=True)
    return jsonify(sorted_lb)

# Route: Current song info (for real-time updates)
@app.route('/current-song')
@login_required
def current_song():
    sp = get_spotify_client()
    if not sp:
        return json.dumps({'error': 'Spotify authentication error.'})
    try:
        playback = sp.current_playback()
    except spotipy.SpotifyException as e:
        if 'Permissions missing' in str(e):
            session.pop('token_info', None)
            flash('Your Spotify login is missing playback permissions. Please log in again.', 'danger')
            return json.dumps({'error': 'Spotify permissions missing. Please log in again.'})
        return json.dumps({'error': 'Spotify API error.'})
    if not playback or not playback.get('item'):
        return json.dumps({'error': 'No song currently playing.'})
    track = playback['item']
    track_url = track['external_urls']['spotify']
    # Find who added this song (if known)
    added_by = added_songs_db.get(track_url, [])
    # Try to get album image if available
    album_image = None
    if track.get('album') and track['album'].get('images'):
        images = track['album']['images']
        if images:
            album_image = images[0]['url']
    return json.dumps({
        'name': track['name'],
        'artist': ', '.join(artist['name'] for artist in track['artists']),
        'url': track_url,
        'added_by': added_by,
        'album_image': album_image
    })

# Run the Flask app
if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)