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

"""Seed script to populate Firestore with initial destination catalog data."""

from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-01-9c9afcf9b4bc"

SEEDED_DESTINATIONS = [
    {
        "id": "tokyo-japan",
        "city": "Tokyo",
        "country": "Japan",
        "category": "Urban & Culture",
        "estimated_daily_cost": 180,
        "best_season": "Spring / Autumn",
        "highlights": ["Shibuya Crossing", "Senso-ji Temple", "Tsukiji Outer Market", "Shinjuku Gyoen"],
        "description": "A bustling metropolis blending ultra-modern skyscrapers with historic temples and world-class cuisine.",
    },
    {
        "id": "kyoto-japan",
        "city": "Kyoto",
        "country": "Japan",
        "category": "Historical & Nature",
        "estimated_daily_cost": 150,
        "best_season": "Spring / Autumn",
        "highlights": ["Fushimi Inari Shrine", "Arashiyama Bamboo Grove", "Kinkaku-ji (Golden Pavilion)"],
        "description": "The cultural heart of Japan, famous for classical Buddhist temples, gardens, imperial palaces, and traditional wooden houses.",
    },
    {
        "id": "paris-france",
        "city": "Paris",
        "country": "France",
        "category": "Arts & Culinary",
        "estimated_daily_cost": 220,
        "best_season": "Spring / Summer",
        "highlights": ["Eiffel Tower", "Louvre Museum", "Notre-Dame Cathedral", "Montmartre"],
        "description": "The global center for art, fashion, gastronomy, and culture with iconic landmarks and romantic boulevards.",
    },
    {
        "id": "san-francisco-usa",
        "city": "San Francisco",
        "country": "USA",
        "category": "Coastal & Tech",
        "estimated_daily_cost": 250,
        "best_season": "Autumn / Summer",
        "highlights": ["Golden Gate Bridge", "Alcatraz Island", "Fisherman's Wharf", "Golden Gate Park"],
        "description": "A scenic coastal city known for its steep rolling hills, iconic bridge, historic cable cars, and vibrant foodie scene.",
    },
    {
        "id": "rome-italy",
        "city": "Rome",
        "country": "Italy",
        "category": "History & Heritage",
        "estimated_daily_cost": 170,
        "best_season": "Spring / Autumn",
        "highlights": ["Colosseum", "Vatican City", "Trevi Fountain", "Pantheon"],
        "description": "The Eternal City, overflowing with ancient ruins, Renaissance architecture, and incredible pasta and gelato.",
    },
]


def seed_database():
    print(f"Connecting to Firestore with project_id='{PROJECT_ID}'...")
    db = firestore.Client(project=PROJECT_ID)
    
    collection_ref = db.collection("destinations")
    print("Seeding destinations collection...")
    for item in SEEDED_DESTINATIONS:
        doc_id = item["id"]
        doc_data = {k: v for k, v in item.items() if k != "id"}
        collection_ref.document(doc_id).set(doc_data)
        print(f"  ✓ Added destination: {item['city']}, {item['country']} ({doc_id})")
    
    print("Seeding complete! Successfully added 5 destinations.")


if __name__ == "__main__":
    seed_database()
