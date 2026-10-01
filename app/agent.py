# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.cloud import firestore, storage
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.genai import types

from .a2ui_utils import a2ui_callback

# Load environment variables from local .env file
load_dotenv()

# Hardcoded GCP project ID, Cloud Storage bucket name, and Memory Bank ID
PROJECT_ID = "qwiklabs-gcp-01-9c9afcf9b4bc"
BUCKET_NAME = "wanderlust-concierge-media-qwiklabs-gcp-01-9c9afcf9b4bc"
MEMORY_BANK_ID = "7710617759872188416"

# Load Agent Engine resource name from deployment_metadata.json if available
_metadata_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "deployment_metadata.json")
_agent_engine_resource_name = None
if os.path.exists(_metadata_path):
    try:
        with open(_metadata_path, "r") as f:
            _meta = json.load(f)
            _agent_engine_resource_name = _meta.get("remote_agent_runtime_id")
    except Exception:
        pass


def memory_bank_service_builder():
    """Returns a VertexAiMemoryBankService instance for Vertex AI Memory Bank integration on Agent Runtime."""
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )


async def generate_memories_callback(callback_context: CallbackContext):
    """Sends session conversation history to Memory Bank at the end of each turn for durable fact/preference extraction."""
    try:
        await callback_context.add_session_to_memory()
    except Exception as e:
        print(f"Memory Bank save skipped/failed: {e}")
    return None


def _get_db():
    return firestore.Client(project=PROJECT_ID)


def generate_destination_image(prompt: str, tool_context: ToolContext = None) -> dict:
    """Generates a high-quality travel image using gemini-3.1-flash-lite-image in the global region.

    Saves the image as an ADK session artifact and uploads it directly to the public Cloud Storage bucket.

    Args:
        prompt: Detailed description of the travel scene or landmark image to generate.

    Returns:
        A dictionary containing the public Cloud Storage HTTPS URL of the generated image.
    """
    try:
        client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"]
            ),
        )

        image_bytes = None
        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    image_bytes = part.inline_data.data
                    break

        if not image_bytes:
            return {"error": "Failed to extract image bytes from gemini-3.1-flash-lite-image model response."}

        filename = f"destination_{uuid.uuid4().hex[:8]}.png"

        # (1) Save with tool_context.save_artifact so it shows up in Playground's Artifacts panel
        if tool_context is not None:
            artifact_part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")
            tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # (2) Upload image bytes directly to public Cloud Storage bucket (without writing to a local file)
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type="image/png")

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return {
            "status": "success",
            "prompt": prompt,
            "filename": filename,
            "public_image_url": public_url,
        }
    except Exception as e:
        return {"error": f"Failed to generate destination image: {str(e)}"}


def generate_destination_video(prompt: str, tool_context: ToolContext = None) -> dict:
    """Generates a short destination video clip using Google's gemini-omni-flash-preview model in global region.

    Saves the generated video as an ADK session artifact and uploads it directly to public Cloud Storage.

    Args:
        prompt: Detailed description of the travel scene, landscape, or landmark video clip to generate.

    Returns:
        A dictionary containing the public Cloud Storage HTTPS URL of the generated video.
    """
    try:
        client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
        video_bytes = None
        mime_type = "video/mp4"

        # (A) Try Interactions API for gemini-omni-flash-preview
        try:
            interaction = client.interactions.create(
                model="gemini-omni-flash-preview",
                input=f"Generate a short 3-5 second travel video clip of: {prompt}",
            )
            if hasattr(interaction, "steps") and interaction.steps:
                for step in interaction.steps:
                    if hasattr(step, "outputs") and step.outputs:
                        for output in step.outputs:
                            if hasattr(output, "data") and output.data:
                                video_bytes = output.data
                                if getattr(output, "mime_type", None):
                                    mime_type = output.mime_type
                                break
                    if video_bytes:
                        break
        except Exception as e:
            print(f"Interactions API video generation attempt note: {e}")

        # (B) Fallback to generate_content / generate_videos
        if not video_bytes:
            try:
                response = client.models.generate_content(
                    model="gemini-omni-flash-preview",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["TEXT", "VIDEO"]
                    ),
                )
                if response.candidates and response.candidates[0].content:
                    for part in response.candidates[0].content.parts:
                        if part.inline_data:
                            video_bytes = part.inline_data.data
                            if getattr(part.inline_data, "mime_type", None):
                                mime_type = part.inline_data.mime_type
                            break
            except Exception as e:
                print(f"generate_content video generation attempt note: {e}")

        if not video_bytes:
            return {"error": "Failed to extract video bytes from gemini-omni-flash-preview model response."}

        extension = "mp4"
        if "webm" in mime_type:
            extension = "webm"
        filename = f"destination_video_{uuid.uuid4().hex[:8]}.{extension}"

        # (1) Save with tool_context.save_artifact so it shows up in Playground's Artifacts panel
        if tool_context is not None:
            artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
            tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # (2) Upload video bytes directly to public Cloud Storage bucket without writing to local file
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return {
            "status": "success",
            "prompt": prompt,
            "filename": filename,
            "public_video_url": public_url,
        }
    except Exception as e:
        return {"error": f"Failed to generate destination video: {str(e)}"}


