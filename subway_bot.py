import os
import json
import time
import requests
import pandas as pd
from google.transit import gtfs_realtime_pb2
from openai import OpenAI
from datetime import datetime

# --- CONFIGURATION ---
# Set your OpenAI API Key here or in your environment variables
api_key = os.getenv("OPENAI_API_KEY", "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")
client = OpenAI(api_key=api_key)

# MTA Real-Time Feed URLs
FEED_URLS = {
    '1': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs',
    '2': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs',
    '3': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs',
    '4': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs',
    '5': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs',
    '6': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs',
    'S': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs',
    'A': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-ace',
    'C': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-ace',
    'E': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-ace',
    'N': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-nqrw',
    'Q': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-nqrw',
    'R': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-nqrw',
    'W': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-nqrw',
    'B': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm',
    'D': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm',
    'F': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm',
    'M': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm',
    'L': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-l',
    'G': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-g',
    'J': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-jz',
    'Z': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-jz',
    '7': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-7',
}

# --- HELPER: Load Static Station Data ---
def load_stations():
    """Downloads stops.txt if not present and loads it into a DataFrame."""
    stops_file = "stops.txt"
    if not os.path.exists(stops_file):
        print("Downloading static station data...")
        # This URL is the official MTA static GTFS feed zip, extracting just stops.txt is cleaner
        # For simplicity, we use a direct link to a raw stops.csv or parse it from the zip
        # Here we will mock the process or ask user to download. 
        # For this script to run immediately, I'll use a public mirror of stops.txt
        url = "https://raw.githubusercontent.com/google/transit/master/gtfs/stops.txt" 
        # Note: Real apps should download the official MTA google_transit.zip
        # But raw github CSV is easier for a quick demo. 
        # MTA official: http://web.mta.info/developers/data/nyct/subway/google_transit.zip
        
        try:
            df = pd.read_csv("http://web.mta.info/developers/data/nyct/subway/Stations.csv")
            # Renaming for consistency with standard GTFS stops.txt logic
            df = df.rename(columns={'GTFS Stop ID': 'stop_id', 'Stop Name': 'stop_name', 'Daytime Routes': 'routes'})
            df.to_csv(stops_file, index=False)
        except Exception as e:
            print(f"Error downloading stations: {e}")
            return pd.DataFrame()
    else:
        df = pd.read_csv(stops_file)
    return df

STATIONS_DF = load_stations()

# --- TOOL: Get Subway Arrivals ---
def get_subway_time(line, station_name):
    """
    Fetches real-time arrival times for a specific line and station name.
    """
    line = line.upper()
    
    # 1. Find the URL
    feed_url = FEED_URLS.get(line)
    if not feed_url:
        return json.dumps({"error": f"Line {line} not supported or invalid."})

    # 2. Find the Stop ID (Fuzzy match)
    # We look for the station name in our static data
    match = STATIONS_DF[STATIONS_DF['stop_name'].str.contains(station_name, case=False, na=False)]
    
    if match.empty:
        return json.dumps({"error": f"Station '{station_name}' not found."})
    
    # Prioritize stops that actually serve the requested line
    match = match[match['routes'].str.contains(line, na=False)]
    if match.empty:
         # Fallback to just the name match if line filter is too strict
         match = STATIONS_DF[STATIONS_DF['stop_name'].str.contains(station_name, case=False, na=False)]

    # Take the first valid stop_id (usually the parent station)
    stop_id = str(match.iloc[0]['stop_id'])
    official_name = match.iloc[0]['stop_name']

    # 3. Fetch Real-Time Data
    try:
        response = requests.get(feed_url)
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)
    except Exception as e:
        return json.dumps({"error": f"Failed to fetch MTA data: {str(e)}"})

    arrivals = []
    current_time = time.time()

    # 4. Parse the Feed
    for entity in feed.entity:
        if entity.HasField('trip_update'):
            for update in entity.trip_update.stop_time_update:
                # Check if this update is for our stop (North 'N' or South 'S')
                if update.stop_id.startswith(stop_id):
                    arrival_time = update.arrival.time
                    if arrival_time > current_time:
                        direction = "Northbound" if update.stop_id.endswith('N') else "Southbound"
                        minutes = int((arrival_time - current_time) / 60)
                        arrivals.append(f"{direction} in {minutes} min")

    # Sort by time and take top 5
    arrivals.sort(key=lambda x: int(x.split()[2])) 
    
    if not arrivals:
        return json.dumps({"status": f"No upcoming '{line}' trains found for {official_name}."})

    return json.dumps({
        "station": official_name,
        "line": line,
        "arrivals": arrivals[:5]
    })

# --- LLM INTEGRATION ---
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_subway_time",
            "description": "Get real-time subway arrival times for a specific NYC station and line.",
            "parameters": {
                "type": "object",
                "properties": {
                    "line": {
                        "type": "string",
                        "description": "The subway line (e.g. 'L', '1', 'Q', 'A')."
                    },
                    "station_name": {
                        "type": "string",
                        "description": "The name of the station (e.g. 'Union Square', 'Times Square')."
                    }
                },
                "required": ["line", "station_name"]
            }
        }
    }
]

def chat_with_mta():
    print("🚇 NYC Subway AI Assistant (Type 'quit' to exit)")
    print("Example: 'When is the next L train at Union Square?'")
    
    messages = [{"role": "system", "content": "You are a helpful NYC transit assistant. Use the available tools to look up train times."}]

    while True:
        user_input = input("\nYou: ")
        if user_input.lower() in ['quit', 'exit']:
            break

        messages.append({"role": "user", "content": user_input})

        # 1. Ask OpenAI
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )

        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls

        # 2. If OpenAI wants to use a tool (Function Calling)
        if tool_calls:
            messages.append(response_message)  # extend conversation with assistant's reply

            for tool_call in tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                
                print(f"   (Checking real-time data for {function_args.get('line')} at {function_args.get('station_name')}...)")
                
                if function_name == "get_subway_time":
                    function_response = get_subway_time(
                        line=function_args.get("line"),
                        station_name=function_args.get("station_name")
                    )

                    # 3. Send tool result back to OpenAI
                    messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": function_response,
                    })

            # 4. Get final natural language answer
            second_response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages
            )
            print(f"AI: {second_response.choices[0].message.content}")
        else:
            print(f"AI: {response_message.content}")

if __name__ == "__main__":

    chat_with_mta()
