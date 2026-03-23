from typing import Dict
import os
from pathlib import Path

class TS_Config:

    def __init__(self):
        self.conf_path = Path(os.getenv('TARGET_POSTGRES_CONF_PATH'))

    def read_postgresql_conf(self) -> Dict[str, str]:
        params = {}
        try:
            with open(self.conf_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        key, val = line.split('=', 1)
                        key = key.strip()
                        val = val.strip()
                        if '#' in val:
                            val = val.split('#', 1)[0].strip()
                        params[key] = val
        except FileNotFoundError:
            print(f"Configuration file {self.conf_path} not found. Using empty dict.")
        return params

    def update_postgresql_conf(self, new_params: Dict[str, str]):

        params_to_update = new_params.copy()
    
        with open(self.conf_path, 'r') as f:
            lines = f.readlines()
        
        with open(self.conf_path, 'w') as f:
            for line in lines:
                stripped = line.strip()
                if not stripped.startswith('#') and '=' in stripped:
                    key = stripped.split('=', 1)[0].strip()
                    if key in params_to_update:
                        f.write(f"{key} = {params_to_update[key]}\n")
                        del params_to_update[key]
                    else:
                        f.write(line)
                else:
                    f.write(line)
            for key, val in params_to_update.items():
                f.write(f"{key} = {val}\n")

    