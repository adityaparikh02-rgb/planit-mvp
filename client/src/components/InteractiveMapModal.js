import React, { useState, useEffect, useRef } from 'react';
import MapView from './MapView';
import { X, MapPin } from 'lucide-react';
import './InteractiveMapModal.css';

const InteractiveMapModal = ({
  places,
  onClose,
  selectedPlaceIndex,
  setSelectedPlaceIndex
}) => {
  const [userLocation, setUserLocation] = useState(null);
  const [showPlacesList, setShowPlacesList] = useState(true);
  const mapRef = useRef(null);

  // Get user's current location
  useEffect(() => {
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          setUserLocation({
            lat: position.coords.latitude,
            lng: position.coords.longitude
          });
        },
        (error) => console.log('Location access denied:', error)
      );
    }
  }, []);

  // Handle venue card click - zoom to pin
  const handleVenueClick = (index) => {
    setSelectedPlaceIndex(index);
    if (mapRef.current) {
      mapRef.current.zoomToPlace(index);
    }
  };

  return (
    <div className="map-modal-overlay">
      <div className="map-modal-container">
        {/* Header */}
        <div className="map-modal-header">
          <h2>Map View</h2>
          <button onClick={onClose} className="close-button">
            <X size={24} />
          </button>
        </div>

        {/* Map + Places List */}
        <div className="map-modal-content">
          {/* Map Section */}
          <div className="map-section">
            <MapView
              ref={mapRef}
              places={places}
              selectedPlaceIndex={selectedPlaceIndex}
              userLocation={userLocation}
              onMarkerClick={setSelectedPlaceIndex}
            />
          </div>

          {/* Places List Sidebar (Desktop) / Bottom Sheet (Mobile) */}
          {showPlacesList && (
            <div className="places-list-sidebar">
              <div className="places-list-header">
                <h3>Venues ({places.length})</h3>
                <button
                  onClick={() => setShowPlacesList(false)}
                  className="hide-list-button"
                >
                  Hide
                </button>
              </div>

              <div className="places-list-scroll">
                {places.map((place, index) => (
                  <div
                    key={index}
                    className={`place-card-mini ${
                      selectedPlaceIndex === index ? 'selected' : ''
                    }`}
                    onClick={() => handleVenueClick(index)}
                  >
                    {place.photo_url && (
                      <img
                        src={place.photo_url}
                        alt={place.name}
                        className="place-card-mini-image"
                      />
                    )}
                    <div className="place-card-mini-info">
                      <h4>{place.name}</h4>
                      <p className="place-neighborhood">
                        <MapPin size={14} /> {place.neighborhood}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Toggle List Button (when hidden) */}
          {!showPlacesList && (
            <button
              onClick={() => setShowPlacesList(true)}
              className="show-list-button"
            >
              Show Venues
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default InteractiveMapModal;
