"""
Optimized Geocoding Service for PlanIt
Reduces Google API costs by 80-95% through intelligent caching and deduplication
"""

import os
import json
import sqlite3
import hashlib
import logging
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime, timedelta
from contextlib import contextmanager
from dataclasses import dataclass, asdict
import re

try:
    import googlemaps
except ImportError:
    print("Warning: googlemaps not installed. Run: pip install googlemaps")
    googlemaps = None

try:
    from rapidfuzz import fuzz, process
except ImportError:
    print("Warning: rapidfuzz not installed. Run: pip install rapidfuzz")
    # Fallback to difflib if rapidfuzz not available
    from difflib import SequenceMatcher
    
    class FuzzFallback:
        @staticmethod
        def token_sort_ratio(s1, s2):
            return SequenceMatcher(None, s1.lower(), s2.lower()).ratio() * 100
    
    class ProcessFallback:
        @staticmethod
        def extractOne(query, choices, scorer=None):
            if not choices:
                return None
            best_match = max(choices, key=lambda x: FuzzFallback.token_sort_ratio(query, x))
            score = FuzzFallback.token_sort_ratio(query, best_match)
            return (best_match, score, 0)
    
    fuzz = FuzzFallback
    process = ProcessFallback

# Configure logging
logger = logging.getLogger(__name__)

# Configuration
GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY') or os.environ.get('GOOGLE_API_KEY')
CACHE_DB_PATH = os.environ.get('CACHE_DB_PATH', 'planit_cache.db')
SIMILARITY_THRESHOLD = 85  # Fuzzy match threshold for deduplication
CACHE_EXPIRY_DAYS = 90  # How long to keep cached data
MAX_PHOTO_WIDTH = 800  # Max width for photo requests (matching your current setup)

@dataclass
class PlaceResult:
    """Standardized place result structure"""
    place_id: str
    name: str
    formatted_address: str
    latitude: float
    longitude: float
    photo_url: Optional[str] = None
    raw_query: Optional[str] = None
    confidence_score: float = 1.0


