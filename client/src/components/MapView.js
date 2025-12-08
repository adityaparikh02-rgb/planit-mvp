import React, { useMemo, useRef, useEffect, useState, useCallback, forwardRef, useImperativeHandle } from "react";
import { GoogleMap, LoadScript, Marker, InfoWindow } from "@react-google-maps/api";
import "./MapView.css";

const GOOGLE_MAPS_API_KEY = process.env.REACT_APP_GOOGLE_MAPS_API_KEY || "";

// Move libraries array outside component to prevent LoadScript reloads
const GOOGLE_MAPS_LIBRARIES = ['places', 'geometry'];

const MapView = forwardRef(({ places, onClose, selectedPlaceIndex, userLocation, onMarkerClick }, ref) => {
  // Default center (NYC)
  const defaultCenter = { lat: 40.7128, lng: -73.9352 };

  // ALL HOOKS MUST BE CALLED FIRST - before any conditional returns
  const [placePositions, setPlacePositions] = useState({});
  const [mapCenter, setMapCenter] = useState(defaultCenter);
  const [mapZoom, setMapZoom] = useState(12);
  const [mapError, setMapError] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [loadScriptReady, setLoadScriptReady] = useState(false);
  const [selectedMarker, setSelectedMarker] = useState(null);
  const mapRef = useRef(null);
  const geocodingInProgressRef = useRef(false); // Prevent multiple geocoding runs
  const geocodedPlaceNamesRef = useRef(new Set()); // Track which places we've geocoded

  // Filter places that have addresses or can be geocoded
  const placesWithLocation = useMemo(() => {
    return places.filter(p => p.address || p.maps_url || p.name);
  }, [places]);

  // Debug: Log API key status
  useEffect(() => {
    console.log('🗺️ MapView Debug:');
    console.log('  - API Key exists:', !!GOOGLE_MAPS_API_KEY);
    console.log('  - API Key (first 20 chars):', GOOGLE_MAPS_API_KEY?.substring(0, 20) + '...' || 'MISSING');
    console.log('  - API Key length:', GOOGLE_MAPS_API_KEY?.length || 0);
    console.log('  - Places count:', placesWithLocation.length);
    console.log('  - Places:', placesWithLocation.map(p => ({name: p.name, address: p.address})));
  }, [placesWithLocation]);

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
      geocodedPlaceNamesRef.current.clear();
      return;
    }

    // Check if we already have positions for all places - prevent re-geocoding
    const placeNames = placesWithLocation.map(p => p.name);
    const placeNamesSet = new Set(placeNames);
    const existingPositions = Object.keys(placePositions);
    const existingSet = new Set(existingPositions);
    
    // Check if all current places are already geocoded
    const allPlacesGeocoded = placeNames.every(name => existingSet.has(name));
    
    // Also check if geocoding is already in progress
    if (geocodingInProgressRef.current) {
      console.log('⏳ Geocoding already in progress, skipping');
      return;
    }
    
    // Only geocode if we don't have positions for all places
    if (allPlacesGeocoded && placeNames.length === existingPositions.length) {
      console.log('✅ All places already geocoded, skipping');
      return;
    }

    // Prevent concurrent geocoding runs
    geocodingInProgressRef.current = true;
    setIsLoading(true);
    setMapError(null);

    const geocodePlace = async (place) => {
      // Skip if we already have a position for this place
      if (placePositions[place.name]) {
        return placePositions[place.name];
      }

      const query = place.address || `${place.name} NYC`;
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000); // Reduced to 5 second timeout
        
        const response = await fetch(
          `https://maps.googleapis.com/maps/api/geocode/json?address=${encodeURIComponent(query)}&key=${GOOGLE_MAPS_API_KEY}`,
          { signal: controller.signal }
        );
        
        clearTimeout(timeoutId);
        
        if (!response.ok) {
          throw new Error(`Geocoding failed: ${response.status}`);
        }
        
        const data = await response.json();
        if (data.results && data.results.length > 0) {
          const location = data.results[0].geometry.location;
          return { lat: location.lat, lng: location.lng };
        }
      } catch (error) {
        if (error.name === 'AbortError') {
          console.warn(`Geocoding timeout for ${place.name} - using fallback`);
        } else {
          console.warn(`Failed to geocode ${place.name}:`, error.message);
        }
        // Don't re-throw - use fallback instead
      }
      
      // Fallback: use address if available, otherwise random position near NYC
      if (place.address) {
        // Try to extract lat/lng from maps_url if available
        const mapsUrlMatch = place.maps_url?.match(/@(-?\d+\.\d+),(-?\d+\.\d+)/);
        if (mapsUrlMatch) {
          return {
            lat: parseFloat(mapsUrlMatch[1]),
            lng: parseFloat(mapsUrlMatch[2])
          };
        }
      }
      
      // Final fallback: use a stable position based on place name hash (not random)
      const nameHash = place.name.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);
      return {
        lat: defaultCenter.lat + ((nameHash % 100) / 1000 - 0.05),
        lng: defaultCenter.lng + (((nameHash * 7) % 100) / 1000 - 0.05),
      };
    };

    const geocodeAll = async () => {
      try {
        const positions = { ...placePositions }; // Start with existing positions
        
        // Only geocode places we don't have positions for
        const placesToGeocode = placesWithLocation.filter(p => {
          const alreadyGeocoded = geocodedPlaceNamesRef.current.has(p.name) || positions[p.name];
          return !alreadyGeocoded;
        });
        
        if (placesToGeocode.length === 0) {
          console.log('✅ No new places to geocode');
          geocodingInProgressRef.current = false;
          setIsLoading(false);
          return;
        }
        
        console.log(`📍 Geocoding ${placesToGeocode.length} new place(s)...`);
        
        // Geocode places with delay between requests to avoid ERR_INSUFFICIENT_RESOURCES
        for (let i = 0; i < placesToGeocode.length; i++) {
          const place = placesToGeocode[i];
          
          // Add delay between requests (except first one)
          if (i > 0) {
            await new Promise(resolve => setTimeout(resolve, 300)); // 300ms delay
          }
          
          try {
            positions[place.name] = await geocodePlace(place);
            geocodedPlaceNamesRef.current.add(place.name); // Mark as geocoded
          } catch (error) {
            console.warn(`Failed to geocode ${place.name}, using fallback`);
            // Use stable fallback position
            const nameHash = place.name.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);
            positions[place.name] = {
              lat: defaultCenter.lat + ((nameHash % 100) / 1000 - 0.05),
              lng: defaultCenter.lng + (((nameHash * 7) % 100) / 1000 - 0.05),
            };
            geocodedPlaceNamesRef.current.add(place.name); // Mark as geocoded even with fallback
          }
        }
        
        setPlacePositions(positions);

        const positionsArray = Object.values(positions);
        if (positionsArray.length > 0) {
          const avgLat = positionsArray.reduce((sum, pos) => sum + pos.lat, 0) / positionsArray.length;
          const avgLng = positionsArray.reduce((sum, pos) => sum + pos.lng, 0) / positionsArray.length;

          setMapCenter({ lat: avgLat, lng: avgLng });
          setMapZoom(13);
        }
        geocodingInProgressRef.current = false;
        setIsLoading(false);
      } catch (error) {
        console.error('Geocoding error:', error);
        setMapError('Failed to geocode places');
        geocodingInProgressRef.current = false;
        setIsLoading(false);
      }
    };

    geocodeAll();
  }, [placesWithLocation, GOOGLE_MAPS_API_KEY, defaultCenter]); // Removed placePositions from dependencies to prevent loop

  // Simple map options - ensure map is fully interactive
  const mapOptions = useMemo(() => ({
    zoom: mapZoom,
    center: mapCenter,
    zoomControl: true,
    mapTypeControl: true,
    streetViewControl: false,
    fullscreenControl: true,
    panControl: true, // Enable pan controls
    scrollwheel: true, // Enable mouse wheel zoom
    disableDoubleClickZoom: false, // Enable double-click zoom
    draggable: true, // Enable dragging/panning
    keyboardShortcuts: true, // Enable keyboard shortcuts
    gestureHandling: 'auto', // Enable touch gestures
  }), [mapZoom, mapCenter]);

  // Check if Google Maps is already loaded (from previous render or other component)
  useEffect(() => {
    if (window.google && window.google.maps) {
      console.log('✅ Google Maps already loaded, skipping LoadScript wait');
      setLoadScriptReady(true);
    }
  }, []);

  // Debug: Log when component renders (must be before conditional returns)
  useEffect(() => {
    console.log('🗺️ MapView render:', {
      placesCount: places.length,
      placesWithLocationCount: placesWithLocation.length,
      placePositionsCount: Object.keys(placePositions).length,
      loadScriptReady,
      hasApiKey: !!GOOGLE_MAPS_API_KEY,
      mapCenter,
      mapZoom,
      googleAlreadyLoaded: !!(window.google && window.google.maps)
    });
  }, [places, placesWithLocation, placePositions, loadScriptReady, mapCenter, mapZoom]);

  // Function to fit map to all markers
  const fitMapToMarkers = useCallback(() => {
    if (!mapRef.current) {
      console.log('⏳ Map ref not ready yet');
      return;
    }
    
    if (Object.keys(placePositions).length === 0) {
      console.log('⏳ No place positions yet');
      return;
    }
    
    if (!window.google || !window.google.maps) {
      console.log('⏳ Google Maps API not ready');
      return;
    }
    
    try {
      const positionsArray = Object.values(placePositions);
      console.log(`📍 Fitting map to ${positionsArray.length} markers`);
      
      const bounds = new window.google.maps.LatLngBounds();
      positionsArray.forEach(pos => {
        if (pos && typeof pos.lat === 'number' && typeof pos.lng === 'number') {
          bounds.extend(new window.google.maps.LatLng(pos.lat, pos.lng));
        }
      });
      
      if (bounds.isEmpty()) {
        console.log('⚠️ Bounds are empty, cannot fit map');
        return;
      }
      
      mapRef.current.fitBounds(bounds, { padding: 50 });
      console.log('✅ Map fitted to show all markers');
    } catch (error) {
      console.error('❌ Error fitting map to markers:', error);
    }
  }, [placePositions]);

  // Expose methods to parent component via ref
  useImperativeHandle(ref, () => ({
    zoomToPlace: (index) => {
      if (mapRef.current && placesWithLocation[index]) {
        const place = placesWithLocation[index];
        const position = placePositions[place.name];
        if (position) {
          mapRef.current.panTo(position);
          mapRef.current.setZoom(16);
          setSelectedMarker(index);
        }
      }
    },
    fitAllMarkers: () => {
      fitMapToMarkers();
    }
  }), [placesWithLocation, placePositions, fitMapToMarkers]);

  // Effect to fit map when placePositions are ready AND map is loaded
  useEffect(() => {
    if (!loadScriptReady) {
      return; // Wait for LoadScript
    }
    
    if (Object.keys(placePositions).length === 0) {
      return; // No positions yet
    }
    
    if (placesWithLocation.length === 0) {
      return; // No places to show
    }
    
    // Only fit if we have positions for all places AND map is ready
    const placeNames = placesWithLocation.map(p => p.name);
    const hasAllPositions = placeNames.every(name => placePositions[name]);
    
    if (!hasAllPositions) {
      console.log('⏳ Waiting for all positions. Have:', Object.keys(placePositions).length, 'of', placeNames.length);
      return;
    }
    
    // If map is ready, fit immediately
    if (mapRef.current) {
      console.log('✅ All positions ready and map loaded, fitting map to markers');
      console.log('Positions:', placePositions);
      // Use a small delay to ensure map is fully initialized
      const timeoutId = setTimeout(() => {
        if (mapRef.current) {
          fitMapToMarkers();
        }
      }, 500);
      
      return () => clearTimeout(timeoutId);
    } else {
      // Map not ready yet - set up a retry mechanism
      console.log('⏳ Positions ready but map not loaded yet - will retry fitting');
      let retryCount = 0;
      const maxRetries = 10; // Try for up to 5 seconds (10 * 500ms)
      
      const retryInterval = setInterval(() => {
        retryCount++;
        if (mapRef.current) {
          console.log('✅ Map now loaded, fitting to markers');
          clearInterval(retryInterval);
          setTimeout(() => {
            if (mapRef.current) {
              fitMapToMarkers();
            }
          }, 300);
        } else if (retryCount >= maxRetries) {
          console.log('⚠️ Map not loaded after retries, giving up');
          clearInterval(retryInterval);
        }
      }, 500);
      
      return () => clearInterval(retryInterval);
    }
  }, [placePositions, placesWithLocation, loadScriptReady, fitMapToMarkers]); // Fit map when positions change

  // NOW we can do conditional returns - all hooks are called above
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
      <div className="map-section-simple" style={{ position: 'relative' }}>
        {/* Show all markers button */}
        {loadScriptReady && Object.keys(placePositions).length > 0 && (
          <button
            onClick={fitMapToMarkers}
            style={{
              position: 'absolute',
              top: '10px',
              right: '10px',
              zIndex: 1000,
              padding: '8px 16px',
              backgroundColor: '#4F46E5',
              color: '#fff',
              border: 'none',
              borderRadius: '8px',
              cursor: 'pointer',
              fontSize: '14px',
              fontWeight: '600',
              boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
            }}
            onMouseOver={(e) => e.target.style.backgroundColor = '#4338CA'}
            onMouseOut={(e) => e.target.style.backgroundColor = '#4F46E5'}
          >
            🔍 Zoom to Fit
          </button>
        )}
        <LoadScript
          googleMapsApiKey={GOOGLE_MAPS_API_KEY}
          libraries={GOOGLE_MAPS_LIBRARIES}
          loadingElement={
            <div style={{
              width: '100%',
              height: '100%',
              minHeight: '500px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: '#0a0a0f',
              color: '#fff'
            }}>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: '24px', marginBottom: '12px' }}>🗺️</div>
                <div>Loading map...</div>
              </div>
            </div>
          }
          onError={(error) => {
            console.error('❌ LoadScript error:', error);
            console.error('Error details:', {
              message: error?.message,
              code: error?.code,
              toString: error?.toString(),
              error
            });
            setMapError(`Failed to load Google Maps: ${error?.message || error?.toString() || 'Unknown error'}`);
          }}
          onLoad={() => {
            console.log('✅ LoadScript loaded successfully');
            console.log('window.google:', window.google);
            console.log('window.google.maps:', window.google?.maps);
            setLoadScriptReady(true);
          }}
        >
          {loadScriptReady && window.google && window.google.maps && (
            <GoogleMap
              mapContainerStyle={{
                width: "100%",
                height: "500px",
                position: "relative"
              }}
              options={mapOptions}
              center={mapCenter}
              zoom={mapZoom}
              onLoad={(map) => {
                console.log('✅ GoogleMap onLoad called');
                console.log('Map instance:', map);
                console.log('Places to show:', placesWithLocation.length);
                console.log('Place positions:', placePositions);
                mapRef.current = map;
                
                // Always try to fit the map when it loads
                // Use a function that checks current state
                const tryFitMap = () => {
                  if (!mapRef.current) return;
                  
                  const currentPositions = placePositions;
                  if (Object.keys(currentPositions).length === 0) {
                    console.log('⏳ Map loaded but positions not ready yet');
                    return;
                  }
                  
                  const placeNames = placesWithLocation.map(p => p.name);
                  const hasAllPositions = placeNames.every(name => currentPositions[name]);
                  
                  if (hasAllPositions) {
                    console.log('✅ Map loaded and positions ready, fitting immediately');
                    setTimeout(() => {
                      if (mapRef.current) {
                        fitMapToMarkers();
                      }
                    }, 300);
                  } else {
                    console.log('⏳ Map loaded but waiting for all positions. Have:', Object.keys(currentPositions).length, 'of', placeNames.length);
                  }
                };
                
                // Try immediately
                tryFitMap();
                
                // Also set up a check in case positions arrive after map loads
                // This will be handled by the useEffect, but we can also check here
                setTimeout(() => {
                  tryFitMap();
                }, 1000);
              }}
              onError={(error) => {
                console.error('❌ GoogleMap error:', error);
                console.error('Error details:', error);
                setMapError(`Map error: ${error?.message || 'Unknown error'}`);
              }}
            >
              {/* User location marker */}
              {userLocation && (
                <Marker
                  position={userLocation}
                  icon={{
                    path: window.google.maps.SymbolPath.CIRCLE,
                    scale: 8,
                    fillColor: '#4285F4',
                    fillOpacity: 1,
                    strokeColor: '#ffffff',
                    strokeWeight: 2,
                  }}
                  title="Your Location"
                />
              )}

              {/* Place markers */}
              {placesWithLocation.map((place, index) => {
                const position = placePositions[place.name] || {
                  lat: mapCenter.lat + (Math.random() - 0.5) * 0.05,
                  lng: mapCenter.lng + (Math.random() - 0.5) * 0.05,
                };

                console.log(`📍 Marker ${index + 1}: ${place.name} at`, position);

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
                    onClick={() => {
                      setSelectedMarker(index);
                      if (onMarkerClick) {
                        onMarkerClick(index);
                      }
                    }}
                  />
                );
              })}

              {/* Info Window for selected marker */}
              {selectedMarker !== null && placesWithLocation[selectedMarker] && placePositions[placesWithLocation[selectedMarker].name] && (
                <InfoWindow
                  position={placePositions[placesWithLocation[selectedMarker].name]}
                  onCloseClick={() => setSelectedMarker(null)}
                >
                  <div style={{ maxWidth: '200px' }}>
                    {placesWithLocation[selectedMarker].photo_url && (
                      <img
                        src={placesWithLocation[selectedMarker].photo_url}
                        alt={placesWithLocation[selectedMarker].name}
                        style={{ width: '100%', height: '120px', objectFit: 'cover', borderRadius: '8px', marginBottom: '8px' }}
                      />
                    )}
                    <h4 style={{ margin: '0 0 8px 0', fontSize: '14px', fontWeight: '600' }}>
                      {placesWithLocation[selectedMarker].name}
                    </h4>
                    {placesWithLocation[selectedMarker].summary && (
                      <p style={{ margin: 0, fontSize: '12px', color: '#666' }}>
                        {placesWithLocation[selectedMarker].summary.substring(0, 100)}
                        {placesWithLocation[selectedMarker].summary.length > 100 ? '...' : ''}
                      </p>
                    )}
                  </div>
                </InfoWindow>
              )}
            </GoogleMap>
          )}
        </LoadScript>
        {loadScriptReady && (!window.google || !window.google.maps) && (
          <div style={{ 
            width: '100%',
            height: '500px',
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center', 
            background: '#0a0a0f',
            color: '#f87171'
          }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '24px', marginBottom: '12px' }}>⚠️</div>
              <div>Google Maps API not available</div>
              <div style={{ fontSize: '12px', marginTop: '10px', opacity: 0.7 }}>
                Check console for details
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
});

export default MapView;
