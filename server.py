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
from datetime import datetime
import glob

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
# Global host user id (the user whose playback we'll read for the game view)
HOST_USER_ID = None
# In-memory dictionary to track which users added which songs: {track_url: [user1, user2, ...]}
added_songs_db = {}
# In-memory dictionary to store all players' top tracks: {user_id: [track_url, ...]}
all_top_tracks = {}
# Map of user_id -> cache_path for per-user Spotipy caches (set at /callback)
USER_CACHE_MAP = {}
# Map of user_id -> display_name for building selectable user lists
USER_DISPLAY_NAMES = {}
# File paths and locks for thread safety
LEADERBOARD_FILE = 'leaderboard.json'
SONG_QUEUE_FILE = 'song_queue.json'
leaderboard_lock = Lock()
song_queue_lock = Lock()
# Server-side session version. Incremented on server start to invalidate client sessions.
SERVER_SESSION_VERSION = None
# How many incorrect guesses a player may make per song before being blocked
GUESS_LIMIT = 1

def load_song_queue():
    if not os.path.exists(SONG_QUEUE_FILE):
        return {}
    with song_queue_lock:
        with open(SONG_QUEUE_FILE, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                # Ensure we always return a dictionary
                if not isinstance(data, dict):
                    return {}
                return data
            except Exception:
                return {}

def save_song_queue(data):
    # Ensure data is a dictionary
    if not isinstance(data, dict):
        data = {}
    with song_queue_lock:
        with open(SONG_QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)

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
    sp_oauth = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SCOPE,
        show_dialog=True,  # Always show the authorization dialog
        open_browser=False  # Don't auto-open browser
    )
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


def get_spotify_client_for_user(user_id):
    """Return a Spotipy client for a given user_id using that user's cache file if available.
    Returns None if no valid token is available for that user.
    """
    # If requesting the current logged-in user, reuse the existing helper
    if user_id and session.get('user_id') == user_id:
        return get_spotify_client()
    # Look up cache path recorded at /callback
    cache_path = USER_CACHE_MAP.get(user_id)
    if not cache_path:
        return None
    sp_oauth = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SCOPE,
        cache_path=cache_path
    )
    token_info = sp_oauth.get_cached_token()
    if not token_info or not token_info.get('access_token'):
        return None
    # Refresh if expired
    if sp_oauth.is_token_expired(token_info):
        try:
            token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
        except Exception:
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
        # If server session version doesn't match, clear session to force fresh login
        global SERVER_SESSION_VERSION
        if SERVER_SESSION_VERSION is None or session.get('session_version') != SERVER_SESSION_VERSION:
            session.clear()
            return redirect(url_for('login'))
        if 'token_info' not in session:
            return redirect(url_for('login'))

# Function to get or create a playlist named with today's date (YYYY-MM-DD)
def get_or_create_spotify_game_playlist(sp):
    global spotify_game_playlist
    global HOST_USER_ID
    from datetime import date
    today_str = date.today().isoformat()
    playlist_name = f"Spotify-GuessWho-{today_str}"
    playlists = sp.current_user_playlists()
    for playlist in playlists['items']:
        if playlist['name'] == playlist_name:
            spotify_game_playlist = playlist
            # set host to the playlist owner
            try:
                HOST_USER_ID = playlist.get('owner', {}).get('id')
            except Exception:
                pass
            # If playlist is not public, make it public
            if not playlist.get('public', False):
                sp.playlist_change_details(playlist['id'], public=True)
            return playlist
    # If not found, create the playlist as public
    user = sp.current_user()
    # If we need to create the playlist, treat the current user as the host
    try:
        HOST_USER_ID = user['id']
    except Exception:
        pass
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
    # Clear any existing tokens to force a new authorization
    session.clear()
    # Create a fresh Spotipy OAuth helper with a unique cache file for this login
    # This prevents token cache collisions between different users using the same server.
    state = pysecrets.token_urlsafe(16)
    cache_path = f".cache-{state}"
    session['oauth_state'] = state
    session['oauth_cache_path'] = cache_path

    sp_oauth = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SCOPE,
        show_dialog=True,  # Force the authorization dialog to appear
        open_browser=False,  # Prevent automatic browser opening
        cache_path=cache_path
    )
    auth_url = sp_oauth.get_authorize_url(state=state)
    return redirect(auth_url)

