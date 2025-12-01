# app.py
import sys
import os
import time
import logging
import json
import threading
import requests
import subprocess
import psutil
import tkinter as tk
import webbrowser
from tkinter import filedialog
from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_socketio import SocketIO
from concurrent.futures import ThreadPoolExecutor

# --- EXTERNAL LIBRARIES ---
import pystray
from PIL import Image, ImageDraw
import engineio.async_drivers.threading

# --- IMPORT MODULES ---
from game_scanner import GameScanner
from game import Game

try:
    from watcher import start_watcher
except ImportError:
    start_watcher = None

# --- PATH CONFIGURATION ---
if os.name == 'nt':
    DATA_DIR = os.path.join(os.getenv('LOCALAPPDATA'), 'Game Hub')
else:
    DATA_DIR = os.path.expanduser('~/Game Hub')

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
MANUAL_GAMES_FILE = os.path.join(DATA_DIR, 'manual_games.json')
GAME_CACHE_FILE = os.path.join(DATA_DIR, 'game_cache.json')
LOG_FILE = os.path.join(DATA_DIR, 'app_error.log')

# --- CONSTANTS ---
STEAMGRIDDB_API_URL = "https://www.steamgriddb.com/api/v2"
GITHUB_REPO = "zorkosss/GameHub"
CURRENT_VERSION = "1.7"
PORT = 5000
HOST_URL = f"http://127.0.0.1:{PORT}"

def setup_logging():
    logging.basicConfig(level=logging.INFO, 
                        format='%(asctime)s - %(levelname)s - %(message)s', 
                        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])

app = Flask(__name__)
app.config['SECRET_KEY'] = 'gamehub_secret'

# --- SOCKET.IO ---
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

scanner = GameScanner()
all_games = []

# --- HELPER FUNCTIONS ---
def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to save config: {e}")

def load_games_from_cache():
    global all_games
    try:
        with open(GAME_CACHE_FILE, 'r') as f:
            all_games = [Game.from_dict(g) for g in json.load(f)]
        app.logger.info(f"Loaded {len(all_games)} games from cache.")
    except:
        all_games = []

def save_games_to_cache():
    try:
        with open(GAME_CACHE_FILE, 'w') as f:
            json.dump([g.to_dict() for g in all_games], f, indent=4)
    except Exception as e:
        logging.error(f"Failed to save cache: {e}")

def _fetch_grid_image(game: Game, api_key: str):
    # Skip if we have an image OR if we already marked it as missing
    if game.grid_image_url and game.grid_image_url != "":
        return

    headers = {'Authorization': f'Bearer {api_key}'}
    found = False
    try:
        # 1. Try Steam ID
        if game.source == "Steam":
            res = requests.get(f"{STEAMGRIDDB_API_URL}/grids/steam/{game.launch_id}", headers=headers, params={'dimensions': '600x900'})
            if res.ok:
                data = res.json().get('data')
                if data:
                    game.grid_image_url = data[0]['url']
                    found = True
        
        # 2. Try Name Search (Fallback)
        if not found:
            res = requests.get(f"{STEAMGRIDDB_API_URL}/search/autocomplete/{game.name}", headers=headers)
            if res.ok:
                data = res.json().get('data')
                if data:
                    game_id = data[0]['id']
                    grid_res = requests.get(f"{STEAMGRIDDB_API_URL}/grids/game/{game_id}", headers=headers, params={'dimensions': '600x900'})
                    if grid_res.ok:
                        grid_data = grid_res.json().get('data')
                        if grid_data:
                            game.grid_image_url = grid_data[0]['url']
                            found = True
    except Exception as e:
        logging.warning(f"Error fetching cover for {game.name}: {e}")

    # Mark as MISSING so we don't try again in the next loop
    if not found:
        game.grid_image_url = "MISSING"

# --- AUTO-FETCH BACKGROUND LOOP ---
def fetch_missing_covers(api_key):
    """Continuously fetches covers in small batches until done."""
    logging.info("--- Starting Cover Art Fetcher Loop ---")
    
    while True:
        # Filter games that are strictly empty (Ignore "MISSING" status)
        # This finds games that haven't been checked yet
        queue = [g for g in all_games if not g.grid_image_url or g.grid_image_url == ""]
        
        if not queue:
            logging.info("All covers fetched or marked missing. Stopping fetcher.")
            break
        
        # Fetch 5 at a time to be responsive
        batch = queue[:5] 
        logging.info(f"Fetching batch of {len(batch)} covers...")
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(lambda g: _fetch_grid_image(g, api_key), batch))
        
        # Save progress
        save_games_to_cache()
        
        # Update UI immediately so user sees images pop in
        # We send 'scan_complete' because the frontend logic for that simply re-fetches the list
        socketio.emit('scan_complete', {'message': 'Covers updated'})
        
        # Small pause to respect API limits
        time.sleep(1.5)