def geocode_address(address: str) -> dict:
    """Turns a street address or location name into geographic coordinates using Google Maps Geocoding API.

    Args:
        address: The address or place name to geocode (e.g. 'Tokyo Station', '1600 Amphitheatre Pkwy, Mountain View, CA').

    Returns:
        A dictionary with the formatted address and latitude/longitude location.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        return {"error": "GOOGLE_MAPS_API_KEY environment variable not set."}

    encoded_address = urllib.parse.quote(address.strip())
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={encoded_address}&key={api_key}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())

        if data.get("status") != "OK" or not data.get("results"):
            return {"error": f"Geocoding failed for address '{address}': {data.get('status')}"}

        result = data["results"][0]
        location = result.get("geometry", {}).get("location", {})
        return {
            "name": address,
            "address": result.get("formatted_address"),
            "location": {
                "latitude": location.get("lat"),
                "longitude": location.get("lng"),
            },
        }
    except Exception as e:
        return {"error": f"Failed to geocode address '{address}': {str(e)}"}


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "restaurant",
    radius_meters: float = 1000.0,
) -> list[dict]:
    """Finds nearby places of a given type around a coordinate location using Google Places API (New).

    Args:
        latitude: Latitude coordinate.
        longitude: Longitude coordinate.
        place_type: Type of place to find (e.g. 'restaurant', 'tourist_attraction', 'museum', 'cafe', 'hotel').
        radius_meters: Search radius in meters (default 1000m).

    Returns:
        A list of matching place objects containing name, formatted address, and location coordinates.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        return [{"error": "GOOGLE_MAPS_API_KEY environment variable not set."}]

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
    }
    payload = {
        "includedTypes": [place_type],
        "maxResultCount": 5,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": latitude,
                    "longitude": longitude,
                },
                "radius": float(radius_meters),
            }
        },
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())

        places = data.get("places", [])
        results = []
        for p in places:
            display_name = p.get("displayName", {}).get("text", "")
            address = p.get("formattedAddress", "")
            loc = p.get("location", {})
            results.append({
                "name": display_name,
                "address": address,
                "location": {
                    "latitude": loc.get("latitude"),
                    "longitude": loc.get("longitude"),
                },
            })
        return results
    except Exception as e:
        return [{"error": f"Failed to search nearby places: {str(e)}"}]


def get_destinations(country: str = "") -> list[dict]:
    """Retrieves travel destinations from the Firestore catalog.

    Args:
        country: Optional country name to filter destinations (e.g. 'Japan', 'France', 'USA', 'Italy').

    Returns:
        A list of matching destination dictionaries with city, country, cost, and highlights.
    """
    db = _get_db()
    docs = db.collection("destinations").stream()
    results = []
    
    country_filter = country.strip().lower() if country else ""
    for doc in docs:
        data = doc.to_dict()
        data["doc_id"] = doc.id
        if not country_filter or country_filter in data.get("country", "").lower():
            results.append(data)
            
    return results


