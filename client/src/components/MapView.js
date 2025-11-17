import React, { useMemo, useRef, useEffect, useState } from "react";
import { GoogleMap, LoadScript, Marker } from "@react-google-maps/api";
import "./MapView.css";

const GOOGLE_MAPS_API_KEY = process.env.REACT_APP_GOOGLE_MAPS_API_KEY || "";

const MapView = ({ places, onClose }) => {
  // Filter places that have addresses or can be geocoded
  const placesWithLocation = useMemo(() => {
    return places.filter(p => p.address || p.maps_url || p.name);
  }, [places]);

  // Default center (NYC)
  const defaultCenter = { lat: 40.7128, lng: -73.9352 };

  const [placePositions, setPlacePositions] = useState({});
  const [mapCenter, setMapCenter] = useState(defaultCenter);
  const [mapZoom, setMapZoom] = useState(12);
  const [mapError, setMapError] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const mapRef = useRef(null);

  // Debug: Log API key status
  useEffect(() => {
    console.log('🗺️ MapView Debug:');
    console.log('  - API Key exists:', !!GOOGLE_MAPS_API_KEY);
    console.log('  - API Key length:', GOOGLE_MAPS_API_KEY?.length || 0);
    console.log('  - Places count:', placesWithLocation.length);
  }, []);

  // Geocode places using Google Geocoding API
  useEffect(() => {
    if (!GOOGLE_MAPS_API_KEY) {
      setMapError("Google Maps API key is missing");
      setIsLoading(false);
      return;
    }
    
    if (placesWithLocation.length === 0) {
      setPlacePositions({});
      setIsLoading(false);
      setMapCenter(defaultCenter);
      setMapZoom(12);
      return;
    }

    setIsLoading(true);
    setMapError(null);

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
      // Fallback: random position near NYC
      return {
        lat: defaultCenter.lat + (Math.random() - 0.5) * 0.05,
        lng: defaultCenter.lng + (Math.random() - 0.5) * 0.05,
      };
    };

    const geocodeAll = async () => {
      try {
      const positions = {};
      for (const place of placesWithLocation) {
        positions[place.name] = await geocodePlace(place);
      }
      setPlacePositions(positions);

      const positionsArray = Object.values(positions);
      if (positionsArray.length > 0) {
        const avgLat = positionsArray.reduce((sum, pos) => sum + pos.lat, 0) / positionsArray.length;
        const avgLng = positionsArray.reduce((sum, pos) => sum + pos.lng, 0) / positionsArray.length;

        setMapCenter({ lat: avgLat, lng: avgLng });
          setMapZoom(13);
        }
        setIsLoading(false);
      } catch (error) {
        console.error('Geocoding error:', error);
        setMapError('Failed to geocode places');
        setIsLoading(false);
      }
    };

    geocodeAll();
  }, [placesWithLocation, GOOGLE_MAPS_API_KEY, defaultCenter]);

  // Simple map options - no custom styling
  const mapOptions = useMemo(() => ({
    zoom: mapZoom,
    center: mapCenter,
    zoomControl: true,
    mapTypeControl: true,
    streetViewControl: false,
    fullscreenControl: true,
  }), [mapZoom, mapCenter]);

  // If no API key, show error
  if (!GOOGLE_MAPS_API_KEY || GOOGLE_MAPS_API_KEY.trim() === "") {
    return (
      <div style={{ padding: "40px", textAlign: "center", color: "#888" }}>
        <p style={{ fontSize: "1.1rem", marginBottom: "10px" }}>
          ⚠️ Map view requires Google Maps API key.
        </p>
        <p style={{ fontSize: "0.9rem", color: "#aaa" }}>
          Set REACT_APP_GOOGLE_MAPS_API_KEY environment variable.
        </p>
        {onClose && (
          <button 
            onClick={onClose}
            style={{ marginTop: "20px", padding: "10px 20px", cursor: "pointer" }}
          >
            Close
          </button>
        )}
      </div>
    );
  }

  // Show error if geocoding failed
  if (mapError) {
  return (
      <div style={{ padding: "40px", textAlign: "center", color: "#f87171" }}>
        <p style={{ fontSize: "1.1rem", marginBottom: "10px" }}>❌ {mapError}</p>
        <p style={{ fontSize: "0.9rem", color: "#888", marginTop: "10px" }}>
          Check browser console for details.
        </p>
      {onClose && (
        <button 
          onClick={onClose}
            style={{ marginTop: "20px", padding: "10px 20px", cursor: "pointer" }}
        >
            Close
        </button>
      )}
      </div>
    );
  }

  return (
    <div className="map-simple-container">
      <div className="map-section-simple">
        <LoadScript 
          googleMapsApiKey={GOOGLE_MAPS_API_KEY}
          loadingElement={
          <div style={{ 
              width: '100%',
              height: '500px',
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center', 
              background: '#f0f0f0',
              color: '#666'
          }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '24px', marginBottom: '12px' }}>🗺️</div>
              <div>Loading map...</div>
            </div>
          </div>
          }
          onError={(error) => {
            console.error('❌ LoadScript error:', error);
            setMapError(`Failed to load Google Maps: ${error.message || 'Unknown error'}`);
            setIsLoading(false);
          }}
        >
        <GoogleMap
            mapContainerStyle={{
              width: "100%",
              height: "500px",
            }}
          options={mapOptions}
          center={mapCenter}
          zoom={mapZoom}
            onLoad={(map) => {
              console.log('✅ Map loaded successfully');
              mapRef.current = map;
              setIsLoading(false);
              
              // Center map on places if we have positions
              if (Object.keys(placePositions).length > 0) {
                const positionsArray = Object.values(placePositions);
                const avgLat = positionsArray.reduce((sum, pos) => sum + pos.lat, 0) / positionsArray.length;
                const avgLng = positionsArray.reduce((sum, pos) => sum + pos.lng, 0) / positionsArray.length;
                
                setTimeout(() => {
                  if (map && map.panTo) {
                    try {
                      map.panTo({ lat: avgLat, lng: avgLng });
                      map.setZoom(13);
                    } catch (error) {
                      console.error('Error centering map:', error);
                    }
                  }
                }, 300);
              }
            }}
            onError={(error) => {
              console.error('❌ GoogleMap error:', error);
              setMapError(`Map error: ${error.message || 'Unknown error'}`);
              setIsLoading(false);
            }}
        >
          {placesWithLocation.map((place, index) => {
            const position = placePositions[place.name] || {
              lat: mapCenter.lat + (Math.random() - 0.5) * 0.05,
              lng: mapCenter.lng + (Math.random() - 0.5) * 0.05,
            };
            
            return (
              <Marker
                  key={`${place.name}-${index}`}
                position={position}
                  title={place.name}
                  label={{
                    text: String(index + 1),
                    color: '#000',
                    fontSize: '12px',
                    fontWeight: 'bold',
                  }}
              />
            );
          })}
          </GoogleMap>
        </LoadScript>
      </div>
    </div>
  );
};

export default MapView;