# --- BACKGROUND SCANNER LOGIC ---
def scan_library_background():
    """Performs the game scan in a background thread."""
    with app.app_context():
        global all_games
        logging.info("--- Starting Background Library Scan ---")
        
        config = load_config()
        scanned_games = scanner.find_all_games(config)
        
        # Merge data
        existing_data = { (g.name, g.source): g for g in all_games }
        updated_list = []
        
        for s_game in scanned_games:
            key = (s_game.name, s_game.source)
            if key in existing_data:
                e_game = existing_data[key]
                s_game.favorite = e_game.favorite
                s_game.hidden = e_game.hidden
                s_game.playtime_seconds = e_game.playtime_seconds
                s_game.last_played = e_game.last_played
                s_game.grid_image_url = e_game.grid_image_url
            updated_list.append(s_game)
        
        all_games = updated_list
        save_games_to_cache()
        
        # 1. Notify UI that scan is done (Shows text list instantly)
        socketio.emit('scan_complete', {'message': 'Scan complete'})
        
        # 2. Start fetching covers in the background (if key exists)
        api_key = config.get('steamgriddb_api_key')
        if api_key:
            # Check if we actually need covers
            missing_count = len([g for g in all_games if not g.grid_image_url or g.grid_image_url == ""])
            if missing_count > 0:
                logging.info(f"Found {missing_count} missing covers. Starting fetcher thread.")
                # Spawn a new detached thread for fetching so we don't block anything
                threading.Thread(target=fetch_missing_covers, args=(api_key,)).start()

# --- CLASSES ---
class AppTray:
    def __init__(self):
        self.icon = None

    def create_image(self):
        if os.path.exists('assets/app_icon.ico'):
            try:
                return Image.open('assets/app_icon.ico')
            except:
                pass
        width = 64
        height = 64
        image = Image.new('RGB', (width, height), (49, 51, 56))
        dc = ImageDraw.Draw(image)
        dc.ellipse((8, 8, 56, 56), fill=(88, 101, 242))
        return image

    def on_open(self, icon, item):
        webbrowser.open(HOST_URL)

    def on_quit(self, icon, item):
        icon.stop()
        os._exit(0)

    def run(self):
        image = self.create_image()
        menu = pystray.Menu(
            pystray.MenuItem('Open Game Hub', self.on_open, default=True),
            pystray.MenuItem('Quit', self.on_quit)
        )
        self.icon = pystray.Icon("Game Hub", image, "Game Hub", menu)
        self.icon.run()

class PlaytimeTracker(threading.Thread):
    def __init__(self, game_name, source, install_path):
        super().__init__(daemon=True)
        self.game_name = game_name
        self.source = source
        self.install_path = os.path.normpath(install_path) if install_path else None
        self.game_processes = set()
        self.start_time = 0

    def run(self):
        logging.info(f"[{self.game_name}] Starting tracker.")
        self.start_time = time.time()
        
        if not self.detect_game_process():
            return
            
        logging.info(f"[{self.game_name}] Game detected.")
        while True:
            time.sleep(5)
            try:
                self.game_processes = {p for p in self.game_processes if p.is_running()}
                if not self.game_processes:
                    break
            except:
                break 
        
        duration = int(time.time() - self.start_time)
        self.update_local_playtime(duration)

    def detect_game_process(self):
        for _ in range(12): 
            time.sleep(5)
            for proc in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    p_exe = proc.info.get('exe')
                    if p_exe and self.install_path and os.path.normpath(p_exe).startswith(self.install_path):
                        process = psutil.Process(proc.info['pid'])
                        self.game_processes.update([process] + process.children(recursive=True))
                except:
                    continue
            if self.game_processes:
                return True
        return False

    def update_local_playtime(self, duration):
        global all_games
        found = False
        for game in all_games:
            if game.name == self.game_name and game.source == self.source:
                game.playtime_seconds = (game.playtime_seconds or 0) + duration
                game.last_played = time.time()
                found = True
                break
        if found:
            save_games_to_cache()
            socketio.emit('game_updated', game.to_dict())

# --- API ROUTES ---

@app.route('/api/games')
def get_games():
    return jsonify([g.to_dict() for g in all_games])

@app.route('/api/refresh', methods=['POST'])
def refresh_games():
    # Trigger background scan
    threading.Thread(target=scan_library_background).start()
    return jsonify({"status": "success"})

@app.route('/api/update_game', methods=['POST'])
def update_game():
    data = request.json
    game_name = data.get('name')
    game_source = data.get('source')
    update_data = data.get('update_data')
    
    for game in all_games:
        if game.name == game_name and game.source == game_source:
            if 'favorite' in update_data:
                game.favorite = update_data['favorite']
            if 'hidden' in update_data:
                game.hidden = update_data['hidden']
            save_games_to_cache()
            return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 404