def add_itinerary_item(destination: str, activity: str, estimated_cost: float, time_slot: str) -> dict:
    """Saves a new travel activity item into the user's Firestore itinerary.

    Args:
        destination: City or location name (e.g. 'Tokyo', 'Paris').
        activity: Description of the planned activity or site to visit.
        estimated_cost: Estimated expense in USD for this activity.
        time_slot: Morning, Afternoon, Evening, or specific time range.

    Returns:
        A status dictionary with the saved itinerary record details.
    """
    db = _get_db()
    item_data = {
        "destination": destination,
        "activity": activity,
        "estimated_cost": estimated_cost,
        "time_slot": time_slot,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    doc_ref = db.collection("itinerary_items").document()
    doc_ref.set(item_data)
    item_data["item_id"] = doc_ref.id
    return {"status": "success", "message": "Itinerary item saved to Firestore", "item": item_data}


def get_itinerary_items(destination: str = "") -> list[dict]:
    """Retrieves all saved itinerary items from the Firestore database.

    Args:
        destination: Optional destination name to filter saved itinerary items.

    Returns:
        A list of itinerary item dictionaries.
    """
    db = _get_db()
    docs = db.collection("itinerary_items").stream()
    results = []
    
    dest_filter = destination.strip().lower() if destination else ""
    for doc in docs:
        data = doc.to_dict()
        data["item_id"] = doc.id
        if not dest_filter or dest_filter in data.get("destination", "").lower():
            results.append(data)
            
    return results


def calculate_trip_budget(destination: str, duration_days: int = 3, traveler_count: int = 1) -> dict:
    """Calculates an itemized estimated trip budget based on destination rates.

    Args:
        destination: Name of the destination city (e.g. 'Tokyo', 'Paris', 'Kyoto', 'San Francisco', 'Rome').
        duration_days: Number of days for the trip (default is 3).
        traveler_count: Number of travelers (default is 1).

    Returns:
        A dictionary containing total budget and itemized cost breakdown (lodging, dining, activities).
    """
    destinations = get_destinations(country="")
    daily_rate = 180.0
    for d in destinations:
        if destination.lower() in d.get("city", "").lower() or d.get("city", "").lower() in destination.lower():
            daily_rate = float(d.get("estimated_daily_cost", 180))
            break

    total_cost = daily_rate * duration_days * traveler_count
    return {
        "destination": destination,
        "duration_days": duration_days,
        "traveler_count": traveler_count,
        "daily_rate_per_person_usd": daily_rate,
        "total_budget_usd": round(total_cost, 2),
        "breakdown": {
            "lodging_usd": round(total_cost * 0.45, 2),
            "food_dining_usd": round(total_cost * 0.35, 2),
            "sightseeing_activities_usd": round(total_cost * 0.20, 2),
        },
    }


def get_live_city_info(city_name: str) -> dict:
    """Fetches real-time geographical data and live weather forecast for a city using Open-Meteo public API.

    Args:
        city_name: Name of the city (e.g. 'Tokyo', 'Paris', 'Rome', 'Kyoto', 'San Francisco').

    Returns:
        A dictionary with live coordinates, country, timezone, population, current temperature, and wind speed.
    """
    api_key = os.getenv("OPEN_METEO_API_KEY", "")

    encoded_city = urllib.parse.quote(city_name.strip())
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={encoded_city}&count=1&language=en&format=json"
    if api_key:
        geo_url += f"&apikey={api_key}"

    try:
        req = urllib.request.Request(geo_url, headers={"User-Agent": "WanderlustAgent/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            geo_data = json.loads(response.read().decode())

        results = geo_data.get("results")
        if not results:
            return {"error": f"City '{city_name}' not found in public geographical database."}

        city_info = results[0]
        lat = city_info.get("latitude")
        lon = city_info.get("longitude")

        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        req_w = urllib.request.Request(weather_url, headers={"User-Agent": "WanderlustAgent/1.0"})
        with urllib.request.urlopen(req_w, timeout=5) as response_w:
            weather_data = json.loads(response_w.read().decode())

        current_w = weather_data.get("current_weather", {})
        temp_c = current_w.get("temperature")
        temp_f = round((temp_c * 9 / 5) + 32, 1) if temp_c is not None else None

        return {
            "city": city_info.get("name"),
            "country": city_info.get("country"),
            "latitude": lat,
            "longitude": lon,
            "timezone": city_info.get("timezone"),
            "population": city_info.get("population"),
            "current_weather": {
                "temperature_celsius": temp_c,
                "temperature_fahrenheit": temp_f,
                "windspeed_kmh": current_w.get("windspeed"),
            },
        }
    except Exception as e:
        return {"error": f"Failed to fetch live city info for '{city_name}': {str(e)}"}


def get_weather(query: str) -> str:
    """Simulates a web search for location weather.

    Args:
        query: A string containing the location to get weather information for.

    Returns:
        A string with weather information for the queried location.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        query: The name of the city to get the current time for.

    Returns:
        A string with current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    elif "tokyo" in query.lower():
        tz_identifier = "Asia/Tokyo"
    elif "paris" in query.lower():
        tz_identifier = "Europe/Paris"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


# Configure Agent Platform Code Execution Sandbox
code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=_agent_engine_resource_name,
    optimize_data_file=True,
)

# Build system prompt using A2uiSchemaManager version 0.8 and BasicCatalog
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are Wanderlust Travel Concierge, a stateful AI travel assistant. "
        "You systematically remember all user allergies, dietary restrictions (e.g. gluten-free, peanut allergy, vegan), "
        "travel plans (e.g. upcoming destinations, dates, budgets, companion preferences), and facts across sessions. "
        "Always personalize all recommendations, restaurant suggestions, and itineraries using the user's remembered allergies and travel plans. "
        "You have direct access to a Firestore database containing destination catalogs and trip itineraries, "
        "Google Maps Geocoding and Places API (New) for location search, "
        "a Python code execution sandbox for custom calculations/data analysis, "
        "and `generate_destination_image` to create travel photos/postcards using gemini-3.1-flash-lite-image, "
        "and `generate_destination_video` to create video clips of destinations using gemini-omni-flash-preview. "
        "Use `generate_destination_image` to create visual travel previews, "
        "`generate_destination_video` to generate short video clips, "
        "`geocode_address` to convert location names into coordinates, "
        "`find_nearby_places` to search for nearby restaurants/attractions/hotels, "
        "`get_destinations` to look up catalog destinations, `get_live_city_info` to fetch live city weather, "
        "`calculate_trip_budget` to compute itemized trip costs, `add_itinerary_item` to save activities, "
        "and `get_itinerary_items` to review saved plans."
    ),
    workflow_description="Analyze the user's request, invoke tools when needed to gather data or generate images/videos, and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=code_executor,
    tools=[
        PreloadMemoryTool(),
        generate_destination_image,
        generate_destination_video,
        geocode_address,
        find_nearby_places,
        get_destinations,
        get_live_city_info,
        calculate_trip_budget,
        add_itinerary_item,
        get_itinerary_items,
        get_weather,
        get_current_time,
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
