import React, { useMemo, useRef, useEffect, useState, useCallback } from "react";
import { GoogleMap, LoadScript, Marker } from "@react-google-maps/api";
import "./MapView.css";

const GOOGLE_MAPS_API_KEY = process.env.REACT_APP_GOOGLE_MAPS_API_KEY || "";

// Bottom sheet states
const PANEL_STATES = {
  COLLAPSED: 'collapsed',   // 20-25% height
  HALF: 'half',             // 50% height
  FULL: 'full'               // 100% height
};

const PANEL_HEIGHTS = {
  [PANEL_STATES.COLLAPSED]: 0.25,  // 25% of viewport
  [PANEL_STATES.HALF]: 0.5,        // 50% of viewport
  [PANEL_STATES.FULL]: 0.95         // 95% of viewport (leaving space for status bar)
};

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
  const [mapZoom, setMapZoom] = useState(12);
  const [showListMenu, setShowListMenu] = useState(null);
  const [panelState, setPanelState] = useState(PANEL_STATES.HALF);
  const [panelHeight, setPanelHeight] = useState(() => {
    if (typeof window !== 'undefined') {
      return window.innerHeight * PANEL_HEIGHTS[PANEL_STATES.HALF];
    }
    return 400; // Default fallback
  });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStartY, setDragStartY] = useState(0);
  const [dragStartHeight, setDragStartHeight] = useState(0);
  const [dragVelocity, setDragVelocity] = useState(0);
  const [expandedPlace, setExpandedPlace] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [mapDimmed, setMapDimmed] = useState(false);
  const [mapLoaded, setMapLoaded] = useState(false);
  const panelRef = useRef(null);
  const cardRefs = useRef({});
  const mapRef = useRef(null);
  const lastDragY = useRef(0);
  const lastDragTime = useRef(0);
  const scrollTimeoutRef = useRef(null);
  const hoverTimeoutRef = useRef(null);

  // Calculate panel height from state
  const getPanelHeightFromState = (state) => {
    return window.innerHeight * PANEL_HEIGHTS[state];
  };

  // Snap to nearest panel state
  const snapToState = useCallback((currentHeight, velocity = 0) => {
    const viewportHeight = window.innerHeight;
    const collapsedHeight = viewportHeight * PANEL_HEIGHTS[PANEL_STATES.COLLAPSED];
    const halfHeight = viewportHeight * PANEL_HEIGHTS[PANEL_STATES.HALF];
    const fullHeight = viewportHeight * PANEL_HEIGHTS[PANEL_STATES.FULL];

    // If velocity is high, snap to next state
    if (Math.abs(velocity) > 0.5) {
      if (velocity < 0 && currentHeight < halfHeight) {
        // Dragging up fast - go to full
        return PANEL_STATES.FULL;
      } else if (velocity < 0 && currentHeight < fullHeight) {
        // Dragging up fast - go to full
        return PANEL_STATES.FULL;
      } else if (velocity > 0 && currentHeight > halfHeight) {
        // Dragging down fast - go to collapsed
        return PANEL_STATES.COLLAPSED;
      } else if (velocity > 0 && currentHeight > collapsedHeight) {
        // Dragging down fast - go to half
        return PANEL_STATES.HALF;
      }
    }

    // Normal snapping based on position
    const distances = {
      [PANEL_STATES.COLLAPSED]: Math.abs(currentHeight - collapsedHeight),
      [PANEL_STATES.HALF]: Math.abs(currentHeight - halfHeight),
      [PANEL_STATES.FULL]: Math.abs(currentHeight - fullHeight),
    };

    const nearestState = Object.keys(distances).reduce((a, b) => 
      distances[a] < distances[b] ? a : b
    );

    return nearestState;
  }, []);

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
      // Keep map centered on default location
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

  // Handle panel dragging with velocity detection
  const handleDragStart = (clientY) => {
    setIsDragging(true);
    setDragStartY(clientY);
    setDragStartHeight(panelHeight);
    lastDragY.current = clientY;
    lastDragTime.current = Date.now();
    setDragVelocity(0);
  };

  const handleDragMove = useCallback((clientY) => {
    const deltaY = dragStartY - clientY;
    const newHeight = Math.max(
      window.innerHeight * PANEL_HEIGHTS[PANEL_STATES.COLLAPSED],
      Math.min(window.innerHeight * PANEL_HEIGHTS[PANEL_STATES.FULL], dragStartHeight + deltaY)
    );
    setPanelHeight(newHeight);

    // Calculate velocity
    const now = Date.now();
    const timeDelta = now - lastDragTime.current;
    if (timeDelta > 0) {
      const yDelta = clientY - lastDragY.current;
      const velocity = yDelta / timeDelta;
      setDragVelocity(velocity);
    }
    lastDragY.current = clientY;
    lastDragTime.current = now;
  }, [dragStartY, dragStartHeight]);

  const handleDragEnd = useCallback(() => {
    setIsDragging(false);
    const snappedState = snapToState(panelHeight, dragVelocity);
    setPanelState(snappedState);
    const targetHeight = getPanelHeightFromState(snappedState);
    setPanelHeight(targetHeight);
    setMapDimmed(snappedState === PANEL_STATES.FULL);
    setDragVelocity(0);
  }, [panelHeight, dragVelocity, snapToState]);

  const handleMouseDown = (e) => {
    handleDragStart(e.clientY);
    e.preventDefault();
    e.stopPropagation();
  };

  const handleTouchStart = (e) => {
    handleDragStart(e.touches[0].clientY);
    e.preventDefault();
    e.stopPropagation();
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
  }, [isDragging, handleDragMove, handleDragEnd]);

  // Debounced hover handler for list items
  const handleCardHover = useCallback((place) => {
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
    }
    
    hoverTimeoutRef.current = setTimeout(() => {
      setHoveredPlace(place);
      // Don't auto-center map on hover - just highlight pin
    }, 100);
  }, []);

  const handleCardLeave = useCallback(() => {
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current);
    }
    setHoveredPlace(null);
  }, []);

  // Smooth map centering when place is selected
  const centerMapOnPlace = useCallback((place, position) => {
    if (!mapRef.current || !position) return;
    
    try {
      // Smooth pan with easing
      mapRef.current.panTo({
        lat: position.lat,
        lng: position.lng
      });
    } catch (error) {
      console.error('Error panning map:', error);
    }
  }, []);

  // Center map on place when selected (with smooth animation)
  useEffect(() => {
    if (selectedPlace && placePositions[selectedPlace.name] && mapRef.current) {
      const position = placePositions[selectedPlace.name];
      centerMapOnPlace(selectedPlace, position);
      
      // Smooth scroll to card
      setTimeout(() => {
        const cardElement = cardRefs.current[selectedPlace.name];
        if (cardElement) {
          cardElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }, 150);
    }
  }, [selectedPlace, placePositions, centerMapOnPlace]);
  
  // Center map on all places when they're loaded - show bounds/area
  useEffect(() => {
    if (mapLoaded && mapRef.current && Object.keys(placePositions).length > 0) {
      const positionsArray = Object.values(placePositions);
      
      if (positionsArray.length === 1) {
        // Single place - center on it with zoom 15
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
        // Multiple places - fit bounds to show all places
        const lats = positionsArray.map(pos => pos.lat);
        const lngs = positionsArray.map(pos => pos.lng);
        const minLat = Math.min(...lats);
        const maxLat = Math.max(...lats);
        const minLng = Math.min(...lngs);
        const maxLng = Math.max(...lngs);
        
        const bounds = {
          north: maxLat,
          south: minLat,
          east: maxLng,
          west: minLng
        };
        
        const avgLat = (minLat + maxLat) / 2;
        const avgLng = (minLng + maxLng) / 2;
        
        // Calculate appropriate zoom
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
        
        // Update state
        setMapCenter({ lat: avgLat, lng: avgLng });
        setMapZoom(zoom);
        
        // Fit bounds to show all places
        setTimeout(() => {
          if (mapRef.current && window.google && window.google.maps) {
            try {
              const googleBounds = new window.google.maps.LatLngBounds();
              positionsArray.forEach(pos => {
                googleBounds.extend(new window.google.maps.LatLng(pos.lat, pos.lng));
              });
              
              // Add padding to bounds
              mapRef.current.fitBounds(googleBounds, {
                top: 50,
                right: 50,
                bottom: panelHeight + 50,
                left: 50
              });
            } catch (error) {
              console.error('Error fitting bounds:', error);
              // Fallback to center and zoom
              if (mapRef.current.panTo) {
                mapRef.current.panTo({ lat: avgLat, lng: avgLng });
                mapRef.current.setZoom(zoom);
              }
            }
          }
        }, 500);
      }
    } else if (mapLoaded && mapRef.current && Object.keys(placePositions).length === 0) {
      // No places - center on default location
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
  }, [mapLoaded, placePositions]);

  // Update map dimming based on panel state
  useEffect(() => {
    setMapDimmed(panelState === PANEL_STATES.FULL);
  }, [panelState]);

  // Handle window resize
  useEffect(() => {
    const handleResize = () => {
      if (!isDragging) {
        const newHeight = getPanelHeightFromState(panelState);
        setPanelHeight(newHeight);
      }
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [panelState, isDragging]);

  const mapHeight = typeof window !== 'undefined' && panelHeight > 0
    ? `calc(100vh - ${panelHeight}px)` 
    : '60vh';

  const mapOptions = {
    zoom: mapZoom,
    center: mapCenter,
    mapTypeControl: false,
    streetViewControl: false,
    fullscreenControl: true,
    backgroundColor: '#0a0a0f',
    gestureHandling: 'greedy', // Allow map gestures even when over other elements
    disableDefaultUI: false,
    zoomControl: true,
    zoomControlOptions: {
      position: window.google?.maps?.ControlPosition?.RIGHT_BOTTOM || 11
    },
    // Ensure map always renders
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
  };

  // Custom marker icon with smooth animations
  const createMarkerIcon = useCallback((index, isSelected, isHovered) => {
    const baseSize = 32;
    const size = isSelected ? 44 : isHovered ? 38 : baseSize;
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
      
      {/* Map Section with dimming overlay */}
      <div 
        className={`map-section ${mapDimmed ? 'dimmed' : ''}`} 
        style={{ height: mapHeight, minHeight: '300px' }}
      >
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
              minHeight: "300px",
              flex: 1,
            }}
          options={mapOptions}
          center={mapCenter}
          zoom={mapZoom}
            onLoad={(map) => {
              mapRef.current = map;
              setMapLoaded(true);
              setIsLoading(false);
              console.log('Map loaded successfully');
              
              // Always ensure map is visible - center on default if no places yet
              if (Object.keys(placePositions).length === 0) {
                // Center on default location (NYC) if no places geocoded yet
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
                // Center map on places once loaded
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
              console.error('Map error:', error);
              setIsLoading(false);
            }}
            onDragStart={() => {
              // Prevent list scrolling when dragging map
            }}
            onIdle={() => {
              // Map is ready and rendered
              setIsLoading(false);
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
                  key={`${place.name}-${index}`}
                position={position}
                  onClick={() => {
                    setSelectedPlace(place);
                    setHoveredPlace(null);
                  }}
                  onMouseOver={() => setHoveredPlace(place)}
                  onMouseOut={() => setHoveredPlace(null)}
                  icon={createMarkerIcon(index, isSelected, isHovered)}
                title={place.name}
                  zIndex={isSelected ? 1000 : isHovered ? 500 : index}
                  animation={isSelected ? window.google?.maps?.Animation?.BOUNCE : null}
              />
            );
          })}
          </GoogleMap>
        </LoadScript>
      </div>

      {/* Draggable Bottom Panel */}
      <div 
        ref={panelRef}
        className={`map-bottom-panel ${panelState}`}
          style={{ 
            height: `${panelHeight}px`,
            transition: isDragging ? 'none' : 'height 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94)',
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
          {panelState === PANEL_STATES.COLLAPSED && (
            <div className="panel-preview">
              {placesWithLocation.slice(0, 3).map((place, idx) => (
                <span key={idx} className="preview-badge">{idx + 1}</span>
              ))}
              {placesWithLocation.length > 3 && (
                <span className="preview-more">+{placesWithLocation.length - 3}</span>
                )}
              </div>
          )}
        </div>

        {/* Scrollable List */}
        <div className="panel-list-container" onWheel={(e) => e.stopPropagation()}>
          {isLoading && Object.keys(placePositions).length === 0 ? (
            <div className="panel-list">
              {[1, 2, 3].map((i) => (
                <div key={i} className="map-place-card skeleton">
                  <div className="skeleton-photo"></div>
                  <div className="skeleton-content">
                    <div className="skeleton-line" style={{ width: '60%' }}></div>
                    <div className="skeleton-line" style={{ width: '80%' }}></div>
                    <div className="skeleton-line" style={{ width: '40%' }}></div>
                  </div>
                </div>
              ))}
            </div>
          ) : placesWithLocation.length === 0 ? (
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
                    key={`${place.name}-${index}`}
                    ref={(el) => {
                      if (el) cardRefs.current[place.name] = el;
                    }}
                    className={`map-place-card ${isSelected ? 'selected' : ''} ${isExpanded ? 'expanded' : ''}`}
                    onMouseEnter={() => handleCardHover(place)}
                    onMouseLeave={handleCardLeave}
                    onTouchStart={(e) => {
                      e.stopPropagation();
                      setSelectedPlace(place);
                      setExpandedPlace(isExpanded ? null : place);
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
                          loading="lazy"
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
