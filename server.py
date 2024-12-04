import json
import threading
from flask import Flask, render_template, request, flash, session, redirect, url_for
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from urllib.parse import urlparse, urlunparse

# Load secrets from JSON file
with open("secrets.json") as f:
    secrets = json.load(f)

# Flask app setup
app = Flask(__name__)
app.secret_key = secrets["FLASK_SECRET_KEY"]  # Secret key for Flask session

# Spotify setup
SPOTIFY_CLIENT_ID = secrets["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = secrets["SPOTIFY_CLIENT_SECRET"]
SPOTIFY_REDIRECT_URI = secrets["SPOTIFY_REDIRECT_URI"]
SCOPE = 'user-library-read playlist-read-private playlist-modify-private playlist-modify-public'

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope=SCOPE
))


# Global variable to store the SpotifyGame playlist
spotify_game_playlist = None

# A simple in-memory "database" for tracking songs and users
added_songs_db = {}  # Dictionary: {track_url: [user1, user2, ...]}


# Function to check if the "SpotifyGame" playlist exists, if not create it
def get_or_create_spotify_game_playlist():
    global spotify_game_playlist
    playlists = sp.current_user_playlists()
    for playlist in playlists['items']:
        if playlist['name'] == 'SpotifyGame-2024':
            spotify_game_playlist = playlist
            return playlist

    user = sp.current_user()
    spotify_game_playlist = sp.user_playlist_create(user['id'], 'SpotifyGame-2024', public=False)
    return spotify_game_playlist

# Function to check if a song is already in the "SpotifyGame" playlist
def is_song_in_playlist(track_url):
    """
    Checks if the song from the given track URL is already in the SpotifyGame playlist.
    """
    playlist_id = spotify_game_playlist['id']

    # Extract track ID from the URL (handle potential query parameters)
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
        if len(response['items']) < 100:  # No more tracks to fetch
            break
        offset += 100

    # Check if the extracted track ID is in the playlist
    return track_id in track_ids

# Function to clean the URL
def clean_url(track_url):
    """
    Cleans the track URL by removing any query parameters.
    """
    # Parse the URL
    parsed_url = urlparse(track_url)
    
    # Rebuild the URL without query parameters and fragment
    cleaned_url = urlunparse(parsed_url._replace(query='', fragment=''))
    
    return cleaned_url

# Function to add a track to the "SpotifyGame" playlist
def add_song_to_playlist(track_url, user_id):
    """
    Adds a song to the SpotifyGame playlist if not already in it.
    Also tracks users who tried to add the same song in `added_songs_db`.
    """
    if not spotify_game_playlist:
        get_or_create_spotify_game_playlist()  # Ensure the playlist exists

    # Clean the track URL (remove query parameters)
    track_url = clean_url(track_url)

    if is_song_in_playlist(track_url):  # Check if the song is already in the playlist
        # Update added_songs_db to include the user for the existing song
        if track_url in added_songs_db:
            # Append the user_id if it's not already added
            if user_id not in added_songs_db[track_url]:
                added_songs_db[track_url].append(user_id)
        else:
            # Create a new entry with the track_url and user_id
            added_songs_db[track_url] = [user_id]
        return False  # Song already in playlist
    else:
        # Add the song to the playlist
        sp.playlist_add_items(spotify_game_playlist['id'], [track_url])
        
        # Add to added_songs_db for tracking
        added_songs_db[track_url] = [user_id]
        return True  # Song successfully added



# Flask route to show the form to add a song
@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))  # Redirect to login if no user session exists
    return render_template('index.html')

# Flask route to log in (for simplicity, using a hardcoded username)
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user_id = request.form['username']
        session['user_id'] = user_id
        flash(f"Logged in as {user_id}", 'success')
        return redirect(url_for('home'))
    return render_template('login.html')

# Flask route to add a song
@app.route('/add-song', methods=['POST'])
def add_song():
    track_url = request.form['track_url']
    user_id = session.get('user_id')  # Use session.get to handle cases where user_id might not be set
    
    if not user_id:
        flash("You need to log in first!", 'danger')
        return redirect(url_for('login'))
    
    #print(f"User ID: {user_id}")  # Debugging line to check if user_id is correct
    
    if add_song_to_playlist(track_url, user_id):
        flash(f"Song added to playlist!", 'success')
    else:
        flash(f"Song is already in the playlist. User {user_id} tried adding it again.", 'info')
    return redirect(url_for('home'))


# Route to display added songs
@app.route('/added-songs')
def added_songs():
    """
    Displays all added songs and the users who added them.
    """
    formatted_songs = [
        {'url': track_url, 'users': users}
        for track_url, users in added_songs_db.items()
    ]
    return render_template('added_songs.html', added_songs=formatted_songs)

# Route to display playlist-data
@app.route('/playlist-data')
def playlist_data():
    """
    Fetches the current songs in the SpotifyGame playlist.
    Returns the data as JSON for the front-end, including the IDs for validation.
    """
    if not spotify_game_playlist:
        get_or_create_spotify_game_playlist()

    playlist_id = spotify_game_playlist['id']
    tracks = sp.playlist_tracks(playlist_id)['items']
    formatted_tracks = []

    for track in tracks:
        track_id = track['track']['id']
        track_url = track['track']['external_urls']['spotify']

        # Debugging: Print the track_url to ensure it matches the one in added_songs_db
        #print(f"Track URL: {track_url}")

        # Retrieve the user(s) who added the song from added_songs_db
        added_by_users = added_songs_db.get(track_url, [])
        #print(f"Added by users: {added_by_users}")  # Debugging line to check the users

        if not added_by_users:  # Default to a friendly "Not recorded" message if no user found
            added_by_users = ["Not recorded"]

        formatted_tracks.append({
            'id': track_id,
            'name': track['track']['name'],
            'artist': ', '.join(artist['name'] for artist in track['track']['artists']),
            'url': track_url,
            'added_by': added_by_users  # List of user IDs who added the song
        })

    return json.dumps(formatted_tracks)

# Route to display playlist
@app.route('/game')
def playlist():
    return render_template('game.html')

@app.route('/debug-added-songs')
def debug_added_songs():
    return json.dumps(added_songs_db, indent=4)



# Run Flask in a separate thread to keep it non-blocking
def run_flask():
    app.run(debug=False, host='0.0.0.0', port=5000)

# Start Flask server in a separate thread
flask_thread = threading.Thread(target=run_flask)
flask_thread.start()

# Initialize the server and check for the "SpotifyGame" playlist
get_or_create_spotify_game_playlist()

# Main function (if you have other tasks to run alongside Flask)
def spotify_logic():
    while True:
        pass  # Add any additional Spotify-related logic if necessary

# Run the main logic in the main thread
spotify_logic()