# game_scanner.py
import os
import winreg
import json
import vdf
import logging
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor
from game import Game

class GameScanner:
    def find_all_games(self, config: Dict) -> List[Game]:
        games = []
        logging.info("--- STARTING GAME SCAN ---")
        
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_steam = executor.submit(self._find_steam_games, config)
            future_epic = executor.submit(self._find_epic_games)
            future_ea = executor.submit(self._find_ea_games)
            future_manual = executor.submit(self._load_manual_games)
            
            try: games.extend(future_steam.result())
            except: pass
            try: games.extend(future_epic.result())
            except: pass
            try: games.extend(future_ea.result())
            except: pass
            try: games.extend(future_manual.result())
            except: pass
            
        logging.info(f"--- SCAN COMPLETE. Found {len(games)} games. ---")
        return games

    # --- HELPER 1: Normalize Names ---
    def _clean_name(self, name):
        # Removes spaces and symbols to match "Need for Speed™ Unbound" with "Need for Speed Unbound"
        return name.lower().replace("™", "").replace("®", "").replace(":", "").replace(" ", "").strip()

    # --- HELPER 2: Check Physical Folder Size ---
    def _is_valid_game_folder(self, folder_path):
        """Returns True if folder exists and is > 50MB."""
        if not folder_path or not os.path.exists(folder_path):
            return False
        
        total_size = 0
        # 50 MB Threshold (Filters out empty/junk folders)
        threshold = 50 * 1024 * 1024 
        
        try:
            for root, dirs, files in os.walk(folder_path):
                if "__installer" in root.lower(): continue # Skip installer backups
                
                for f in files:
                    fp = os.path.join(root, f)
                    total_size += os.path.getsize(fp)
                    
                    if total_size > threshold:
                        return True
        except: pass
        return False

    # --- HELPER 3: Check Start Menu ---
    def _has_start_menu_shortcut(self, game_name):
        paths = [
            os.path.join(os.getenv('ProgramData'), r'Microsoft\Windows\Start Menu\Programs'),
            os.path.join(os.getenv('APPDATA'), r'Microsoft\Windows\Start Menu\Programs')
        ]
        
        target = self._clean_name(game_name)
        
        for path in paths:
            if not os.path.exists(path): continue
            for root, dirs, files in os.walk(path):
                for f in files:
                    if f.lower().endswith(".lnk"):
                        sc_name = self._clean_name(f.replace(".lnk", ""))
                        # Check for partial match (e.g. "skate" in "skate.lnk")
                        if target in sc_name or sc_name in target:
                            return True
        return False

    def _find_ea_games(self) -> List[Game]:
        games = []
        
        # 1. Build Uninstall Map { CleanName : Path }
        # This gets the paths for F1 24, NFS, etc.
        uninstall_map = {}
        uninstall_keys = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
        ]
        
        for reg_path in uninstall_keys:
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path)
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, i)
                        skey = winreg.OpenKey(key, sub)
                        try:
                            name = winreg.QueryValueEx(skey, "DisplayName")[0]
                            path = winreg.QueryValueEx(skey, "InstallLocation")[0]
                            if name and path:
                                uninstall_map[self._clean_name(name)] = path
                        except: pass
                        finally: winreg.CloseKey(skey)
                        i += 1
                    except OSError: break
                winreg.CloseKey(key)
            except: pass

        # 2. Scan EA Registry (To get the Launch IDs)
        ea_reg_paths = [
            r"SOFTWARE\WOW6432Node\Origin Games",
            r"SOFTWARE\Electronic Arts\EA Games",
            r"SOFTWARE\WOW6432Node\Electronic Arts\EA Games"
        ]

        for reg_path in ea_reg_paths:
            try:
                hkey = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path)
                index = 0
                while True:
                    try:
                        launch_id = winreg.EnumKey(hkey, index)
                        game_key = winreg.OpenKey(hkey, launch_id)
                        try:
                            name = winreg.QueryValueEx(game_key, "DisplayName")[0]
                            clean_name = self._clean_name(name)
                            
                            should_add = False
                            install_path = "Unknown"

                            # CHECK A: Is it in the Windows Uninstall List? (Ghosts are here)
                            if clean_name in uninstall_map:
                                path = uninstall_map[clean_name]
                                
                                # VERIFY THE FOLDER SIZE
                                if self._is_valid_game_folder(path):
                                    should_add = True
                                    install_path = path
                                else:
                                    # Found path, but folder is empty/missing. It's a Ghost.
                                    should_add = False 

                            # CHECK B: Not in Uninstall List (Skate is here)
                            else:
                                # Since we have no path to check, we fallback to Start Menu
                                if self._has_start_menu_shortcut(name):
                                    should_add = True
                                    install_path = "Unknown"

                            if name and should_add:
                                games.append(Game(name, 'EA', launch_id, install_path))
                                
                        except: pass
                        finally: winreg.CloseKey(game_key)
                        index += 1
                    except OSError: break 
                winreg.CloseKey(hkey)
            except: pass
            
        return games

    def _find_steam_games(self, config: Dict) -> List[Game]:
        games = []
        try:
            hkey = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")
            steam_path = winreg.QueryValueEx(hkey, "InstallPath")[0]
            winreg.CloseKey(hkey)
            paths = {os.path.join(steam_path, "steamapps")}
            lib_vdf = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
            if os.path.exists(lib_vdf):
                with open(lib_vdf, 'r', encoding='utf-8') as f:
                    data = vdf.load(f)
                    for k, v in data.get('libraryfolders', {}).items():
                        if isinstance(v, dict) and 'path' in v: paths.add(os.path.join(v['path'], 'steamapps'))
            for path in paths:
                if not os.path.exists(path): continue
                for f in os.listdir(path):
                    if f.startswith("appmanifest_") and f.endswith(".acf"):
                        try:
                            with open(os.path.join(path, f), 'r', encoding='utf-8') as file:
                                m = vdf.load(file).get('AppState', {})
                                if m.get('name'): games.append(Game(m.get('name'), 'Steam', m.get('appid'), os.path.join(path, 'common', m.get('installdir', ''))))
                        except: pass
        except: pass
        for path in config.get('scan_paths', []):
             if os.path.isdir(path):
                if os.path.basename(path) == 'steamapps': 
                    try:
                        for f in os.listdir(path):
                            if f.startswith("appmanifest_") and f.endswith(".acf"):
                                with open(os.path.join(path, f), 'r', encoding='utf-8') as file:
                                    m = vdf.load(file).get('AppState', {})
                                    if m.get('name'): games.append(Game(m.get('name'), 'Steam', m.get('appid'), os.path.join(path, 'common', m.get('installdir', ''))))
                    except: pass
        return games
    
    def _find_epic_games(self) -> List[Game]:
        games = []
        try:
            path = os.path.join(os.environ.get('ProgramData', r'C:\ProgramData'), 'Epic', 'EpicGamesLauncher', 'Data', 'Manifests')
            if os.path.isdir(path):
                for f in os.listdir(path):
                    if f.endswith(".item"):
                        try:
                            with open(os.path.join(path, f), 'r', encoding='utf-8') as file:
                                d = json.load(file)
                                games.append(Game(d.get('DisplayName'), 'Epic Games', d.get('AppName'), d.get('InstallLocation')))
                        except: pass
        except: pass
        return games
    
    def _load_manual_games(self) -> List[Game]:
        games = []
        try:
            path = os.path.join(os.getenv('LOCALAPPDATA'), 'Game Hub', 'manual_games.json')
            if os.path.exists(path):
                with open(path, 'r') as f:
                    for n, p in json.load(f).items():
                        if os.path.exists(p): games.append(Game(n, 'Other Games', None, p))
        except: pass
        return games