# Route: Spotify OAuth callback
# Validates state, exchanges code for token, stores user info in session
@app.route('/callback')
def callback():
    print('DEBUG: /callback route accessed')
    print('DEBUG: request.path =', request.path)
    print('DEBUG: request.args =', dict(request.args))
    state = request.args.get('state')
    # Debug: log the state in session and the one received
    print('DEBUG: session[oauth_state]=', session.get('oauth_state'))
    print('DEBUG: received state=', state)
    if not state or state != session.get('oauth_state'):
        abort(400, description=f'Invalid state parameter. Possible CSRF attack. (session: {session.get("oauth_state")}, received: {state})')

    # Use the same cache_path we created during /login so Spotipy reads/writes the
    # right cache file for this user flow and doesn't mix tokens between users.
    cache_path = session.pop('oauth_cache_path', None)
    # Debug: show which cache file we're using and whether it exists to help
    # diagnose token collisions or stale cache issues when multiple users login.
    try:
        print('DEBUG: using cache_path =', cache_path)
        print('DEBUG: cache file exists? =', os.path.exists(cache_path) if cache_path else False)
    except Exception:
        pass
    print('DEBUG: all cache files =', glob.glob('.cache-*'))
    sp_oauth = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SCOPE,
        cache_path=cache_path
    )
    code = request.args.get('code')
    # Exchange code for token and read token info from the per-session cache
    sp_oauth.get_access_token(code)
    token_info = sp_oauth.get_cached_token()
    session['token_info'] = token_info
    # Debug: show token_info (don't print the full access token)
    try:
        print('DEBUG: token_info keys =', list(token_info.keys()) if isinstance(token_info, dict) else type(token_info))
        if isinstance(token_info, dict):
            scopes = token_info.get('scope')
            print('DEBUG: token_info scope =', scopes)
            at = token_info.get('access_token')
            if at:
                print('DEBUG: access_token (truncated) =', at[:8] + '...' + at[-8:])
    except Exception:
        pass

    sp = spotipy.Spotify(auth=token_info['access_token'])
    # Call current_user() inside try/except to catch 403 and provide guidance
    try:
        user = sp.current_user()
    except Exception as e:
        # Log full exception and token_info for debugging (server logs only)
        print('ERROR: Spotify API /me call failed:', repr(e))
        try:
            print('DEBUG: token_info full =', token_info)
        except Exception:
            pass
        # Common cause: app still in Development mode and the user is not a test user
        flash('Spotify API error (403): your account may not be allowed for this app.\n'
              'If your app is in Development mode on developer.spotify.com, add this user as a test user or publish the app.\n'
              'Also verify the redirect URI in the Spotify dashboard matches the app redirect.', 'danger')
        # Clear token info to avoid reusing a broken token
        session.pop('token_info', None)
        return redirect(url_for('login'))
    session['user_id'] = user['id']
    session['display_name'] = user.get('display_name', user['id'])
    # Record the mapping of user -> cache_path and user -> display name for
    # cross-user operations (e.g. reading host playback)
    try:
        USER_CACHE_MAP[user['id']] = cache_path
    except Exception:
        pass
    try:
        USER_DISPLAY_NAMES[user['id']] = session.get('display_name')
    except Exception:
        pass
    # Mark this session as valid for the current server session version
    global SERVER_SESSION_VERSION
    session['session_version'] = SERVER_SESSION_VERSION
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
    # Ensure user has an entry in the leaderboard, but do not reset existing scores
    if display_name not in leaderboard:
        leaderboard[display_name] = 0
        save_leaderboard(leaderboard)
    return render_template('index.html', display_name=display_name)

