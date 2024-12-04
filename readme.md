# Spotify Playlist Guessing Game 🎶🎮

Welcome to the **Spotify Playlist Guessing Game**, a fun and interactive way to engage with your favorite music and discover who added which song to a playlist! 🎧✨

## How it Works 🎼

The game revolves around **Spotify playlists** that you can add to and guess who added each song. Here's how you can play:

1. **Add Songs**: You can submit your favorite songs by entering their Spotify URLs. These tracks will be added to a playlist that all players will guess from.
2. **Guess the Contributor**: Once the playlist is created, players will have to guess **who added each song** by typing in the name of the contributor. If you guess correctly, you earn points!
3. **Track Your Progress**: The app keeps track of how many points you've earned as you successfully guess who added which songs. 

---

## Features 🎉

- **Add Your Own Songs**: Simply enter a Spotify track URL, and it will be added to the playlist for everyone to guess.
- **Guess Who Added The Song**: When the game starts, you can guess who contributed each song by typing their name.
- **Points System**: For every correct guess, you earn points, which are displayed live as you play.
- **Leaderboard**: See your progress and compete with friends to become the top player in the game!

---

## How to Play 🕹️

1. **Add Songs**: Go to the "Add Song" section, paste a Spotify track URL, and click **Add Song**.
2. **Play the Game**: Once songs are added, head over to the **Game** section where you will guess who added each song to the playlist.
3. **Guess and Earn Points**: Type in the name of the person you think added the song. If you're correct, you'll get points!

---

## Tech Stack 💻

This app is built using the following technologies:
- **Frontend**: HTML, CSS (with Bootstrap for responsive design), and JavaScript (with jQuery for interactivity).
- **Backend**: Python (Flask) for the server-side logic.
- **Database**: Local storage to save game data and progress.

---

## Configuration Process 🛠️

To get started with the Spotify Playlist Guessing Game, you need to **authorize** the app to interact with Spotify. Here's how you can set up the app:

### Step 1: Create a Spotify Developer Account
To begin, you need to create an account with **Spotify Developer**:
1. Go to [Spotify for Developers](https://developer.spotify.com/).
2. Sign up or log in with your Spotify account.
3. Once logged in, create a new **Spotify App** by following the instructions provided by Spotify. You will get **Client ID** and **Client Secret** keys, which are essential for authorization.

### Step 2: Set Up Spotify API
1. In your newly created app on the Spotify Developer dashboard, configure the **Redirect URI**. This is where Spotify will send the user after authorization.
   - Example: `http://localhost:5000/callback`
2. Add this Redirect URI to your **app's Spotify settings** under the "Edit Settings" section.

### Step 3: Authorize Your App
- The game uses the **Spotify Web API** to fetch and manage playlists. To connect your app with Spotify:
  1. Go to the **authorization page**.
  2. Log in to your Spotify account.
  3. Grant the necessary permissions for the app to read your playlists and track data.

### Step 4: Integrate Your Client ID & Client Secret
- In your **backend application** (Flask server), replace the placeholders with your actual **Client ID** and **Client Secret** obtained from the Spotify Developer dashboard.
  ```python
  CLIENT_ID = 'your_client_id'
  CLIENT_SECRET = 'your_client_secret'
  REDIRECT_URI = 'http://localhost:5000/callback'
