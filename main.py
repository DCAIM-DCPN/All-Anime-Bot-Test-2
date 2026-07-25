
import argparse
import requests
import os
import json
import time
import subprocess
import sys

# Constants
RPM_SHARE_API_KEY = "cba926e36ff510351f459f27"
RPM_SHARE_BASE_URL = "https://rpmshare.com/api/v1"
TSUKIHIME_BASE_URL = "https://api.tsukihime.org/v1"
MANUS_API_BASE_URL = "https://api.manus.ai"
ANIME_PARENT_FOLDER_ID = "b33i"
DATA_DIR = "bot_data"
BASE_DOWNLOAD_DIR = "Anime"

def create_rpmshare_folder(name, parent_id):
    headers = {"api-token": RPM_SHARE_API_KEY, "Content-Type": "application/json"}
    payload = {"name": name, "parent_id": parent_id}
    try:
        res = requests.post(f"{RPM_SHARE_BASE_URL}/video/folder", headers=headers, json=payload)
        if res.status_code == 409: return None
        res.raise_for_status()
        return res.json().get("id")
    except: return None

def upload_to_rpmshare(file_path, folder_id):
    headers = {"api-token": RPM_SHARE_API_KEY}
    try:
        # Get upload endpoint
        endpoint_res = requests.get(f"{RPM_SHARE_BASE_URL}/video/upload", headers=headers)
        endpoint_res.raise_for_status()
        upload_url = endpoint_res.json().get("url")
        
        with open(file_path, 'rb') as f:
            files = {'file': f}
            data = {'folder_id': folder_id}
            res = requests.post(upload_url, headers=headers, files=files, data=data)
            res.raise_for_status()
            return True
    except Exception as e:
        print(f"Upload failed for {file_path}: {e}")
        return False

def get_torrents(anime_name):
    results = []
    offset = 0
    while offset < 1000:
        params = {"q": anime_name, "limit": 100, "offset": offset}
        try:
            res = requests.get(f"{TSUKIHIME_BASE_URL}/search/torrents", params=params)
            data = res.json()
            results.extend(data.get("results", []))
            if len(data.get("results", [])) < 100: break
            offset += 100
        except: break
    return results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--anime", required=True)
    args = parser.parse_args()
    anime_name = args.anime

    os.makedirs(DATA_DIR, exist_ok=True)
    state_file = os.path.join(DATA_DIR, f"{anime_name.replace(' ', '_')}_state.json")
    
    # 1. Search and Setup Local Structure
    torrents = get_torrents(anime_name)
    if not torrents: return

    types = set()
    for t in torrents:
        n = t.get("name", "").lower()
        if "movie" in n: types.add("Movie")
        elif "ova" in n: types.add("OVA")
        elif "special" in n: types.add("Special")
        else: types.add("TV")

    local_root = os.path.join(BASE_DOWNLOAD_DIR, anime_name)
    subtypes = ["Hard Sub", "Soft Sub", "Dub"]
    
    # 2. RPMShare Folder Setup
    print("Setting up RPMShare folders...")
    rpm_anime_id = create_rpmshare_folder(anime_name, ANIME_PARENT_FOLDER_ID)
    folder_map = {}
    for t in types:
        t_id = create_rpmshare_folder(t, rpm_anime_id)
        if t_id:
            folder_map[t] = {"id": t_id}
            for s in subtypes:
                s_id = create_rpmshare_folder(s, t_id)
                folder_map[t][s] = s_id
                os.makedirs(os.path.join(local_root, t, s), exist_ok=True)

    # 3. Call Manus for executor.py
    if not os.path.exists("executor.py"):
        prompt = f"Generate executor.py to download these torrents: {json.dumps(torrents)}. " \
                 f"Process into local folders under {local_root} (TV/Movie/OVA/Special -> Hard Sub/Soft Sub/Dub). " \
                 f"Use -preset ultrafast for FFmpeg. Output ONLY Python code."
        
        api_key = os.environ.get("MANUS_API_KEY")
        headers = {"x-manus-api-key": api_key, "Content-Type": "application/json"}
        payload = {"message": {"content": prompt}, "structured_output_schema": {"type": "object", "properties": {"executor_code": {"type": "string"}}, "required": ["executor_code"], "additionalProperties": False}}
        
        res = requests.post(f"{MANUS_API_BASE_URL}/v2/task.create", headers=headers, json=payload).json()
        tid = res.get("task_id") or res.get("task", {}).get("task_id")
        
        while True:
            time.sleep(15)
            status = requests.get(f"{MANUS_API_BASE_URL}/v2/task.detail?task_id={tid}", headers=headers).json().get("task", {}).get("status")
            if status == "stopped": break
            
        msgs = requests.get(f"{MANUS_API_BASE_URL}/v2/task.listMessages?task_id={tid}", headers=headers).json()
        for m in msgs["messages"]:
            if m.get("type") == "structured_output_result":
                with open("executor.py", "w") as f: f.write(m["structured_output_result"]["value"]["executor_code"])
                break

    # 4. Run Executor and Upload
    print("Running processing...")
    subprocess.run([sys.executable, "executor.py"], check=True)
    
    print("Uploading to RPMShare...")
    for t in types:
        for s in subtypes:
            dir_path = os.path.join(local_root, t, s)
            if os.path.exists(dir_path):
                for f in os.listdir(dir_path):
                    f_path = os.path.join(dir_path, f)
                    if os.path.isfile(f_path):
                        if upload_to_rpmshare(f_path, folder_map[t][s]):
                            os.remove(f_path) # Cleanup after upload

if __name__ == "__main__": main()