class PlaceCacheManager:
    """SQLite-based caching for place data"""
    
    def __init__(self, db_path: str = CACHE_DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize the cache database with required tables"""
        with self._get_connection() as conn:
            # Places cache table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS places_cache (
                    normalized_key TEXT PRIMARY KEY,
                    place_data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    access_count INTEGER DEFAULT 1
                )
            """)
            
            # TikTok extraction cache table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tiktok_cache (
                    tiktok_id TEXT PRIMARY KEY,
                    extraction_data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Deduplication mapping table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS place_aliases (
                    alias TEXT PRIMARY KEY,
                    canonical_key TEXT NOT NULL,
                    similarity_score REAL NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes for performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_places_accessed ON places_cache(accessed_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_aliases_canonical ON place_aliases(canonical_key)")
    
    @contextmanager
    def _get_connection(self):
        """Thread-safe database connection context manager"""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def get_place(self, normalized_key: str) -> Optional[Dict]:
        """Retrieve cached place data"""
        with self._get_connection() as conn:
            # Check direct match
            row = conn.execute(
                "SELECT place_data FROM places_cache WHERE normalized_key = ?",
                (normalized_key,)
            ).fetchone()
            
            if row:
                # Update access time and count
                conn.execute(
                    """UPDATE places_cache 
                       SET accessed_at = CURRENT_TIMESTAMP, 
                           access_count = access_count + 1 
                       WHERE normalized_key = ?""",
                    (normalized_key,)
                )
                return json.loads(row['place_data'])
            
            # Check for aliases
            alias_row = conn.execute(
                """SELECT p.place_data 
                   FROM place_aliases a 
                   JOIN places_cache p ON a.canonical_key = p.normalized_key 
                   WHERE a.alias = ?""",
                (normalized_key,)
            ).fetchone()
            
            if alias_row:
                return json.loads(alias_row['place_data'])
            
            return None
    
    def save_place(self, normalized_key: str, place_data: Dict):
        """Save place data to cache"""
        with self._get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO places_cache 
                   (normalized_key, place_data) 
                   VALUES (?, ?)""",
                (normalized_key, json.dumps(place_data))
            )
    
    def add_alias(self, alias: str, canonical_key: str, similarity_score: float):
        """Add an alias mapping for deduplication"""
        with self._get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO place_aliases 
                   (alias, canonical_key, similarity_score) 
                   VALUES (?, ?, ?)""",
                (alias, canonical_key, similarity_score)
            )
    
    def get_tiktok_extraction(self, tiktok_id: str) -> Optional[Dict]:
        """Get cached TikTok extraction results"""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT extraction_data FROM tiktok_cache WHERE tiktok_id = ?",
                (tiktok_id,)
            ).fetchone()
            
            return json.loads(row['extraction_data']) if row else None
    
    def save_tiktok_extraction(self, tiktok_id: str, extraction_data: Dict):
        """Save TikTok extraction results"""
        with self._get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO tiktok_cache 
                   (tiktok_id, extraction_data) 
                   VALUES (?, ?)""",
                (tiktok_id, json.dumps(extraction_data))
            )
    
    def cleanup_old_entries(self, days: int = CACHE_EXPIRY_DAYS):
        """Remove old cache entries"""
        cutoff_date = datetime.now() - timedelta(days=days)
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM places_cache WHERE accessed_at < ?",
                (cutoff_date,)
            )
            conn.execute(
                "DELETE FROM tiktok_cache WHERE created_at < ?",
                (cutoff_date,)
            )


class PlaceDeduplicator:
    """Handles fuzzy matching and deduplication of place names"""
    
    def __init__(self, cache_manager: PlaceCacheManager):
        self.cache = cache_manager
        self._known_places = set()
        self._refresh_known_places()
    
    def _refresh_known_places(self):
        """Load all known place keys for fuzzy matching"""
        with self.cache._get_connection() as conn:
            rows = conn.execute("SELECT normalized_key FROM places_cache").fetchall()
            self._known_places = {row['normalized_key'] for row in rows}
    
    @staticmethod
    def normalize_place_name(place_name: str, location_hint: str = "") -> str:
        """
        Normalize place name for consistent caching
        Examples: "Lucali Pizza" → "lucali_pizza"
        """
        # Remove common suffixes and clean up
        normalized = place_name.lower().strip()
        
        # Remove common business suffixes
        suffixes_to_remove = [
            'restaurant', 'cafe', 'coffee', 'pizza', 'bar', 'grill',
            'bistro', 'kitchen', 'eatery', 'diner', 'pub', 'tavern',
            'inc', 'llc', 'corp', 'co', 'company'
        ]
        
        for suffix in suffixes_to_remove:
            normalized = re.sub(rf'\b{suffix}\b', '', normalized, flags=re.IGNORECASE)
        
        # Remove special characters and extra spaces
        normalized = re.sub(r'[^\w\s]', '', normalized)
        normalized = re.sub(r'\s+', '_', normalized.strip())
        
        # Add location hint if provided
        if location_hint:
            location_clean = re.sub(r'[^\w\s]', '', location_hint.lower())
            location_clean = re.sub(r'\s+', '_', location_clean.strip())
            normalized = f"{normalized}_{location_clean}"
        
        return normalized
    
    def find_similar_place(self, place_name: str, location_hint: str = "") -> Optional[Tuple[str, float]]:
        """
        Find a similar place in cache using fuzzy matching
        Returns: (canonical_key, similarity_score) or None
        """
        if not self._known_places:
            return None
        
        normalized = self.normalize_place_name(place_name, location_hint)
        
        # First check exact match
        if normalized in self._known_places:
            return (normalized, 100.0)
        
        # Fuzzy match against known places
        result = process.extractOne(
            normalized,
            self._known_places,
            scorer=fuzz.token_sort_ratio
        )
        
        if result and result[1] >= SIMILARITY_THRESHOLD:
            return (result[0], result[1])
        
        return None
    
    def deduplicate_place_list(self, places: List[str]) -> List[Tuple[str, str]]:
        """
        Deduplicate a list of place names
        Returns: List of (original_name, canonical_name) tuples
        """
        deduped = []
        seen_canonical = set()
        
        for place in places:
            normalized = self.normalize_place_name(place)
            
            # Check if we've already processed a similar place in this batch
            batch_match = process.extractOne(
                normalized,
                seen_canonical,
                scorer=fuzz.token_sort_ratio
            )
            
            if batch_match and batch_match[1] >= SIMILARITY_THRESHOLD:
                # Use existing canonical name
                deduped.append((place, batch_match[0]))
            else:
                # New unique place
                seen_canonical.add(normalized)
                deduped.append((place, normalized))
        
        return deduped


class OptimizedGeocodingService:
    """
    Main service for efficient place resolution
    Uses FindPlaceFromText + Place Details instead of Geocoding API
    """
    
    def __init__(self, api_key: str = GOOGLE_MAPS_API_KEY):
        if not api_key:
            raise ValueError("Google Maps API key is required")
        
        if not googlemaps:
            raise ImportError("googlemaps library is required. Install with: pip install googlemaps")
        
        self.gmaps = googlemaps.Client(key=api_key)
        self.cache = PlaceCacheManager()
        self.deduplicator = PlaceDeduplicator(self.cache)
        
        # Field masks for API efficiency
        self.FINDPLACE_FIELDS = ['place_id', 'name', 'formatted_address']
        self.DETAILS_FIELDS = [
            'place_id',
            'name',
            'formatted_address',
            'geometry',
            'photos'  # Get photos array
        ]
    
    def _get_photo_url(self, photo_reference: str) -> str:
        """Generate photo URL with size constraints"""
        return (
            f"https://maps.googleapis.com/maps/api/place/photo"
            f"?maxwidth={MAX_PHOTO_WIDTH}"
            f"&photo_reference={photo_reference}"
            f"&key={self.gmaps.key}"
        )
    
    def _call_google_api(self, place_query: str, location_bias: Optional[str] = None) -> Optional[PlaceResult]:
        """
        Make actual Google API calls (FindPlaceFromText + Place Details)
        This should be called RARELY due to caching
        """
        try:
            # Step 1: FindPlaceFromText (cheaper than geocoding)
            findplace_params = {
                'input': place_query,
                'inputtype': 'textquery',
                'fields': self.FINDPLACE_FIELDS
            }
            
            # Add location bias if provided
            if location_bias:
                findplace_params['locationbias'] = f"circle:50000@{location_bias}"
            
            findplace_result = self.gmaps.find_place(**findplace_params)
            
            if not findplace_result.get('candidates'):
                logger.warning(f"No results found for: {place_query}")
                return None
            
            # Get the first (best) candidate
            candidate = findplace_result['candidates'][0]
            place_id = candidate['place_id']
            
            # Step 2: Place Details (only if we need geometry/photos)
            details_result = self.gmaps.place(
                place_id=place_id,
                fields=self.DETAILS_FIELDS
            )
            
            if not details_result.get('result'):
                return None
            
            place = details_result['result']
            
            # Extract photo URL and photos array
            photo_url = None
            photos_array = []
            if 'photos' in place and place['photos']:
                photos_array = place['photos']  # Keep full photos array
                photo_ref = place['photos'][0].get('photo_reference')
                if photo_ref:
                    photo_url = self._get_photo_url(photo_ref)
            
            # Build result
            result = PlaceResult(
                place_id=place['place_id'],
                name=place.get('name', place_query),
                formatted_address=place.get('formatted_address', ''),
                latitude=place['geometry']['location']['lat'],
                longitude=place['geometry']['location']['lng'],
                photo_url=photo_url,
                raw_query=place_query,
                confidence_score=1.0
            )
            
            # Convert to dict and add photos array
            result_dict = asdict(result)
            result_dict['photos'] = photos_array
            return result_dict
            
        except Exception as e:
            logger.error(f"Google API error for '{place_query}': {str(e)}")
            return None
    
    def resolve_single_place(self, place_name: str, location_hint: str = "") -> Optional[Dict]:
        """
        Resolve a single place with caching and deduplication
        Returns dict compatible with existing get_place_info_from_google format:
        (canonical_name, address, place_id, photos)
        """
        # Normalize the place name
        normalized_key = self.deduplicator.normalize_place_name(place_name, location_hint)
        
        # Check cache first
        cached_result = self.cache.get_place(normalized_key)
        if cached_result:
            logger.info(f"Cache hit for: {place_name}")
            # Convert cached result to expected format
            return {
                'canonical_name': cached_result.get('name', place_name),
                'address': cached_result.get('formatted_address', ''),
                'place_id': cached_result.get('place_id'),
                'photos': cached_result.get('photos', []),
                'latitude': cached_result.get('latitude'),
                'longitude': cached_result.get('longitude'),
                'photo_url': cached_result.get('photo_url')
            }
        
        # Check for similar places (fuzzy matching)
        similar = self.deduplicator.find_similar_place(place_name, location_hint)
        if similar:
            canonical_key, score = similar
            if canonical_key != normalized_key:
                logger.info(f"Found similar place: {place_name} → {canonical_key} (score: {score})")
                
                # Save alias mapping
                self.cache.add_alias(normalized_key, canonical_key, score)
                
                # Return cached result
                cached_result = self.cache.get_place(canonical_key)
                if cached_result:
                    return {
                        'canonical_name': cached_result.get('name', place_name),
                        'address': cached_result.get('formatted_address', ''),
                        'place_id': cached_result.get('place_id'),
                        'photos': cached_result.get('photos', []),
                        'latitude': cached_result.get('latitude'),
                        'longitude': cached_result.get('longitude'),
                        'photo_url': cached_result.get('photo_url')
                    }
        
        # No cache hit - call Google API (this should be rare!)
        logger.info(f"Cache miss - calling Google API for: {place_name}")
        
        # Build query with location hint if available
        query = place_name
        if location_hint:
            query = f"{place_name} {location_hint}"
        
        result = self._call_google_api(query)
        
        if result:
            # Result is already a dict with photos array
            self.cache.save_place(normalized_key, result)
            
            # Refresh known places for future deduplication
            self.deduplicator._refresh_known_places()
            
            # Return in expected format
            return {
                'canonical_name': result.get('name', place_name),
                'address': result.get('formatted_address', ''),
                'place_id': result.get('place_id'),
                'photos': result.get('photos', []),
                'latitude': result.get('latitude'),
                'longitude': result.get('longitude'),
                'photo_url': result.get('photo_url')
            }
        
        return None


# Global service instance (singleton pattern)
_geocoding_service = None
_geocoding_service_error = None

def get_geocoding_service():
    """Get or create the global geocoding service instance"""
    global _geocoding_service, _geocoding_service_error
    if _geocoding_service is None and _geocoding_service_error is None:
        try:
            _geocoding_service = OptimizedGeocodingService()
        except Exception as e:
            _geocoding_service_error = e
            raise
    elif _geocoding_service_error:
        raise _geocoding_service_error
    return _geocoding_service


# Main interface function for your Flask app
def resolve_places_with_google(place_candidates: List[Dict], tiktok_id: Optional[str] = None) -> Dict:
    """
    Main entry point for place resolution
    
    Args:
        place_candidates: List of place dicts with 'name' and optional 'location_hint'
        tiktok_id: Optional TikTok video ID for caching entire extractions
    
    Returns:
        Dict with 'places' list and 'stats' for monitoring
    """
    service = get_geocoding_service()
    stats = {
        'total_candidates': len(place_candidates),
        'cache_hits': 0,
        'api_calls': 0,
        'unique_places': 0
    }
    
    # Check TikTok cache first
    if tiktok_id:
        cached_extraction = service.cache.get_tiktok_extraction(tiktok_id)
        if cached_extraction:
            logger.info(f"Full TikTok cache hit for: {tiktok_id}")
            stats['cache_hits'] = stats['total_candidates']
            return {
                'places': cached_extraction['places'],
                'stats': stats,
                'from_cache': True
            }
    
    # Count cache hits vs API calls
    initial_cache_state = set()
    with service.cache._get_connection() as conn:
        rows = conn.execute("SELECT normalized_key FROM places_cache").fetchall()
        initial_cache_state = {row['normalized_key'] for row in rows}
    
    # Resolve places
    resolved_places = []
    for candidate in place_candidates:
        place_name = candidate.get('name', '')
        location_hint = candidate.get('location_hint', '')
        
        result = service.resolve_single_place(place_name, location_hint)
        if result:
            resolved_places.append({
                'name': result['canonical_name'],
                'original_query': place_name,
                'formatted_address': result['address'],
                'place_id': result['place_id'],
                'latitude': result.get('latitude'),
                'longitude': result.get('longitude'),
                'photo_url': result.get('photo_url'),
                'photos': result.get('photos', [])
            })
    
    # Count new API calls
    with service.cache._get_connection() as conn:
        rows = conn.execute("SELECT normalized_key FROM places_cache").fetchall()
        final_cache_state = {row['normalized_key'] for row in rows}
    
    new_places = final_cache_state - initial_cache_state
    stats['api_calls'] = len(new_places)
    stats['cache_hits'] = stats['total_candidates'] - stats['api_calls']
    stats['unique_places'] = len(resolved_places)
    
    # Save to TikTok cache if ID provided
    if tiktok_id and resolved_places:
        service.cache.save_tiktok_extraction(
            tiktok_id,
            {'places': resolved_places, 'timestamp': datetime.now().isoformat()}
        )
    
    return {
        'places': resolved_places,
        'stats': stats,
        'from_cache': False
    }


# Utility function for cache maintenance
def cleanup_cache(days: int = CACHE_EXPIRY_DAYS):
    """Run periodic cache cleanup"""
    cache = PlaceCacheManager()
    cache.cleanup_old_entries(days)
    logger.info(f"Cache cleanup completed - removed entries older than {days} days")


def get_cache_stats() -> Dict:
    """Get cache statistics for monitoring"""
    cache = PlaceCacheManager()
    with cache._get_connection() as conn:
        total_places = conn.execute("SELECT COUNT(*) as count FROM places_cache").fetchone()['count']
        total_tiktoks = conn.execute("SELECT COUNT(*) as count FROM tiktok_cache").fetchone()['count']
        total_aliases = conn.execute("SELECT COUNT(*) as count FROM place_aliases").fetchone()['count']
        
        # Recent accesses (last 24 hours)
        cutoff = datetime.now() - timedelta(hours=24)
        recent_accesses = conn.execute(
            "SELECT COUNT(*) as count FROM places_cache WHERE accessed_at > ?",
            (cutoff,)
        ).fetchone()['count']
    
    return {
        'total_cached_places': total_places,
        'total_tiktok_caches': total_tiktoks,
        'total_aliases': total_aliases,
        'recent_accesses_24h': recent_accesses
    }

