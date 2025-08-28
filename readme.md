# Spotify-GuessWho! 🎵🎮

A multiplayer Spotify guessing game web app built with Flask and Spotipy.

## Features
- **Spotify OAuth login**: Each player logs in securely with their own Spotify account.
- **Add your top tracks**: Players can add their top Spotify songs or enter tracks manually.
- **Multiplayer playlist**: All players' tracks are pooled and shuffled into a shared playlist.
- **Guess who added the song**: During the game, guess which player added the currently playing track.
- **Real-time leaderboard**: See live scores and compete for the top spot.
- **Modern UI**: Beautiful, responsive design with standout buttons and easy navigation.
- **Secure**: CSRF protection, secure session cookies, and minimal dependencies.

## How to Play
1. **Login**: Click "Login with Spotify" and authorize the app.
2. **Add Songs**: Use "Add My Top Songs" to add your top tracks, or enter songs manually.
3. **Shuffle & Add All Songs**: When all players have added tracks, shuffle and add them to the playlist.
4. **Play Game**: Click "Play Game" to start guessing who added each song.
5. **Leaderboard**: Track your score and see who’s winning in real time.

## Setup Instructions
1. **Clone the repository**
   ```sh
   git clone https://github.com/namanuel/SpotiGame.git
   cd SpotiGame
   ```
2. **Install dependencies**
   ```sh
   pip install -r requirements.txt
   ```
3. **Create and configure `secrets.json`**
   - Copy `secrets-template.json` to `secrets.json`.
   - Fill in your Spotify Client ID, Client Secret, and a Flask secret key.
   - Example:
     ```json
     {
       "SPOTIFY_CLIENT_ID": "your_client_id",
       "SPOTIFY_CLIENT_SECRET": "your_client_secret",
       "FLASK_SECRET_KEY": "your_flask_secret_key"
     }
     ```
4. **Set your redirect URI**
   - In your Spotify Developer Dashboard, add your device’s IP and port as a redirect URI (e.g. `http://10.0.100.233:5000/callback`).
   - Make sure this matches the value in your `server.py`.
5. **Run the app**
   ```sh
   python server.py
   ```
6. **Access the app**
   - Open your browser and go to `http://<your-ip>:5000`.

## Notes
- All players must be on the same network and able to access the server’s IP/port.
- Spotify only allows redirect URIs that are explicitly set in the developer dashboard.
- For best results, use a static IP for your server.

## Tech Stack
- **Backend**: Python, Flask, Spotipy
- **Frontend**: HTML, CSS (custom + Bootstrap)
- **Storage**: JSON file for leaderboard

## License
MIT

---

Enjoy playing Spotify-GuessWho! If you have issues or feature requests, open an issue or contact the maintainer.
