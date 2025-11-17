import React, { useMemo, useRef, useEffect, useState, useCallback } from "react";
import { GoogleMap, LoadScript, Marker } from "@react-google-maps/api";
import "./MapView.css";

const GOOGLE_MAPS_API_KEY = process.env.REACT_APP_GOOGLE_MAPS_API_KEY || "";

const MapView = ({ places, savedPlaces = {}, togglePlaceInList, handleAddNewList, isInList, onClose }) => {
  // Filter places that have addresses or can be geocoded
  const placesWithLocation = useMemo(() => {
    return places.filter(p => p.address || p.maps_url || p.name);
  }, [places]);

  // Default center (NYC)
  const defaultCenter = { lat: 40.7128, lng: -73.9352 };

  const [selectedPlace, setSelectedPlace] = useState(null);
  const [placePositions, setPlacePositions] = useState({});
  const [mapCenter, setMapCenter] = useState(defaultCenter);
  const [mapZoom, setMapZoom] = useState(12);
  const [isLoading, setIsLoading] = useState(true);
  const [mapLoaded, setMapLoaded] = useState(false);
  const mapRef = useRef(null);

  // Geocode places using Google Geocoding API and calculate center
  useEffect(() => {
    if (!GOOGLE_MAPS_API_KEY) {
      setPlacePositions({});
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

      const positionsArray = Object.values(positions);
      if (positionsArray.length > 0) {
        const avgLat = positionsArray.reduce((sum, pos) => sum + pos.lat, 0) / positionsArray.length;
        const avgLng = positionsArray.reduce((sum, pos) => sum + pos.lng, 0) / positionsArray.length;
        
        const lats = positionsArray.map(pos => pos.lat);
        const lngs = positionsArray.map(pos => pos.lng);
        const latDiff = Math.max(...lats) - Math.min(...lats);
        const lngDiff = Math.max(...lngs) - Math.min(...lngs);
        const maxDiff = Math.max(latDiff, lngDiff);
        
        let zoom = 13;
        if (maxDiff > 0.1) zoom = 11;
        else if (maxDiff > 0.05) zoom = 12;
        else if (maxDiff > 0.02) zoom = 13;
        else if (maxDiff > 0.01) zoom = 14;
        else zoom = 15;

        setMapCenter({ lat: avgLat, lng: avgLng });
        setMapZoom(zoom);
        
        // Center map once it's loaded
        if (mapRef.current) {
          setTimeout(() => {
            mapRef.current.panTo({ lat: avgLat, lng: avgLng });
            mapRef.current.setZoom(zoom);
          }, 100);
        }
      }
      setIsLoading(false);
    };

    geocodeAll();
  }, [placesWithLocation, GOOGLE_MAPS_API_KEY, defaultCenter]);

  // Center map on all places when they're loaded
  useEffect(() => {
    if (mapLoaded && mapRef.current && Object.keys(placePositions).length > 0) {
      const positionsArray = Object.values(placePositions);
      
      if (positionsArray.length === 1) {
        const pos = positionsArray[0];
        setMapCenter({ lat: pos.lat, lng: pos.lng });
        setMapZoom(15);
        setTimeout(() => {
          if (mapRef.current && mapRef.current.panTo) {
            try {
              mapRef.current.panTo({ lat: pos.lat, lng: pos.lng });
              mapRef.current.setZoom(15);
            } catch (error) {
              console.error('Error centering map:', error);
            }
          }
        }, 500);
      } else {
        const lats = positionsArray.map(pos => pos.lat);
        const lngs = positionsArray.map(pos => pos.lng);
        const minLat = Math.min(...lats);
        const maxLat = Math.max(...lats);
        const minLng = Math.min(...lngs);
        const maxLng = Math.max(...lngs);
        
        const avgLat = (minLat + maxLat) / 2;
        const avgLng = (minLng + maxLng) / 2;
        
        const latDiff = maxLat - minLat;
        const lngDiff = maxLng - minLng;
        const maxDiff = Math.max(latDiff, lngDiff);
        
        let zoom = 13;
        if (maxDiff > 0.1) zoom = 10;
        else if (maxDiff > 0.05) zoom = 11;
        else if (maxDiff > 0.02) zoom = 12;
        else if (maxDiff > 0.01) zoom = 13;
        else if (maxDiff > 0.005) zoom = 14;
        else zoom = 15;
        
        setMapCenter({ lat: avgLat, lng: avgLng });
        setMapZoom(zoom);
        
        setTimeout(() => {
          if (mapRef.current && window.google && window.google.maps) {
            try {
              const googleBounds = new window.google.maps.LatLngBounds();
              positionsArray.forEach(pos => {
                googleBounds.extend(new window.google.maps.LatLng(pos.lat, pos.lng));
              });
              
              mapRef.current.fitBounds(googleBounds, {
                top: 50,
                right: 50,
                bottom: 50,
                left: 50
              });
            } catch (error) {
              console.error('Error fitting bounds:', error);
              if (mapRef.current.panTo) {
                mapRef.current.panTo({ lat: avgLat, lng: avgLng });
                mapRef.current.setZoom(zoom);
              }
            }
          }
        }, 500);
      }
    } else if (mapLoaded && mapRef.current && Object.keys(placePositions).length === 0) {
      setTimeout(() => {
        if (mapRef.current && mapRef.current.panTo) {
          try {
            mapRef.current.panTo(defaultCenter);
            mapRef.current.setZoom(12);
          } catch (error) {
            console.error('Error centering map on default:', error);
          }
        }
      }, 300);
    }
  }, [mapLoaded, placePositions, defaultCenter]);

  const mapOptions = useMemo(() => ({
    zoom: mapZoom,
    center: mapCenter,
    mapTypeControl: false,
    streetViewControl: false,
    fullscreenControl: true,
    backgroundColor: '#0a0a0f',
    gestureHandling: 'greedy',
    disableDefaultUI: false,
    zoomControl: true,
    zoomControlOptions: {
      position: window.google?.maps?.ControlPosition?.RIGHT_BOTTOM || 11
    },
    minZoom: 10,
    maxZoom: 18,
    styles: [
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
  }), [mapZoom, mapCenter]);

  // Custom marker icon
  const createMarkerIcon = useCallback((index, isSelected) => {
    const baseSize = 32;
    const size = isSelected ? 44 : baseSize;
    const color = isSelected ? '#6366f1' : '#fff';
    
    let anchor;
    try {
      if (window.google && window.google.maps && window.google.maps.Point) {
        anchor = new window.google.maps.Point(0, -10);
      } else {
        anchor = { x: 0, y: -10 };
      }
    } catch (error) {
      anchor = { x: 0, y: -10 };
    }
    
    return {
      path: 'M 0,0 C -2,-20 -10,-22 -10,-30 A 10,10 0 1,1 10,-30 C 10,-22 2,-20 0,0 z',
      fillColor: color,
      fillOpacity: 1,
      strokeColor: '#1a1a26',
      strokeWeight: isSelected ? 3 : 2,
      scale: size / baseSize,
      anchor: anchor,
      label: {
        text: String(index + 1),
        color: '#1a1a26',
        fontSize: isSelected ? '14px' : '12px',
        fontWeight: 'bold',
      }
    };
  }, []);

  // Center map on place when selected
  useEffect(() => {
    if (selectedPlace && placePositions[selectedPlace.name] && mapRef.current) {
      const position = placePositions[selectedPlace.name];
      try {
        mapRef.current.panTo({
          lat: position.lat,
          lng: position.lng
        });
      } catch (error) {
        console.error('Error panning map:', error);
      }
    }
  }, [selectedPlace, placePositions]);

  // If no API key, show a message
  if (!GOOGLE_MAPS_API_KEY || GOOGLE_MAPS_API_KEY.trim() === "") {
    return (
      <div className="map-simple-container" style={{ padding: "40px", textAlign: "center", color: "#888", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column" }}>
        <p style={{ fontSize: "1.1rem", marginBottom: "10px" }}>Map view requires Google Maps API key.</p>
        <p style={{ fontSize: "0.9rem", marginTop: "10px", color: "#aaa" }}>
          Places can still be viewed in list view.
        </p>
        {onClose && (
          <button 
            className="map-close-btn"
            onClick={onClose}
            aria-label="Close map view"
            style={{ marginTop: "20px" }}
          >
            ✕
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="map-simple-container">
      {/* Map Section */}
      <div className="map-section-simple">
        <LoadScript 
          googleMapsApiKey={GOOGLE_MAPS_API_KEY}
          libraries={['places', 'geometry']}
          loadingElement={
            <div style={{ 
              width: '100%',
              height: '500px',
              display: 'flex', 
              alignItems: 'center', 
              justifyContent: 'center', 
              color: '#fff',
              background: '#0a0a0f'
            }}>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: '24px', marginBottom: '12px' }}>🗺️</div>
                <div>Loading map...</div>
              </div>
            </div>
          }
          onError={(error) => {
            console.error('LoadScript error:', error);
            setIsLoading(false);
          }}
        >
          <GoogleMap
            mapContainerStyle={{
              width: "100%",
              height: "500px",
              minHeight: "500px",
            }}
            options={mapOptions}
            center={mapCenter}
            zoom={mapZoom}
            onLoad={(map) => {
              console.log('Map loaded successfully');
              mapRef.current = map;
              setMapLoaded(true);
              setIsLoading(false);
              
              if (Object.keys(placePositions).length === 0) {
                setTimeout(() => {
                  if (map && map.panTo) {
                    try {
                      map.panTo(defaultCenter);
                      map.setZoom(12);
                    } catch (error) {
                      console.error('Error centering map:', error);
                    }
                  }
                }, 100);
              } else {
                const positionsArray = Object.values(placePositions);
                const avgLat = positionsArray.reduce((sum, pos) => sum + pos.lat, 0) / positionsArray.length;
                const avgLng = positionsArray.reduce((sum, pos) => sum + pos.lng, 0) / positionsArray.length;
                
                setTimeout(() => {
                  if (map && map.panTo) {
                    try {
                      map.panTo({ lat: avgLat, lng: avgLng });
                    } catch (error) {
                      console.error('Error centering map on places:', error);
                    }
                  }
                }, 200);
              }
            }}
            onError={(error) => {
              console.error('GoogleMap error:', error);
              setIsLoading(false);
            }}
            onIdle={() => {
              setIsLoading(false);
            }}
          >
            {placesWithLocation.map((place, index) => {
              const position = placePositions[place.name] || {
                lat: mapCenter.lat + (Math.random() - 0.5) * 0.05,
                lng: mapCenter.lng + (Math.random() - 0.5) * 0.05,
              };
              const isSelected = selectedPlace?.name === place.name;
            
              return (
                <Marker
                  key={`${place.name}-${index}`}
                  position={position}
                  onClick={() => {
                    setSelectedPlace(place);
                  }}
                  icon={createMarkerIcon(index, isSelected)}
                  title={place.name}
                  zIndex={isSelected ? 1000 : index}
                  animation={isSelected && window.google?.maps?.Animation?.BOUNCE ? window.google.maps.Animation.BOUNCE : undefined}
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
