
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
CODE_DIR = "Anime Code"

def create_rpmshare_folder(name, parent_id):
    """Creates a folder on RPMShare and returns its ID."""
    headers = {
        "api-token": RPM_SHARE_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {"name": name, "parent_id": parent_id}
    try:
        response = requests.post(f"{RPM_SHARE_BASE_URL}/video/folder", headers=headers, json=payload)
        if response.status_code == 409:
            print(f"Warning: Folder '{name}' already exists. Reusing existing folder.")
            return None
        response.raise_for_status()
        return response.json().get("id")
    except Exception as e:
        print(f"Error creating folder '{name}': {e}")
        return None

def get_tsukihime_torrents(anime_name):
    """Fetches all torrents for an anime from Tsukihime API with pagination."""
    all_results = []
    offset = 0
    limit = 100
    while offset < 1000:
        params = {"q": anime_name.strip(), "limit": limit, "offset": offset}
        try:
            response = requests.get(f"{TSUKIHIME_BASE_URL}/search/torrents", params=params)
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            all_results.extend(results)
            if len(results) < limit:
                break
            offset += limit
        except Exception as e:
            print(f"Error searching Tsukihime: {e}")
            break
    return all_results

def call_manus_api(prompt, schema):
    """Calls Manus API to generate executor.py using structured output."""
    api_key = os.environ.get("MANUS_API_KEY")
    if not api_key:
        print("Error: MANUS_API_KEY environment variable is missing.")
        sys.exit(1)

    headers = {"x-manus-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "message": {"content": prompt},
        "structured_output_schema": schema
    }

    try:
        res = requests.post(f"{MANUS_API_BASE_URL}/v2/task.create", headers=headers, json=payload)
        res.raise_for_status()
        task_id = res.json()["task"]["task_id"]
        
        while True:
            time.sleep(15)
            detail = requests.get(f"{MANUS_API_BASE_URL}/v2/task.detail?task_id={task_id}", headers=headers).json()
            status = detail["task"]["status"]
            if status == "stopped":
                break
            if status == "failed":
                raise Exception("Manus task failed.")

        msgs = requests.get(f"{MANUS_API_BASE_URL}/v2/task.listMessages?task_id={task_id}", headers=headers).json()
        for msg in msgs["messages"]:
            if msg.get("type") == "structured_output_result":
                return msg["structured_output_result"]["value"]["executor_code"]
    except Exception as e:
        print(f"Manus API Error: {e}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--anime", required=True)
    args = parser.parse_args()
    anime_name = args.anime

    os.makedirs(DATA_DIR, exist_ok=True)
    anime_code_dir = os.path.join(CODE_DIR, anime_name)
    os.makedirs(anime_code_dir, exist_ok=True)
    
    executor_path = os.path.join(anime_code_dir, "executor.py")
    state_file = os.path.join(DATA_DIR, f"{anime_name.replace(' ', '_')}_state.json")

    # 1. Check if executor already exists
    if os.path.exists(state_file):
        with open(state_file, 'r') as f:
            state = json.load(f)
            if state.get("executor_generated") and os.path.exists(executor_path):
                print(f"Executor already exists at {executor_path}. Skipping Manus call.")
                subprocess.run([sys.executable, executor_path], check=True)
                return

    # 2. Setup RPMShare Folders
    print(f"Setting up folders for {anime_name}...")
    anime_folder_id = create_rpmshare_folder(anime_name, ANIME_PARENT_FOLDER_ID)
    folder_map = {"anime_root": anime_folder_id}
    
    types = ["TV", "Movie", "OVA", "Special"]
    subs = ["Hard Sub", "Soft Sub", "Dub"]
    
    for t in types:
        t_id = create_rpmshare_folder(t, anime_folder_id)
        if t_id:
            folder_map[t] = {"root": t_id}
            for s in subs:
                s_id = create_rpmshare_folder(s, t_id)
                folder_map[t][s] = s_id

    # 3. Fetch Torrents
    print("Fetching torrent data...")
    torrents = get_tsukihime_torrents(anime_name)
    if not torrents:
        print("No torrents found.")
        return

    # 4. Generate executor.py via Manus
    prompt = f"""Here are all the magnet links and torrent data for the anime '{anime_name}':
{json.dumps(torrents)}

Here are the pre-created RPMShare folder IDs:
{json.dumps(folder_map)}

Please generate a Python script called executor.py that does the following:
1. Downloads torrents using aria2c with these settings: --seed-time=0 --console-log-level=warn --summary-interval=0.
2. Only download up to 1/3 of total available disk space at a time.
3. Filtering rules: Remove duplicates, filter out raws (unless only raws exist), prioritize 1080p > 720p.
4. Processing:
   a. HARD SUB: FFmpeg burn-in (-preset ultrafast -crf 22 -c:v libx264 -c:a copy).
   b. SOFT SUB: Original MKV/MP4 copy.
   c. DUB: Extract English audio track.
5. Upload to RPMShare using key {RPM_SHARE_API_KEY} to the correct folder IDs provided.
6. Name files as: {anime_name} (Type) (Episode number) (Dub/Soft Sub/Hard Sub).
7. Track completion in a JSON state file.

Output ONLY the Python code. No explanation. No markdown. Just raw Python."""

    schema = {
        "type": "object",
        "properties": {"executor_code": {"type": "string"}},
        "required": ["executor_code"],
        "additionalProperties": False
    }

    print("Requesting executor from Manus...")
    code = call_manus_api(prompt, schema)
    
    with open(executor_path, "w") as f:
        f.write(code)
    
    with open(state_file, "w") as f:
        json.dump({"executor_generated": True, "timestamp": time.time(), "path": executor_path}, f)

    # 5. Run Executor
    print(f"Running executor from {executor_path}...")
    subprocess.run([sys.executable, executor_path], check=True)

if __name__ == "__main__":
    main()
