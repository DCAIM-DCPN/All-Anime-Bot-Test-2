
import argparse
import requests
import os
import json
import time
import subprocess

RPM_SHARE_API_KEY = "cba926e36ff510351f459f27"
RPM_SHARE_BASE_URL = "https://rpmshare.com/api/v1"
TSUKIHIME_BASE_URL = "https://api.tsukihime.org/v1"
MANUS_API_BASE_URL = "https://api.manus.ai"
ANIME_FOLDER_ID = "b33i"

def create_rpmshare_folder(name, parent_id):
    headers = {
        "api-token": RPM_SHARE_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {"name": name, "parent_id": parent_id}
    try:
        response = requests.post(f"{RPM_SHARE_BASE_URL}/video/folder", headers=headers, json=payload)
        response.raise_for_status()
        return response.json()["id"]
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 409:  # Conflict, folder already exists
            print(f"Warning: Folder '{name}' already exists. Attempting to retrieve existing ID.")
            # A more robust solution would be to list folders and find the ID, but for now, we'll assume it's handled.
            # This requires a GET /video/folder endpoint with filtering by name and parent_id, which isn't explicitly provided.
            # For this exercise, we'll proceed assuming the folder ID will be available or a subsequent call will succeed.
            return None # Indicate that we couldn't get the ID this way
        else:
            print(f"Error creating RPMShare folder '{name}': {e}")
            raise
    except Exception as e:
        print(f"Error creating RPMShare folder '{name}': {e}")
        raise

def get_tsukihime_torrents(anime_name):
    all_torrents = []
    offset = 0
    limit = 100
    while True:
        params = {
            "q": anime_name.strip(),
            "limit": limit,
            "offset": offset
        }
        try:
            response = requests.get(f"{TSUKIHIME_BASE_URL}/search/torrents", params=params)
            response.raise_for_status()
            data = response.json()
            if data["error"]:
                print(f"Tsukihime API error: {data['error']}")
                break
            
            torrents = data["results"]
            if not torrents:
                break
            
            all_torrents.extend(torrents)
            offset += limit
            if len(torrents) < limit or offset >= 1000: # Limit to 10 pages for now to avoid excessive calls
                break
        except requests.exceptions.RequestException as e:
            print(f"Error fetching torrents from Tsukihime API: {e}")
            break
    return all_torrents

def call_manus_api(prompt_content, structured_output_schema):
    manus_api_key = os.environ.get("MANUS_API_KEY")
    if not manus_api_key:
        raise ValueError("MANUS_API_KEY environment variable not set.")

    headers = {
        "x-manus-api-key": manus_api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "message": {
            "content": prompt_content
        },
        "structured_output_schema": structured_output_schema
    }

    try:
        create_task_response = requests.post(f"{MANUS_API_BASE_URL}/v2/task.create", headers=headers, json=payload)
        create_task_response.raise_for_status()
        task_id = create_task_response.json()["task"]["task_id"]
        print(f"Manus task created with ID: {task_id}")

        while True:
            time.sleep(15) # Poll every 15 seconds
            task_detail_response = requests.get(f"{MANUS_API_BASE_URL}/v2/task.detail?task_id={task_id}", headers=headers)
            task_detail_response.raise_for_status()
            task_status = task_detail_response.json()["task"]["status"]
            print(f"Manus task {task_id} status: {task_status}")

            if task_status in ["stopped", "failed"]:
                break
        
        # Retrieve messages to get structured output
        list_messages_response = requests.get(f"{MANUS_API_BASE_URL}/v2/task.listMessages?task_id={task_id}&order=desc", headers=headers)
        list_messages_response.raise_for_status()
        messages = list_messages_response.json()["messages"]

        for msg in messages:
            if msg.get("type") == "structured_output_result":
                return msg["structured_output_result"]
        
        raise Exception("Structured output not found in Manus task messages.")

    except requests.exceptions.RequestException as e:
        print(f"Error interacting with Manus API: {e}")
        if e.response:
            print(f"Response: {e.response.text}")
        raise
    except Exception as e:
        print(f"An unexpected error occurred with Manus API: {e}")
        raise

def main():
    parser = argparse.ArgumentParser(description="Automated anime processing and uploading system.")
    parser.add_argument("--anime", required=True, help="The name of the anime to process.")
    args = parser.parse_args()

    anime_name = args.anime
    print(f"Processing anime: {anime_name}")

    # PART 1: RPMShare Folder Structure Setup
    folder_id_map = {}
    try:
        anime_folder_id = create_rpmshare_folder(anime_name, ANIME_FOLDER_ID)
        if not anime_folder_id:
            # If create_rpmshare_folder returns None due to 409, we need to find the existing ID.
            # This part is a placeholder as RPMShare API doesn't have a direct 'get folder by name' endpoint.
            # For a real system, one would implement a folder listing and searching mechanism here.
            print(f"Could not create or find existing folder for '{anime_name}'. Exiting.")
            return
        folder_id_map["anime_folder"] = anime_folder_id

        content_types = ["TV", "Movie", "OVA", "Special"]
        sub_types = ["Hard Sub", "Soft Sub", "Dub"]

        for content_type in content_types:
            content_type_folder_id = create_rpmshare_folder(content_type, anime_folder_id)
            if content_type_folder_id:
                folder_id_map[content_type.lower()] = {}
                for sub_type in sub_types:
                    sub_type_folder_id = create_rpmshare_folder(sub_type, content_type_folder_id)
                    if sub_type_folder_id:
                        folder_id_map[content_type.lower()][sub_type.replace(" ", "_").lower()] = sub_type_folder_id

    except Exception as e:
        print(f"Failed to set up RPMShare folder structure: {e}")
        return

    print(f"RPMShare Folder IDs: {json.dumps(folder_id_map, indent=2)}")

    # PART 2: Tsukihime API Search
    print(f"Searching for torrents for '{anime_name}'...")
    torrents_data = get_tsukihime_torrents(anime_name)
    print(f"Found {len(torrents_data)} torrents.")
    
    if not torrents_data:
        print("No torrents found. Exiting.")
        return

    # PART 3: The Manus API Call
    print("Calling Manus API to generate executor.py...")
    prompt_content = f"""Here are all the magnet links and torrent data for the anime '{anime_name}':
{json.dumps(torrents_data, indent=2)}

Here are the pre-created RPMShare folder IDs:
{json.dumps(folder_id_map, indent=2)}

Please generate a Python script called executor.py that does the following:

1. Downloads torrents using aria2c with these settings:
   - --seed-time=0
   - --console-log-level=warn
   - --summary-interval=0
   - Download to a temp directory
   - Only download up to 1/3 of total available disk space at a time (chunked downloads)

2. Filtering rules:
   - Remove duplicate torrents (same btih)
   - Filter out raw (non-English-translated) torrents. If NO English-translated options exist at all, keep the raws and flag them for subtitle search
   - Remove multisub versions if English-only versions exist
   - Prioritize 1080p over 720p over lower resolutions
   - Organize by type: TV episodes, Movies, OVAs, Specials

3. For each downloaded video file:
   a. HARD SUB: Use FFmpeg to burn in subtitles with these exact settings: -preset ultrafast -crf 22 -c:v libx264 -c:a copy. Use the subtitle track found in the MKV or a matching .ass/.srt file.
   b. SOFT SUB: Keep the original MKV with subtitle tracks intact (no re-encoding needed, just copy).
   c. DUB: Extract only the English audio track using FFmpeg: -map 0:a:X -c:a copy (where X is the index of the English audio stream). Output as .mka or remux into an MP4.

4. Upload each processed file to the correct RPMShare folder using the hardcoded API key {RPM_SHARE_API_KEY}:
   - Hard Sub files go to the Hard Sub folder ID for the correct content type
   - Soft Sub files go to the Soft Sub folder ID for the correct content type
   - Dub files go to the Dub folder ID for the correct content type
   - Use the folder IDs provided above

5. Clean up temp download directory after each torrent is processed.

6. Track completed torrents in a JSON state file so the script can resume if interrupted.

Output ONLY the Python code. No explanation. No markdown. Just raw Python."""

    structured_output_schema = {
        "type": "object",
        "properties": {
            "executor_code": {"type": "string"}
        },
        "required": ["executor_code"],
        "additionalProperties": False
    }

    try:
        manus_response = call_manus_api(prompt_content, structured_output_schema)
        executor_code = manus_response["value"]["executor_code"]

        with open("executor.py", "w") as f:
            f.write(executor_code)
        print("executor.py generated successfully.")

        # PART 4: Execute executor.py
        print("Running executor.py...")
        subprocess.run(["python3", "executor.py"], check=True)
        print("executor.py finished.")

    except Exception as e:
        print(f"Error during Manus API call or executor.py execution: {e}")
        return

if __name__ == "__main__":
    main()