# Route: Add a song to the playlist (requires login)
@app.route('/add-song', methods=['POST'])
@login_required
def add_song():
    track_url = request.form['track_url']
    user_id = session.get('user_id')
    display_name = session.get('display_name', user_id)
    sp = get_spotify_client()
    if not sp:
        flash("Spotify authentication error. Please log in again.", 'danger')
        return redirect(url_for('login'))
    # Always refresh playlist object to ensure it's up to date
    get_or_create_spotify_game_playlist(sp)
    if add_song_to_playlist(track_url, display_name, sp):
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
    # Debug: print current session user and Spotify API user
    print('DEBUG: session user_id =', session.get('user_id'))
    print('DEBUG: session display_name =', session.get('display_name'))
    try:
        api_user = sp.current_user()
        print('DEBUG: Spotify API user =', api_user.get('id'), api_user.get('display_name'))
        # Request the user's top 5 tracks over the long-term (all-time).
        # Spotify supports short_term, medium_term and long_term. There's no exact
        # 12-month window, so long_term is the closest to "whole year" / all-time.
        top_tracks = sp.current_user_top_tracks(limit=5, time_range='long_term').get('items', [])
    except Exception as e:
        flash("Could not fetch your top tracks from Spotify. Please enter 5 tracks manually.", 'warning')
        return redirect(url_for('manual_top_tracks'))
    if not top_tracks:
        flash("No top tracks found for your account. Please enter 5 tracks manually.", 'warning')
        return redirect(url_for('manual_top_tracks'))
    # Save tracks both to memory and persistent storage
    user_id = session.get('user_id')
    display_name = session.get('display_name', user_id)
    
    # Save to memory
    global all_top_tracks
    all_top_tracks[user_id] = [track['external_urls']['spotify'] for track in top_tracks]
    
    # Save to file
    song_queue = load_song_queue()
    song_queue[display_name] = {
        'tracks': [track['external_urls']['spotify'] for track in top_tracks],
        'added_at': str(datetime.now())
    }
    save_song_queue(song_queue)
    
    flash(f"Your top 5 (long-term) tracks have been saved. {len(song_queue)} players have submitted tracks!", 'success')
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
    # Load tracks from both memory and file
    global all_top_tracks
    song_queue = load_song_queue()
    
    if not all_top_tracks and not song_queue:
        flash("No top tracks from any player to add.", 'danger')
        return redirect(url_for('home'))
    
    # Combine tracks from memory and file
    combined_tracks = {}
    # Add tracks from memory
    for user_id, tracks in all_top_tracks.items():
        # Map stored user_id to the user's display name if known so the
        # combined tracks use readable names instead of raw IDs.
        display_name = USER_DISPLAY_NAMES.get(user_id, user_id)
        combined_tracks[display_name] = tracks
    
    # Add tracks from file
    for display_name, data in song_queue.items():
        if display_name not in combined_tracks:  # Don't overwrite memory tracks
            combined_tracks[display_name] = data['tracks']
    
    # Interleave tracks from all players
    interleaved = []
    if combined_tracks:
        max_len = max(len(tracks) for tracks in combined_tracks.values())
        for i in range(max_len):
            for user, tracks in combined_tracks.items():
                if i < len(tracks):
                    interleaved.append((tracks[i], user))
    # Shuffle the interleaved list for extra randomness
    random.shuffle(interleaved)
    added_count = 0
    for track_url, user_id in interleaved:
        if add_song_to_playlist(track_url, user_id, sp):
            added_count += 1
    flash(f"Added {added_count} tracks from {len(combined_tracks)} players to the playlist in shuffled order!", 'success')
    # Clear both memory and file storage after adding
    all_top_tracks = {}
    save_song_queue({})
    return redirect(url_for('home'))

# Route: Manual entry form for 5 tracks (always accessible)
@app.route('/manual-top-tracks', methods=['GET', 'POST'])
@login_required
def manual_top_tracks():
    sp = get_spotify_client()
    if request.method == 'POST':
        user_id = session.get('user_id')
        display_name = session.get('display_name', user_id)
        added_count = 0
        for i in range(1, 6):
            track_url = request.form.get(f'track_url_{i}', '').strip()
            if track_url and 'open.spotify.com/track/' in track_url:
                # Clean and convert to Spotify URI
                track_url = clean_url(track_url)
                if add_song_to_playlist(track_url, display_name, sp):
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
    # For the game view, prefer using the host user's playback (so all players
    # see the same currently playing song). Fall back to the current user's
    # playback if host is not available.
    host_id = HOST_USER_ID or session.get('user_id')
    sp = get_spotify_client_for_user(host_id)
    if not sp:
        # Fall back to current user
        sp = get_spotify_client()
        if not sp:
            flash('Spotify authentication error. Please log in again.', 'danger')
            return redirect(url_for('login'))
    # Get currently playing song (from host or fallback user)
    try:
        playback = sp.current_playback()
    except spotipy.SpotifyException as e:
        if 'Permissions missing' in str(e):
            # If host playback can't be read due to permissions, clear host token
            # mapping to avoid repeated errors and fall back to viewer's playback.
            if host_id != session.get('user_id'):
                USER_CACHE_MAP.pop(host_id, None)
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
    # Build a list of selectable players from multiple sources: submitted
    # song_queue, recorded added_songs_db entries, and known logged-in users.
    all_users = set()
    # From persistent song submissions
    try:
        song_queue = load_song_queue()
        all_users.update(song_queue.keys())
    except Exception:
        pass
    # From recorded add attempts
    for users in added_songs_db.values():
        try:
            all_users.update(users)
        except Exception:
            pass
    # From known user display names
    try:
        all_users.update(USER_DISPLAY_NAMES.values())
    except Exception:
        pass
    all_users = sorted([u for u in all_users if u])
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
    # Use the host's playback for guessing so viewers (who may not be playing)
    # can still guess the host's currently playing song.
    host_id = HOST_USER_ID or session.get('user_id')
    sp = get_spotify_client_for_user(host_id)
    if not sp:
        sp = get_spotify_client()
        if not sp:
            flash('Spotify authentication error. Please log in again.', 'danger')
            return redirect(url_for('login'))
    # Always refresh playlist object to ensure it's up to date
    get_or_create_spotify_game_playlist(sp)
    try:
        playback = sp.current_playback()
    except spotipy.SpotifyException as e:
        flash('Spotify API error while checking playback.', 'danger')
        return redirect(url_for('game'))
    if not playback or not playback.get('item'):
        flash('No song currently playing.', 'warning')
        return redirect(url_for('game'))
    track = playback['item']
    track_url = track['external_urls']['spotify']
    guess_user = request.form.get('guess_user')
    actual_users = added_songs_db.get(track_url, [])
    display_name = session.get('display_name', session.get('user_id', 'Unknown'))
    leaderboard = load_leaderboard()

    # Initialize per-session guess tracking structures if missing
    guessed_counts = session.get('guessed_counts', {})

    # If user already used their guess for this track, don't allow further guesses
    if guessed_counts.get(track_url, 0) >= GUESS_LIMIT:
        flash('You have already used your guess for this song.', 'info')
        return redirect(url_for('game'))

    # Process guess: one attempt only. Award point on correct, otherwise no point.
    if guess_user in actual_users:
        leaderboard[display_name] = leaderboard.get(display_name, 0) + 1
        save_leaderboard(leaderboard)
        flash(f'Correct! {guess_user} added this song.', 'success')
    else:
        flash(f'Incorrect. This song was added by: {", ".join(actual_users) if actual_users else "Unknown"}', 'danger')

    # Mark that this session used their guess for this track
    guessed_counts[track_url] = guessed_counts.get(track_url, 0) + 1
    session['guessed_counts'] = guessed_counts
    return redirect(url_for('game'))

