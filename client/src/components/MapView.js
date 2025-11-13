import React, { useMemo } from "react";
import { GoogleMap, LoadScript, Marker, InfoWindow } from "@react-google-maps/api";

const GOOGLE_MAPS_API_KEY = process.env.REACT_APP_GOOGLE_MAPS_API_KEY || "";

const MapView = ({ places }) => {
  // Filter places that have addresses or can be geocoded
  const placesWithLocation = useMemo(() => {
    return places.filter(p => p.address || p.maps_url || p.name);
  }, [places]);

  // Calculate center of map (NYC default)
  const center = useMemo(() => {
    // Default to NYC
    return { lat: 40.7128, lng: -73.9352 };
  }, []);

  const [selectedPlace, setSelectedPlace] = React.useState(null);
  const [placePositions, setPlacePositions] = React.useState({});

  // Geocode places using Google Geocoding API
  React.useEffect(() => {
    if (!GOOGLE_MAPS_API_KEY || placesWithLocation.length === 0) return;

    const geocodePlace = async (place) => {
      const query = place.address || `${place.name} NYC`;
      try {
        const response = await fetch(
          `https://maps.googleapis.com/maps/api/geocode/json?address=${encodeURIComponent(query)}&key=${GOOGLE_MAPS_API_KEY}`
        );
        const data = await response.json();
        if (data.results && data.results.length > 0) {
          const location = data.results[0].geometry.location;
          return { lat: location.lat, lng: location.lng };
        }
      } catch (error) {
        console.error(`Failed to geocode ${place.name}:`, error);
      }
      // Fallback to NYC with slight offset
      return {
        lat: center.lat + (Math.random() - 0.5) * 0.05,
        lng: center.lng + (Math.random() - 0.5) * 0.05,
      };
    };

    const geocodeAll = async () => {
      const positions = {};
      for (const place of placesWithLocation) {
        positions[place.name] = await geocodePlace(place);
      }
      setPlacePositions(positions);
    };

    geocodeAll();
  }, [placesWithLocation, GOOGLE_MAPS_API_KEY, center]);

  const mapContainerStyle = {
    width: "100%",
    height: "600px",
    borderRadius: "8px",
    marginTop: "20px",
  };

  const mapOptions = {
    zoom: 13,
    center: center,
    mapTypeControl: true,
    streetViewControl: false,
    fullscreenControl: true,
  };

  // If no API key, show a message
  if (!GOOGLE_MAPS_API_KEY) {
    return (
      <div style={{ padding: "40px", textAlign: "center", color: "#888" }}>
        <p>Map view requires Google Maps API key.</p>
        <p style={{ fontSize: "0.9rem", marginTop: "10px" }}>
          Places can still be viewed in list/grid view.
        </p>
      </div>
    );
  }

  return (
    <div style={{ marginTop: "20px" }}>
      <LoadScript googleMapsApiKey={GOOGLE_MAPS_API_KEY}>
        <GoogleMap
          mapContainerStyle={mapContainerStyle}
          options={mapOptions}
          center={center}
          zoom={13}
        >
          {placesWithLocation.map((place, index) => {
            const position = placePositions[place.name] || {
              lat: center.lat + (Math.random() - 0.5) * 0.05,
              lng: center.lng + (Math.random() - 0.5) * 0.05,
            };
            
            return (
              <Marker
                key={index}
                position={position}
                onClick={() => setSelectedPlace(place)}
                title={place.name}
              />
            );
          })}

          {selectedPlace && placePositions[selectedPlace.name] && (
            <InfoWindow
              position={placePositions[selectedPlace.name]}
              onCloseClick={() => setSelectedPlace(null)}
            >
              <div style={{ padding: "8px", maxWidth: "250px" }}>
                <h3 style={{ margin: "0 0 8px 0", fontSize: "16px", fontWeight: "bold" }}>
                  {selectedPlace.name}
                </h3>
                {selectedPlace.summary && (
                  <p style={{ margin: "0 0 8px 0", fontSize: "14px", color: "#666" }}>
                    {selectedPlace.summary.substring(0, 100)}...
                  </p>
                )}
                {selectedPlace.address && (
                  <p style={{ margin: "0 0 8px 0", fontSize: "12px", color: "#888" }}>
                    {selectedPlace.address}
                  </p>
                )}
                {selectedPlace.maps_url && (
                  <a
                    href={selectedPlace.maps_url}
                    target="_blank"
                    rel="noreferrer"
                    style={{
                      display: "inline-block",
                      marginTop: "8px",
                      padding: "6px 12px",
                      backgroundColor: "#007bff",
                      color: "white",
                      textDecoration: "none",
                      borderRadius: "4px",
                      fontSize: "12px",
                    }}
                  >
                    Open in Maps
                  </a>
                )}
              </div>
            </InfoWindow>
          )}
        </GoogleMap>
      </LoadScript>
      
      {/* List of places below map */}
      <div style={{ marginTop: "20px" }}>
        <h3 style={{ marginBottom: "12px", fontSize: "18px" }}>
          Places on Map ({placesWithLocation.length})
        </h3>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))", gap: "12px" }}>
          {placesWithLocation.map((place, index) => (
            <div
              key={index}
              onClick={() => setSelectedPlace(place)}
              style={{
                padding: "12px",
                border: "1px solid #ddd",
                borderRadius: "6px",
                cursor: "pointer",
                backgroundColor: selectedPlace?.name === place.name ? "#f0f8ff" : "white",
              }}
            >
              <h4 style={{ margin: "0 0 6px 0", fontSize: "14px", fontWeight: "bold" }}>
                {place.name}
              </h4>
              {place.address && (
                <p style={{ margin: "0", fontSize: "12px", color: "#666" }}>
                  {place.address}
                </p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default MapView;

