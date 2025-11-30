"""
Centralized NYC venue filtering logic.

This module provides a single source of truth for determining if a venue
is located in NYC based on its address and neighborhood information.
"""

NYC_INDICATORS = [
    "new york",
    "ny",
    "manhattan",
    "brooklyn",
    "queens",
    "bronx",
    "staten island"
]

NON_NYC_INDICATORS = [
    "nj", "new jersey", "jersey city", "hoboken", "elwood", "jersey",
    "denver", "co", "colorado",
    "california", "ca", "los angeles", "la", "san francisco", "sf", "san diego",
    "chicago", "il", "illinois",
    "miami", "fl", "florida",
    "boston", "ma", "massachusetts",
    "seattle", "wa", "washington",
    "portland", "or", "oregon",
    "philadelphia", "pa", "pennsylvania",
    "atlanta", "ga", "georgia",
    "dallas", "tx", "texas",
    "houston",
    "austin",
    "phoenix", "az", "arizona",
    "las vegas", "nv", "nevada"
]


def is_nyc_venue(address: str, neighborhood: str = "") -> tuple[bool, str]:
    """
    Determines if a venue is in NYC based on address/neighborhood.

    Args:
        address: The venue's formatted address
        neighborhood: The venue's neighborhood (optional)

    Returns:
        tuple: (is_nyc, reason) where:
            - is_nyc (bool): True if venue is in NYC, False otherwise
            - reason (str): Explanation of the decision

    Logic:
        - Reject ONLY if venue has non-NYC indicators AND no NYC indicators
        - Keep if venue has NYC indicators (even if it also has non-NYC indicators)
        - Keep if venue has no location indicators (assume NYC for MVP)
    """
    address_lower = (address or "").lower()
    neighborhood_lower = (neighborhood or "").lower()
    combined = f"{address_lower} {neighborhood_lower}"

    # Check for NYC indicators
    has_nyc = any(indicator in combined for indicator in NYC_INDICATORS)

    # Check for non-NYC indicators
    has_non_nyc = any(indicator in combined for indicator in NON_NYC_INDICATORS)

    # Decision logic
    if has_non_nyc and not has_nyc:
        # Definitely not NYC - has non-NYC location but no NYC indicators
        return False, "Has non-NYC indicator, no NYC indicator"
    elif has_nyc:
        # Has NYC indicators - keep it (even if it also has non-NYC indicators)
        return True, "Has NYC indicator"
    else:
        # No clear location indicators - assume NYC for MVP
        return True, "No location indicators (assuming NYC for MVP)"
