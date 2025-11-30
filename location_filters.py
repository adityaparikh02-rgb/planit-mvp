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

# Non-US countries/regions to filter out
NON_US_COUNTRIES = [
    "slovenia", "slovenija", "ljubljana",
    "croatia", "croatia", "zagreb",
    "serbia", "belgrade",
    "bosnia", "sarajevo",
    "montenegro", "podgorica",
    "macedonia", "skopje",
    "albania", "tirana",
    "europe", "european union", "eu",
    "canada", "toronto", "vancouver", "montreal",
    "mexico", "mexico city",
    "united kingdom", "uk", "london", "england", "scotland", "wales",
    "france", "paris",
    "germany", "berlin", "munich",
    "italy", "rome", "milan",
    "spain", "madrid", "barcelona",
    "netherlands", "amsterdam",
    "belgium", "brussels",
    "switzerland", "zurich",
    "austria", "vienna",
    "portugal", "lisbon",
    "greece", "athens",
    "poland", "warsaw",
    "czech republic", "prague",
    "hungary", "budapest",
    "romania", "bucharest",
    "bulgaria", "sofia",
    "denmark", "copenhagen",
    "sweden", "stockholm",
    "norway", "oslo",
    "finland", "helsinki",
    "ireland", "dublin",
    "iceland", "reykjavik",
    "australia", "sydney", "melbourne",
    "new zealand", "auckland", "wellington",
    "japan", "tokyo", "osaka",
    "china", "beijing", "shanghai",
    "south korea", "seoul",
    "singapore",
    "thailand", "bangkok",
    "vietnam", "ho chi minh",
    "philippines", "manila",
    "indonesia", "jakarta",
    "malaysia", "kuala lumpur",
    "india", "mumbai", "delhi",
    "brazil", "sao paulo", "rio de janeiro",
    "argentina", "buenos aires",
    "chile", "santiago",
    "colombia", "bogota",
    "peru", "lima",
    "south africa", "cape town", "johannesburg",
    "egypt", "cairo",
    "turkey", "istanbul",
    "israel", "tel aviv", "jerusalem",
    "uae", "united arab emirates", "dubai", "abu dhabi",
    "saudi arabia", "riyadh",
    "qatar", "doha",
    "kuwait",
    "bahrain",
    "oman", "muscat",
    "jordan", "amman",
    "lebanon", "beirut"
]


def is_nyc_venue(address: str, neighborhood: str = "", country: str = "") -> tuple[bool, str]:
    """
    Determines if a venue is in NYC based on address/neighborhood/country.

    Args:
        address: The venue's formatted address
        neighborhood: The venue's neighborhood (optional)
        country: The venue's country code or name (optional, e.g., "US", "Slovenia")

    Returns:
        tuple: (is_nyc, reason) where:
            - is_nyc (bool): True if venue is in NYC, False otherwise
            - reason (str): Explanation of the decision

    Logic:
        - Reject if country is explicitly non-US (e.g., "Slovenia", "SI")
        - Reject ONLY if venue has non-NYC indicators AND no NYC indicators
        - Keep if venue has NYC indicators (even if it also has non-NYC indicators)
        - Keep if venue has no location indicators (assume NYC for MVP)
    """
    address_lower = (address or "").lower()
    neighborhood_lower = (neighborhood or "").lower()
    country_lower = (country or "").lower()
    combined = f"{address_lower} {neighborhood_lower} {country_lower}"

    # CRITICAL: Check for non-US countries first (most definitive filter)
    # Check if country code is not US (e.g., "SI" for Slovenia, "CA" for Canada)
    if country:
        country_upper = country.upper()
        # Common non-US country codes
        non_us_codes = ["SI", "CA", "MX", "GB", "UK", "FR", "DE", "IT", "ES", "NL", "BE", "CH", "AT", "PT", "GR", "PL", "CZ", "HU", "RO", "BG", "DK", "SE", "NO", "FI", "IE", "IS", "AU", "NZ", "JP", "CN", "KR", "SG", "TH", "VN", "PH", "ID", "MY", "IN", "BR", "AR", "CL", "CO", "PE", "ZA", "EG", "TR", "IL", "AE", "SA", "QA", "KW", "BH", "OM", "JO", "LB"]
        if country_upper in non_us_codes:
            return False, f"Non-US country code: {country_upper}"
        
        # Check if country name matches non-US countries
        if any(non_us_country in country_lower for non_us_country in NON_US_COUNTRIES):
            return False, f"Non-US country: {country}"

    # Check for non-US countries in address/neighborhood text
    if any(non_us_country in combined for non_us_country in NON_US_COUNTRIES):
        # But allow if it also has NYC indicators (e.g., "Amsterdam Avenue" in NYC)
        has_nyc = any(indicator in combined for indicator in NYC_INDICATORS)
        if not has_nyc:
            return False, "Contains non-US country/region name, no NYC indicators"

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
