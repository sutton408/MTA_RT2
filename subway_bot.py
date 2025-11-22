import os
import json
import time
import requests
import pandas as pd
from google.transit import gtfs_realtime_pb2
from openai import OpenAI
from datetime import datetime
from fastapi import FastAPI
from pydantic import BaseModel
import sys

# --- CONFIGURATION ---
# Set your OpenAI API Key here or in your environment variables
# Note: Render recommends using environment variables for this.
api_key = os.getenv("OPENAI_API_KEY") 
if not api_key:
    # If the environment variable is not set, use the hardcoded one (only for local testing)
    api_key = "TEST VALUE"

# Get MTA API Key from environment variables (CRITICAL for Render)
MTA_API_KEY = os.getenv("MTA_API_KEY")
if not MTA_API_KEY:
    print("WARNING: MTA_API_KEY environment variable not set. Real-time data will likely fail.")

client = OpenAI(api_key=api_key)

# MTA Real-Time Feed URLs
# NOTE: The MTA key must be passed in the headers when requesting these URLs.
FEED_URLS = {
    '1': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs',
    '2': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs',
    # ... (Include all your existing FEED_URLS here for a complete file)
    'A': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-ace',
    'L': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-l',
    # ... (Please ensure all original entries are present)
    '7': 'https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-7',
}


# --- FASTAPI SETUP ---
app = FastAPI()

# 1. Define the input structure
class SubwayQuery(BaseModel):
    query: str

# 2. Basic root endpoint for health checks
@app.get("/")
def read_root():
    return {"status": "MTA Subway Bot is running and ready to serve queries at /ask"}


# --- HELPER: Load Static Station Data ---
def load_stations():
    """Downloads stops.txt if not present and loads it into a DataFrame."""
    stops_file = "stops.txt"
    if not os.path.exists(stops_file):
        print("Downloading static station data...")
        # Using the official Stations.csv link which is reliable for deployment
        url = "http://web.mta.info/developers/data/nyct/subway/Stations.csv"
        try:
            df = pd.read_csv(url)
            # Renaming for consistency with standard GTFS stops.txt logic
            df = df.rename(columns={'GTFS Stop ID': 'stop_id', 'Stop Name': 'stop_name', 'Daytime Routes': 'routes'})
            df.to_csv(stops_file, index=False)
        except Exception as e:
            # If download fails, try to load from local file (which won't exist on first deploy)
            print(f"Error downloading stations: {e}. Cannot load station data.")
            return pd.DataFrame()
    
    try:
        df = pd.read_csv(stops_file)
        return df
    except Exception as e:
        print(f"Error loading {stops_file}: {e}")
        return pd.DataFrame()

STATIONS_DF = load_stations()


# --- TOOL: Get Subway Arrivals ---
def get_subway_time(line, station_name):
    """
    Fetches real-time arrival times for a specific line and station name.
    
    This function has been updated to use the MTA_API_KEY from environment variables.
    """
    line = line.upper()

    if line not in FEED_URLS:
        return f"Error: Line {line} not a recognized MTA line."

    # Use the stop_id for the station name
    station_match = STATIONS_DF[STATIONS_DF['stop_name'].str.contains(station_name, case=False, na=False)]
    
    if station_match.empty:
        return f"Error: Could not find station matching '{station_name}'."
    
    # Simple selection: pick the first matching station ID
    stop_id = station_match.iloc[0]['stop_id']
    
    feed_url = FEED_URLS[line]
    
    headers = {'x-api-key': MTA_API_KEY} # Add the key here

    try:
        response = requests.get(feed_url, headers=headers)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        arrivals = []
        now = datetime.now()

        for entity in feed.entity:
            if entity.HasField('trip_update'):
                for stop_time_update in entity.trip_update.stop_time_update:
                    if stop_time_update.stop_id.startswith(stop_id) and entity.trip_update.trip.route_id == line:
                        arrival_timestamp = stop_time_update.arrival.time
                        if arrival_timestamp:
                            arrival_dt = datetime.fromtimestamp(arrival_timestamp)
                            
                            # Calculate time until arrival in minutes
                            time_until_arrival = int((arrival_dt - now).total_seconds() / 60)
                            
                            if time_until_arrival >= 0:
                                arrivals.append(f"{time_until_arrival} minutes ({arrival_dt.strftime('%H:%M:%S')})")

        if not arrivals:
            return f"No scheduled {line} train arrivals found for {station_name} ({stop_id}) right now."
        
        return f"Upcoming {line} train arrivals at {station_name} ({stop_id}): {', '.join(arrivals)}"

    except requests.exceptions.RequestException as e:
        return f"Error fetching MTA data (Is the MTA_API_KEY correct?): {e}"
    except Exception as e:
        return f"An unexpected error occurred during data processing: {e}"


# --- TOOL DEFINITIONS FOR OPENAI ---
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_subway_time",
            "description": "Get the next estimated arrival time in minutes for a specific NYC subway line at a station.",
            "parameters": {
                "type": "object",
                "properties": {
                    "line": {
                        "type": "string",
                        "description": "The subway line (e.g., 'L', 'A', '6')."
                    },
                    "station_name": {
                        "type": "string",
                        "description": "The name of the subway station (e.g., 'Union Square', 'Times Square 42 St')."
                    }
                },
                "required": ["line", "station_name"],
            },
        },
    }
]

# --- CORE LLM FUNCTION ---
def get_llm_response(prompt, tools, tool_choice="auto"):
    """Handles the OpenAI API call with function calling."""
# NEW SYSTEM MESSAGE: Instructs the LLM to use the tool
    system_message = {
        "role": "system", 
        "content": "You are a helpful NYC Subway bot. ALWAYS use the 'get_subway_time' tool when a user asks for arrival times, station information, or train times for a specific subway line and station."
    }
    
    messages = [
        system_message,
        {"role": "user", "content": prompt}
    ]
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o", # A powerful model that handles function calling well
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )
    except Exception as e:
        return f"LLM API Error: {e}"

    response_message = response.choices[0].message
    
    # Check if the LLM decided to call a function
    if response_message.tool_calls:
        function_name = response_message.tool_calls[0].function.name
        function_args = json.loads(response_message.tool_calls[0].function.arguments)
        
        # Execute the function
        if function_name == "get_subway_time":
            tool_output = get_subway_time(
                line=function_args.get("line"), 
                station_name=function_args.get("station_name")
            )
            
            # Send the tool output back to the LLM
            messages.append(response_message)
            messages.append(
                {
                    "tool_call_id": response_message.tool_calls[0].id,
                    "role": "tool",
                    "name": function_name,
                    "content": tool_output,
                }
            )
            
            # Get final response from LLM
            final_response = client.chat.completions.create(
                model="gpt-4o", 
                messages=messages,
            )
            return final_response.choices[0].message.content
    
    # If no function call, return the direct LLM response
    return response_message.content

# --- FASTAPI ENDPOINT: Connects the Web Request to the LLM Logic ---
@app.post("/ask")
def process_subway_query(data: SubwayQuery):
    """The main endpoint to process a user's subway query."""
    user_query = data.query
    
    if not STATIONS_DF.empty:
        # Pass the user query to the core LLM function
        bot_response = get_llm_response(user_query, tools)
        return {"user_query": user_query, "bot_response": bot_response}
    else:
        # Fail gracefully if station data could not be loaded
        return {"user_query": user_query, "bot_response": "Error: Static station data could not be loaded. Cannot look up arrivals."}