@app.route('/leaderboard')
def get_leaderboard():
    leaderboard = load_leaderboard()
    # Return sorted leaderboard
    sorted_lb = sorted(leaderboard.items(), key=lambda x: x[1], reverse=True)
    return jsonify(sorted_lb)


@app.route('/reset-leaderboard', methods=['POST'])
@login_required
def reset_leaderboard():
    # Reset leaderboard to empty
    save_leaderboard({})
    flash('Leaderboard has been reset.', 'success')
    return redirect(url_for('home'))

# Route: Current song info (for real-time updates)
@app.route('/current-song')
@login_required
def current_song():
    # Prefer host playback for current-song so all players see the same song
    host_id = HOST_USER_ID or session.get('user_id')
    sp = get_spotify_client_for_user(host_id)
    if not sp:
        sp = get_spotify_client()
    if not sp:
        return json.dumps({'error': 'Spotify authentication error.'})
    try:
        playback = sp.current_playback()
    except spotipy.SpotifyException as e:
        if 'Permissions missing' in str(e):
            # Clear mapping for host to avoid repeated failures
            if host_id != session.get('user_id'):
                USER_CACHE_MAP.pop(host_id, None)
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
    # Determine how many guesses the current session/user has remaining for this track
    guessed_counts = session.get('guessed_counts', {})
    remaining = GUESS_LIMIT - guessed_counts.get(track_url, 0)
    if remaining < 0:
        remaining = 0
    # Build a list of all players so the frontend shows all selectable options
    players = set()
    try:
        sq = load_song_queue()
        players.update(sq.keys())
    except Exception:
        pass
    for users in added_songs_db.values():
        try:
            players.update(users)
        except Exception:
            pass
    try:
        players.update(USER_DISPLAY_NAMES.values())
    except Exception:
        pass
    players = sorted([p for p in players if p])
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
        'players': players,
        'remaining_guesses': remaining,
        'album_image': album_image
    })

# Run the Flask app
if __name__ == '__main__':
    # Clear persistent and in-memory session data on startup
    try:
        with open(SONG_QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump({}, f)
    except Exception as e:
        print('Warning: Could not clear song_queue.json:', e)
    # Reset leaderboard at server start by overwriting the file (clear scores)
    try:
        with open(LEADERBOARD_FILE, 'w', encoding='utf-8') as f:
            json.dump({}, f)
        print('INFO: leaderboard reset at startup')
    except Exception as e:
        print('Warning: Could not reset leaderboard file:', e)
    all_top_tracks = {}
    added_songs_db = {}
    spotify_game_playlist = None
    # Invalidate any existing session tokens by bumping server session version
    SERVER_SESSION_VERSION = pysecrets.token_urlsafe(16)
    print('INFO: Server session version set to', SERVER_SESSION_VERSION)
    # Remove any Spotipy cache files created previously to avoid using stale tokens
    try:
        for fname in glob.glob('.cache-*'):
            try:
                os.remove(fname)
                print('INFO: removed cache file', fname)
            except Exception as e:
                print('Warning: could not remove cache file', fname, e)
    except Exception as e:
        print('Warning: error while clearing cache files:', e)
    app.run(debug=False, host='0.0.0.0', port=5000)