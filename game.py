# game.py
import os
import json
from typing import Optional, Union
# *** FIX: Import 'fields' (plural) ***
from dataclasses import dataclass, field, fields, asdict

@dataclass
class Game:
    name: str
    source: str
    launch_id: Union[str, int, None]
    install_path: Optional[str] = None
    executable_name: Optional[str] = None 
    
    # --- User Data ---
    favorite: bool = False
    hidden: bool = False
    last_played: Optional[float] = None
    playtime_seconds: int = 0

    # --- Metadata ---
    grid_image_url: Optional[str] = None

    def __post_init__(self):
        self.name = self.name.strip()

    def get_launch_command(self) -> Optional[str]:
        if self.source == 'Steam': return f"steam://run/{self.launch_id}"
        elif self.source == 'Epic Games': return f"com.epicgames.launcher://apps/{self.launch_id}?action=launch&silent=true"
        elif self.source == 'EA': return f"origin://launchgame/{self.launch_id}"
        elif self.source == 'Other Games': return self.install_path
        return None
    
    def get_hero_image_url(self) -> Optional[str]:
        if self.source == 'Steam': return f"https://steamcdn-a.akamaihd.net/steam/apps/{self.launch_id}/header.jpg"
        return None
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @staticmethod
    def from_dict(data: dict) -> 'Game':
        # *** FIX: Use 'fields()' (plural) instead of 'field()' ***
        class_fields = {f.name for f in fields(Game)}
        filtered_data = {k: v for k, v in data.items() if k in class_fields}
        return Game(**filtered_data)

    @property
    def unique_id(self) -> str:
        return f"{self.source}|{self.name}"