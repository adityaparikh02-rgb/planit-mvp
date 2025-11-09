import React, { useEffect, useRef } from "react";

const categoryIcon = (category = "") => {
  const lower = category.toLowerCase();
  if (lower.includes("bar") || lower.includes("club") || lower.includes("lounge") || lower.includes("pub")) return "🍸";
  if (lower.includes("restaurant") || lower.includes("cafe") || lower.includes("diner")) return "🍽";
  if (lower.includes("hotel") || lower.includes("inn") || lower.includes("stay")) return "🏨";
  if (lower.includes("park") || lower.includes("beach") || lower.includes("garden")) return "🌴";
  if (lower.includes("museum") || lower.includes("gallery")) return "🖼";
  return "📍";
};

function MapComponent({ lat, lng, category, name }) {
  const mapRef = useRef(null);

  useEffect(() => {
    if (!window.google || !window.google.maps) return;

    const map = new window.google.maps.Map(mapRef.current, {
      center: { lat: Number(lat), lng: Number(lng) },
      zoom: 15,
      mapId: "PlanItMapStyle",
    });

    const marker = new window.google.maps.marker.AdvancedMarkerElement({
      position: { lat: Number(lat), lng: Number(lng) },
      content: (() => { 
        const div = document.createElement("div");
        div.style.fontSize = "22px";
        div.style.textAlign = "center";
        div.textContent = categoryIcon(category);
        return div;
      })(),
      title: name || "Location",
      map,
    });

    return () => marker.map = null;
  }, [lat, lng, category, name]);

  return (
    <div
      ref={mapRef}
      style={{
        width: "100%",
        height: "200px",
        borderRadius: "12px",
        marginTop: "10px",
      }}
    />
  );
}

export default MapComponent;
