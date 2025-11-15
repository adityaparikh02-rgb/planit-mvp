import React, { useMemo, useRef, useEffect, useState } from "react";
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
  const [hoveredPlace, setHoveredPlace] = useState(null);
  const [placePositions, setPlacePositions] = useState({});
  const [mapCenter, setMapCenter] = useState(defaultCenter);
  const [mapZoom, setMapZoom] = useState(13);
  const [showListMenu, setShowListMenu] = useState(null);
  const [panelHeight, setPanelHeight] = useState(300); // Height of bottom panel in pixels
  const [isDragging, setIsDragging] = useState(false);
  const [dragStartY, setDragStartY] = useState(0);
  const [dragStartHeight, setDragStartHeight] = useState(0);
  const [expandedPlace, setExpandedPlace] = useState(null);
  const panelRef = useRef(null);
  const cardRefs = useRef({});
  const mapRef = useRef(null);

  // Geocode places using Google Geocoding API and calculate center
  useEffect(() => {
    if (!GOOGLE_MAPS_API_KEY || placesWithLocation.length === 0) {
      // Reset positions if no places
      setPlacePositions({});
      return;
    }

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
  }, [placesWithLocation, GOOGLE_MAPS_API_KEY, defaultCenter]);

  // Handle panel dragging
  const handleDragStart = (clientY) => {
    setIsDragging(true);
    setDragStartY(clientY);
    setDragStartHeight(panelHeight);
  };

  const handleDragMove = (clientY) => {
    const deltaY = dragStartY - clientY; // Inverted: dragging up increases height
    const newHeight = Math.max(200, Math.min(window.innerHeight - 100, dragStartHeight + deltaY));
    setPanelHeight(newHeight);
  };

  const handleDragEnd = () => {
    setIsDragging(false);
  };

  const handleMouseDown = (e) => {
    handleDragStart(e.clientY);
    e.preventDefault();
  };

  const handleTouchStart = (e) => {
    handleDragStart(e.touches[0].clientY);
    e.preventDefault();
  };

  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e) => {
      handleDragMove(e.clientY);
    };

    const handleMouseUp = () => {
      handleDragEnd();
    };

    const handleTouchMove = (e) => {
      handleDragMove(e.touches[0].clientY);
    };

    const handleTouchEnd = () => {
      handleDragEnd();
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.addEventListener('touchmove', handleTouchMove, { passive: false });
    document.addEventListener('touchend', handleTouchEnd);
    
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.removeEventListener('touchmove', handleTouchMove);
      document.removeEventListener('touchend', handleTouchEnd);
    };
  }, [isDragging, dragStartY, dragStartHeight, panelHeight]);

  // Center map on place when selected
  useEffect(() => {
    if (selectedPlace && placePositions[selectedPlace.name] && mapRef.current) {
      const position = placePositions[selectedPlace.name];
      try {
        mapRef.current.panTo(position);
      } catch (error) {
        console.error('Error panning map:', error);
      }
      // Scroll card into view with a slight delay to ensure DOM is ready
      setTimeout(() => {
        const cardElement = cardRefs.current[selectedPlace.name];
        if (cardElement) {
          cardElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }, 100);
    }
  }, [selectedPlace, placePositions]);

  const mapOptions = {
    zoom: mapZoom,
    center: mapCenter,
    mapTypeControl: false,
    streetViewControl: false,
    fullscreenControl: true,
    backgroundColor: '#0a0a0f', // Set background color to match container
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
  };

  // Custom marker icon with number
  const createMarkerIcon = (index, isSelected, isHovered) => {
    const size = isSelected ? 40 : isHovered ? 36 : 32;
    const color = isSelected ? '#6366f1' : isHovered ? '#8da0ff' : '#fff';
    
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
      strokeWeight: 2,
      scale: size / 32,
      anchor: anchor,
      label: {
        text: String(index + 1),
        color: '#1a1a26',
        fontSize: '12px',
        fontWeight: 'bold',
      }
    };
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

  const mapHeight = `calc(100vh - ${panelHeight}px)`;

  return (
    <div className="map-dual-pane-container">
      {/* Close Button */}
      {onClose && (
        <button 
          className="map-close-btn"
          onClick={onClose}
          aria-label="Close map view"
        >
          ✕
        </button>
      )}
      
      {/* Map Section */}
      <div className="map-section" style={{ height: mapHeight, minHeight: '200px' }}>
        <LoadScript googleMapsApiKey={GOOGLE_MAPS_API_KEY} loadingElement={
          <div style={{ 
            height: '100%', 
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
        }>
          <GoogleMap
            mapContainerStyle={{
              width: "100%",
              height: mapHeight,
              minHeight: "200px",
            }}
            options={mapOptions}
            center={mapCenter}
            zoom={mapZoom}
            onLoad={(map) => {
              mapRef.current = map;
              console.log('Map loaded successfully');
            }}
            onError={(error) => {
              console.error('Map error:', error);
            }}
          >
            {placesWithLocation.map((place, index) => {
              const position = placePositions[place.name] || {
                lat: mapCenter.lat + (Math.random() - 0.5) * 0.05,
                lng: mapCenter.lng + (Math.random() - 0.5) * 0.05,
              };
              const isSelected = selectedPlace?.name === place.name;
              const isHovered = hoveredPlace?.name === place.name;
              
              return (
                <Marker
                  key={index}
                  position={position}
                  onClick={() => {
                    setSelectedPlace(place);
                    setHoveredPlace(null);
                  }}
                  onMouseOver={() => setHoveredPlace(place)}
                  onMouseOut={() => setHoveredPlace(null)}
                  icon={createMarkerIcon(index, isSelected, isHovered)}
                  title={place.name}
                />
              );
            })}
          </GoogleMap>
        </LoadScript>
      </div>

      {/* Draggable Bottom Panel */}
      <div 
        ref={panelRef}
        className="map-bottom-panel"
        style={{ 
          height: `${panelHeight}px`,
          transition: isDragging ? 'none' : 'height 0.3s ease',
        }}
      >
        {/* Drag Handle */}
        <div 
          className="panel-drag-handle"
          onMouseDown={handleMouseDown}
          onTouchStart={handleTouchStart}
        >
          <div className="drag-handle-bar"></div>
        </div>

        {/* Panel Header */}
        <div className="panel-header">
          <h3 className="panel-title">
            {placesWithLocation.length} {placesWithLocation.length === 1 ? 'Place' : 'Places'}
          </h3>
        </div>

        {/* Scrollable List */}
        <div className="panel-list-container">
          {placesWithLocation.length === 0 ? (
            <div className="panel-empty-state">
              <p>No places to display on the map.</p>
            </div>
          ) : (
            <div className="panel-list">
              {placesWithLocation.map((place, index) => {
              const isSelected = selectedPlace?.name === place.name;
              const isExpanded = expandedPlace?.name === place.name;
              
              return (
                <div
                  key={index}
                  ref={(el) => {
                    if (el) cardRefs.current[place.name] = el;
                  }}
                  className={`map-place-card ${isSelected ? 'selected' : ''} ${isExpanded ? 'expanded' : ''}`}
                  onMouseEnter={() => {
                    setHoveredPlace(place);
                    if (placePositions[place.name] && mapRef.current) {
                      try {
                        mapRef.current.panTo(placePositions[place.name]);
                      } catch (error) {
                        console.error('Error panning map on hover:', error);
                      }
                    }
                  }}
                  onMouseLeave={() => setHoveredPlace(null)}
                  onTouchStart={() => {
                    // On mobile, treat touch as selection
                    setSelectedPlace(place);
                    setExpandedPlace(expandedPlace?.name === place.name ? null : place);
                  }}
                  onClick={() => {
                    setSelectedPlace(place);
                    setExpandedPlace(isExpanded ? null : place);
                  }}
                >
                  {/* Card Number Badge */}
                  <div className="card-number-badge">{index + 1}</div>

                  {/* Photo */}
                  {place.photo_url && (
                    <div className="card-photo-wrapper">
                      <img 
                        src={place.photo_url} 
                        alt={place.name}
                        className="card-photo"
                      />
                    </div>
                  )}

                  {/* Card Content */}
                  <div className="card-content">
                    <div className="card-header">
                      <h4 className="card-name">{place.name}</h4>
                      <button
                        className="card-menu-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          setShowListMenu(showListMenu === place.name ? null : place.name);
                        }}
                      >
                        ⋯
                      </button>
                      {showListMenu === place.name && (
                        <div className="card-menu-popup" onClick={(e) => e.stopPropagation()}>
                          {Object.keys(savedPlaces).length > 0 ? (
                            Object.keys(savedPlaces).map((list, idx) => {
                              const inList = isInList ? isInList(list, place) : false;
                              return (
                                <button
                                  key={idx}
                                  onClick={() => {
                                    if (togglePlaceInList) {
                                      togglePlaceInList(list, place);
                                    }
                                    setShowListMenu(null);
                                  }}
                                  className={`card-list-toggle ${inList ? 'selected' : ''}`}
                                >
                                  <span className={`card-circle ${inList ? 'filled' : ''}`}>
                                    {inList ? '✓' : ''}
                                  </span>
                                  {list}
                                </button>
                              );
                            })
                          ) : (
                            <p className="card-empty-list">No lists yet</p>
                          )}
                          <button
                            className="card-add-list-btn"
                            onClick={() => {
                              if (handleAddNewList) {
                                handleAddNewList();
                              }
                              setShowListMenu(null);
                            }}
                          >
                            ➕ Add to New List
                          </button>
                        </div>
                      )}
                    </div>

                    {place.address && (
                      <p className="card-address">📍 {place.address}</p>
                    )}

                    {place.vibe_tags && place.vibe_tags.length > 0 && (
                      <div className="card-vibes">
                        {place.vibe_tags.slice(0, 3).map((tag, idx) => (
                          <span key={idx} className="card-vibe-chip">{tag}</span>
                        ))}
                      </div>
                    )}

                    {/* Expanded Content */}
                    {isExpanded && (
                      <div className="card-expanded-content">
                        {place.summary && (
                          <p className="card-summary">{place.summary}</p>
                        )}
                        {place.must_try && (
                          <p className="card-must-try">
                            <strong>
                              {place.must_try_field === "highlights" ? "✨ Highlights:" :
                               place.must_try_field === "features" ? "🎯 Features:" :
                               "🍴 Must Try:"}
                            </strong> {place.must_try}
                          </p>
                        )}
                        {place.when_to_go && (
                          <p className="card-when">
                            <strong>🕐 When to Go:</strong> {place.when_to_go}
                          </p>
                        )}
                        <div className="card-actions">
                          {place.maps_url && (
                            <a
                              href={place.maps_url}
                              target="_blank"
                              rel="noreferrer"
                              className="card-action-btn"
                              onClick={(e) => e.stopPropagation()}
                            >
                              🗺️ Maps
                            </a>
                          )}
                          {place.video_url && (
                            <a
                              href={place.video_url}
                              target="_blank"
                              rel="noreferrer"
                              className="card-action-btn"
                              onClick={(e) => e.stopPropagation()}
                            >
                              📹 TikTok
                            </a>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default MapView;
