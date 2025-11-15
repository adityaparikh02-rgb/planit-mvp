import React, { useMemo } from "react";
import { GoogleMap, LoadScript, Marker } from "@react-google-maps/api";
import "./MapView.css";

const GOOGLE_MAPS_API_KEY = process.env.REACT_APP_GOOGLE_MAPS_API_KEY || "";

const MapView = ({ places, savedPlaces = {}, togglePlaceInList, handleAddNewList, isInList }) => {
  // Filter places that have addresses or can be geocoded
  const placesWithLocation = useMemo(() => {
    return places.filter(p => p.address || p.maps_url || p.name);
  }, [places]);

  // Default center (NYC)
  const defaultCenter = { lat: 40.7128, lng: -73.9352 };

  const [selectedPlace, setSelectedPlace] = React.useState(null);
  const [placePositions, setPlacePositions] = React.useState({});
  const [mapCenter, setMapCenter] = React.useState(defaultCenter);
  const [mapZoom, setMapZoom] = React.useState(13);
  const [showListMenu, setShowListMenu] = React.useState(false);

  // Geocode places using Google Geocoding API and calculate center
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
      // Fallback to default center with slight offset
      return {
        lat: defaultCenter.lat + (Math.random() - 0.5) * 0.05,
        lng: defaultCenter.lng + (Math.random() - 0.5) * 0.05,
      };
    };

    const geocodeAll = async () => {
      const positions = {};
      for (const place of placesWithLocation) {
        positions[place.name] = await geocodePlace(place);
      }
      setPlacePositions(positions);

      // Calculate center of all places
      const positionsArray = Object.values(positions);
      if (positionsArray.length > 0) {
        const avgLat = positionsArray.reduce((sum, pos) => sum + pos.lat, 0) / positionsArray.length;
        const avgLng = positionsArray.reduce((sum, pos) => sum + pos.lng, 0) / positionsArray.length;
        
        // Calculate bounds to determine appropriate zoom
        const lats = positionsArray.map(pos => pos.lat);
        const lngs = positionsArray.map(pos => pos.lng);
        const latDiff = Math.max(...lats) - Math.min(...lats);
        const lngDiff = Math.max(...lngs) - Math.min(...lngs);
        const maxDiff = Math.max(latDiff, lngDiff);
        
        // Adjust zoom based on spread of places
        let zoom = 13;
        if (maxDiff > 0.1) zoom = 11;
        else if (maxDiff > 0.05) zoom = 12;
        else if (maxDiff > 0.02) zoom = 13;
        else if (maxDiff > 0.01) zoom = 14;
        else zoom = 15;

        setMapCenter({ lat: avgLat, lng: avgLng });
        setMapZoom(zoom);
      }
    };

    geocodeAll();
  }, [placesWithLocation, GOOGLE_MAPS_API_KEY]);

  const mapContainerStyle = {
    width: "100%",
    height: "600px",
    borderRadius: "14px",
    marginTop: "20px",
    border: "1px solid rgba(255, 255, 255, 0.1)",
    boxShadow: "0 8px 24px rgba(0, 0, 0, 0.3)",
  };

  const mapOptions = {
    zoom: mapZoom,
    center: mapCenter,
    mapTypeControl: false, // Disable satellite/terrain view toggle
    streetViewControl: false,
    fullscreenControl: true,
    styles: [
      // Dark mode styling to match app theme
      {
        featureType: "all",
        elementType: "geometry",
        stylers: [{ color: "#1a1a26" }]
      },
      {
        featureType: "all",
        elementType: "labels.text.fill",
        stylers: [{ color: "#aab1c6" }]
      },
      {
        featureType: "all",
        elementType: "labels.text.stroke",
        stylers: [{ color: "#0c0c13" }]
      },
      {
        featureType: "water",
        elementType: "geometry",
        stylers: [{ color: "#0c0c13" }]
      },
      {
        featureType: "water",
        elementType: "labels.text.fill",
        stylers: [{ color: "#4a5568" }]
      },
      {
        featureType: "road",
        elementType: "geometry",
        stylers: [{ color: "#12121a" }]
      },
      {
        featureType: "road",
        elementType: "geometry.stroke",
        stylers: [{ color: "#1a1a26" }]
      },
      {
        featureType: "road",
        elementType: "labels.text.fill",
        stylers: [{ color: "#8b92b0" }]
      },
      {
        featureType: "road.highway",
        elementType: "geometry",
        stylers: [{ color: "#1a1a26" }]
      },
      {
        featureType: "road.highway",
        elementType: "geometry.stroke",
        stylers: [{ color: "#0c0c13" }]
      },
      {
        featureType: "road.highway",
        elementType: "labels.text.fill",
        stylers: [{ color: "#aab1c6" }]
      },
      {
        featureType: "poi",
        elementType: "geometry",
        stylers: [{ color: "#1a1a26" }]
      },
      {
        featureType: "poi",
        elementType: "labels.text.fill",
        stylers: [{ color: "#8b92b0" }]
      },
      {
        featureType: "transit",
        elementType: "geometry",
        stylers: [{ color: "#12121a" }]
      },
      {
        featureType: "transit.station",
        elementType: "geometry",
        stylers: [{ color: "#1a1a26" }]
      },
      {
        featureType: "administrative",
        elementType: "geometry.stroke",
        stylers: [{ color: "#0c0c13" }]
      },
      {
        featureType: "administrative",
        elementType: "labels.text.fill",
        stylers: [{ color: "#8b92b0" }]
      }
    ]
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
          center={mapCenter}
          zoom={mapZoom}
        >
          {placesWithLocation.map((place, index) => {
            const position = placePositions[place.name] || {
              lat: mapCenter.lat + (Math.random() - 0.5) * 0.05,
              lng: mapCenter.lng + (Math.random() - 0.5) * 0.05,
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

        </GoogleMap>
      </LoadScript>
      
      {/* Dark glossy modal for place details */}
      {selectedPlace && (
        <div className="map-place-modal-overlay" onClick={() => {
          setSelectedPlace(null);
          setShowListMenu(false);
        }}>
          <div className="map-place-modal" onClick={(e) => e.stopPropagation()}>
            <button className="map-modal-close" onClick={() => {
              setSelectedPlace(null);
              setShowListMenu(false);
            }}>
              ✕
            </button>
            
            {selectedPlace.photo_url && (
              <div className="map-modal-photo-wrapper">
                <img 
                  src={selectedPlace.photo_url} 
                  alt={selectedPlace.name}
                  className="map-modal-photo"
                />
              </div>
            )}
            
            <div className="map-modal-content">
              <div className="map-modal-header">
                <h2 className="map-modal-title">{selectedPlace.name}</h2>
                <button
                  className="map-modal-menu-btn"
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowListMenu(!showListMenu);
                  }}
                >
                  ⋯
                </button>
                {showListMenu && (
                  <div className="map-modal-menu-popup" onClick={(e) => e.stopPropagation()}>
                    {Object.keys(savedPlaces).length > 0 ? (
                      Object.keys(savedPlaces).map((list, idx) => {
                        const selected = isInList ? isInList(list, selectedPlace) : false;
                        return (
                          <button
                            key={idx}
                            onClick={() => {
                              if (togglePlaceInList) {
                                togglePlaceInList(list, selectedPlace);
                              }
                              setShowListMenu(false);
                            }}
                            className={`map-modal-list-toggle ${selected ? "selected" : ""}`}
                          >
                            <span className={`map-modal-circle ${selected ? "filled" : ""}`}>
                              {selected ? "✓" : ""}
                            </span>
                            {list}
                          </button>
                        );
                      })
                    ) : (
                      <p className="map-modal-empty-list">No lists yet</p>
                    )}
                    <button
                      className="map-modal-add-list-btn"
                      onClick={() => {
                        if (handleAddNewList) {
                          handleAddNewList();
                        }
                        setShowListMenu(false);
                      }}
                    >
                      ➕ Add to New List
                    </button>
                  </div>
                )}
              </div>
              
              {selectedPlace.summary && (
                <p className="map-modal-summary">{selectedPlace.summary}</p>
              )}
              
              {selectedPlace.address && (
                <p className="map-modal-address">📍 {selectedPlace.address}</p>
              )}
              
              {selectedPlace.vibe_tags && selectedPlace.vibe_tags.length > 0 && (
                <div className="map-modal-vibes">
                  <strong>Vibes:</strong>
                  <div className="map-modal-vibe-tags">
                    {selectedPlace.vibe_tags.map((tag, idx) => (
                      <span key={idx} className="map-modal-vibe-chip">{tag}</span>
                    ))}
                  </div>
                </div>
              )}
              
              {selectedPlace.must_try && (
                <p className="map-modal-must-try">
                  <strong>
                    {selectedPlace.must_try_field === "highlights" ? "✨ Highlights:" :
                     selectedPlace.must_try_field === "features" ? "🎯 Features:" :
                     "🍴 Must Try:"}
                  </strong> {selectedPlace.must_try}
                </p>
              )}
              
              {selectedPlace.when_to_go && (
                <p className="map-modal-when">
                  <strong>🕐 When to Go:</strong> {selectedPlace.when_to_go}
                </p>
              )}
              
              {selectedPlace.vibe && (
                <p className="map-modal-vibe">
                  <strong>💫 Vibe:</strong> {selectedPlace.vibe}
                </p>
              )}
              
              <div className="map-modal-actions">
                {selectedPlace.maps_url && (
                  <a
                    href={selectedPlace.maps_url}
                    target="_blank"
                    rel="noreferrer"
                    className="map-modal-action-btn"
                  >
                    🗺️ Open in Maps
                  </a>
                )}
                {selectedPlace.video_url && (
                  <a
                    href={selectedPlace.video_url}
                    target="_blank"
                    rel="noreferrer"
                    className="map-modal-action-btn"
                  >
                    📹 View on TikTok
                  </a>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
      
      {/* List of places below map */}
      <div className="map-places-list">
        <h3 className="map-places-title">
          Places on Map ({placesWithLocation.length})
        </h3>
        <div className="map-places-grid">
          {placesWithLocation.map((place, index) => (
            <div
              key={index}
              className={`map-place-card ${selectedPlace?.name === place.name ? "selected" : ""}`}
              onClick={() => setSelectedPlace(place)}
            >
              {place.photo_url && (
                <img 
                  src={place.photo_url} 
                  alt={place.name}
                  className="map-place-photo"
                />
              )}
              <div style={{ padding: "16px" }}>
                <h4 className="map-place-name">{place.name}</h4>
                {place.address && (
                  <p className="map-place-address">{place.address}</p>
                )}
                {place.summary && (
                  <p className="map-place-summary">{place.summary.substring(0, 80)}...</p>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default MapView;