@app.route('/api/settings', methods=['GET', 'POST'])
def handle_settings():
    config = load_config()
    if request.method == 'POST':
        data = request.json
        config['steamgriddb_api_key'] = data.get('steamgriddb_api_key', '')
        config['scan_paths'] = data.get('scan_paths', [])
        save_config(config)
        return jsonify({"status": "success"})
    return jsonify(config)

@app.route('/api/add_game', methods=['POST'])
def add_game():
    data = request.json
    name = data.get('name')
    path = data.get('path')
    
    if not name or not path or not os.path.exists(path):
        return jsonify({"status": "error"}), 400
    try:
        manual_games = {}
        if os.path.exists(MANUAL_GAMES_FILE):
            with open(MANUAL_GAMES_FILE, 'r') as f:
                manual_games = json.load(f)
        
        manual_games[name] = path
        with open(MANUAL_GAMES_FILE, 'w') as f:
            json.dump(manual_games, f, indent=4)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/launch', methods=['POST'])
def launch_game():
    data = request.json
    command = data.get('command')
    source = data.get('source')
    name = data.get('name')
    install_path = data.get('install_path')

    if not all([command, source, name]):
        return jsonify({"status": "error", "message": "Missing data"}), 400
    try:
        if source == 'Epic Games':
            subprocess.Popen(f'cmd /c start "" "{command}"', shell=True)
        else:
            os.startfile(command)
        if install_path:
            PlaytimeTracker(name, source, install_path).start()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/browse', methods=['GET'])
def browse_files():
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        filepath = filedialog.askopenfilename(title="Select Game Executable", filetypes=[("Executables", "*.exe"), ("All files", "*.*")])
        root.destroy()
        
        if filepath:
            return jsonify({"status": "success", "path": filepath})
        else:
            return jsonify({"status": "cancelled", "path": ""})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/assets/<path:filename>')
def serve_assets(filename):
    return send_from_directory('assets', filename)

@app.route('/api/check_for_updates')
def check_for_updates():
    try:
        response = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=5, headers={'User-Agent': 'GameHub'})
        if response.ok:
            data = response.json()
            latest = data.get("tag_name", "").lstrip("v")
            if latest > CURRENT_VERSION:
                exe_url = next((a["browser_download_url"] for a in data.get("assets", []) if a["name"].endswith(".exe")), None)
                if exe_url:
                    return jsonify({
                        "update_available": True, 
                        "version": latest, 
                        "url": exe_url, 
                        "notes": data.get("body", "")
                    })
    except: pass
    return jsonify({"update_available": False})

@app.route('/api/perform_update', methods=['POST'])
def perform_update():
    data = request.json
    download_url = data.get('url')
    if not download_url:
        return jsonify({"status": "error"}), 400

    def do_update():
        try:
            dl_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            setup_path = os.path.join(dl_dir, "GameHub_Update.exe")
            
            with requests.get(download_url, stream=True, headers={'User-Agent': 'GameHub'}) as r:
                r.raise_for_status()
                total = int(r.headers.get('content-length', 0))
                dl = 0
                with open(setup_path, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        dl += len(chunk)
                        f.write(chunk)
                        if total and (int(100 * dl / total) % 10 == 0):
                            socketio.emit('update_progress', {'status': 'downloading', 'percent': int(100 * dl / total)})
            
            socketio.emit('update_ready', {'message': 'Installing...'})
            
            bat_path = os.path.join(dl_dir, "update_launcher.bat")
            with open(bat_path, "w") as f:
                f.write(f'@echo off\ntimeout /t 2 /nobreak >nul\nstart "" "{setup_path}" /SILENT /SP- /CLOSEAPPLICATIONS\ndel "%~f0"')
            
            subprocess.Popen([bat_path], shell=True)
            os._exit(0)
        except Exception as e:
            socketio.emit('update_error', {'message': str(e)})

    threading.Thread(target=do_update).start()
    return jsonify({"status": "success", "message": "Update started"})

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    setup_logging()
    load_games_from_cache()
    
    if start_watcher:
        start_watcher(socketio, load_config())
    
    # Auto-start scan if library is empty
    if len(all_games) == 0:
        logging.info("Library empty. Starting Auto-Scan...")
        threading.Thread(target=scan_library_background).start()
    
    print("--- STARTING GAME HUB ---")
    
    server_thread = threading.Thread(target=lambda: socketio.run(app, host='0.0.0.0', port=PORT, allow_unsafe_werkzeug=True, use_reloader=False))
    server_thread.daemon = True
    server_thread.start()

    time.sleep(1)
    webbrowser.open(HOST_URL)

    tray = AppTray()
    tray.run()