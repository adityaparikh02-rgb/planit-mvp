import React, { useState, useEffect } from "react";
import { Grid } from "lucide-react";
import "./App.css";
import PlanItLogo from "./components/PlanItLogo";
import MapView from "./components/MapView";

const API_BASE = process.env.REACT_APP_API_URL || "https://planit-backend-fbm5.onrender.com";

// Log the API base URL for debugging
console.log("🔧 API_BASE:", API_BASE);
console.log("🔧 REACT_APP_API_URL env:", process.env.REACT_APP_API_URL);

function App() {
  const [savedPlaces, setSavedPlaces] = useState({});
  const [activeMenu, setActiveMenu] = useState(null);
  const [videoUrl, setVideoUrl] = useState("");
  const [loadingStep, setLoadingStep] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [history, setHistory] = useState([]);
  const [cachedResults, setCachedResults] = useState({});
  const [viewMode, setViewMode] = useState("list");
  const [expandedIndex, setExpandedIndex] = useState(null);
  const [activeTab, setActiveTab] = useState("home");
  const [abortController, setAbortController] = useState(null);
  const [viewingHistory, setViewingHistory] = useState(false);
  const [selectedList, setSelectedList] = useState(null); // For saved list detail view
  const [showListMap, setShowListMap] = useState(false); // For map view in list detail

  // Handle share target / deep linking
  useEffect(() => {
    // Check URL params for shared TikTok URL
    const urlParams = new URLSearchParams(window.location.search);
    const sharedUrl = urlParams.get("url") || urlParams.get("tiktok_url") || urlParams.get("text");
    if (sharedUrl && sharedUrl.includes("tiktok.com")) {
      setVideoUrl(sharedUrl);
      // Auto-extract
      setTimeout(() => {
        handleExtract(sharedUrl);
      }, 500);
    }

    // Handle Android share intent
    if (window.Android && window.Android.getSharedUrl) {
      const sharedUrl = window.Android.getSharedUrl();
      if (sharedUrl && sharedUrl.includes("tiktok.com")) {
        setVideoUrl(sharedUrl);
        setTimeout(() => {
          handleExtract(sharedUrl);
        }, 500);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load saved places and history from API + localStorage
  useEffect(() => {
    // Always load from localStorage first (for persistence across sessions)
    const localHistory = localStorage.getItem("planit_history");
    const localCachedResults = localStorage.getItem("planit_cached_results");
    
    if (localHistory) {
      try {
        const parsed = JSON.parse(localHistory);
        setHistory(parsed);
      } catch (e) {
        console.error("Failed to parse localStorage history:", e);
      }
    }
    
    if (localCachedResults) {
      try {
        const parsed = JSON.parse(localCachedResults);
        setCachedResults(parsed);
      } catch (e) {
        console.error("Failed to parse localStorage cached results:", e);
      }
    }

    // Load saved places from localStorage
    const localSavedPlaces = localStorage.getItem("planit_saved_places");
    if (localSavedPlaces) {
      try {
        const parsed = JSON.parse(localSavedPlaces);
        setSavedPlaces(parsed);
      } catch (e) {
        console.error("Failed to parse localStorage saved places:", e);
      }
    }
  }, []);

  // ===== Extract TikTok =====
  const handleExtract = async (urlToUse = null, isFromHistory = false) => {
    const url = urlToUse || videoUrl;
    if (!url) return setError("Enter a TikTok URL");

    setError("");
    setResult(null);
    if (!isFromHistory) {
      setViewingHistory(false); // Reset history view when starting new extraction
    }
    setLoadingStep("Analyzing TikTok...");

    if (cachedResults[url]) {
      setResult(cachedResults[url]);
      setLoadingStep("");
      if (!isFromHistory) {
        setViewingHistory(false); // Not viewing history when extracting new
      }
      return;
    }

    try {
      console.log(`🌐 Calling API: ${API_BASE}/api/extract`);
      
      // Add timeout for long-running requests (10 minutes for videos with OCR)
      const controller = new AbortController();
      setAbortController(controller); // Store controller for cancel button
      const timeoutId = setTimeout(() => controller.abort(), 10 * 60 * 1000); // 10 minutes
      
      const res = await fetch(`${API_BASE}/api/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_url: url }),
        signal: controller.signal,
      });
      
      clearTimeout(timeoutId);
      setAbortController(null); // Clear controller after request completes
      
      console.log(`📡 Response status: ${res.status}`);
      
      // Try to parse JSON, but handle non-JSON errors
      let data;
      try {
        data = await res.json();
        console.log("📦 Response data:", data);
      } catch (parseError) {
        const text = await res.text();
        console.error("❌ Failed to parse JSON:", text);
        // Check if it's a timeout
        if (controller.signal.aborted) {
          throw new Error("Request timed out. Video processing is taking too long. The video might be too large or the backend is overloaded. Try a shorter video or wait a moment and try again.");
        }
        throw new Error(`Backend returned invalid response (${res.status}): ${text.substring(0, 200)}`);
      }
      
      if (!res.ok) {
        // Show actual error message from backend
        const errorMsg = data.error || data.message || `Backend error (${res.status})`;
        throw new Error(errorMsg);
      }
      
      // Check if there's an error in the response
      if (data.error) {
        throw new Error(data.error);
      }
      
      // Check if we got valid data
      if (!data.places_extracted) {
        console.warn("⚠️ No places_extracted in response:", data);
        // Still show the result, but with empty places
        data.places_extracted = [];
      }
      
      console.log(`✅ Found ${data.places_extracted?.length || 0} places`);
      console.log("📋 Full response structure:", {
        has_places: !!data.places_extracted,
        places_count: data.places_extracted?.length || 0,
        has_summary: !!data.context_summary,
        summary: data.context_summary,
        video_url: data.video_url
      });

      // attach TikTok URL to every place
      const uniquePlaces = [
        ...new Map(
          (data.places_extracted || []).map((p) => [
            p.name,
            { ...p, video_url: data.video_url },
          ])
        ).values(),
      ];
      const cleanData = { ...data, places_extracted: uniquePlaces };

      console.log("💾 Setting result:", cleanData);
      setCachedResults((prev) => {
        const updated = { ...prev, [url]: cleanData };
        // Save to localStorage for persistence
        try {
          localStorage.setItem("planit_cached_results", JSON.stringify(updated));
        } catch (e) {
          console.error("Failed to save cached results to localStorage:", e);
        }
        return updated;
      });
      setResult(cleanData);

      const title =
        cleanData.summary_title?.replace(/(^"|"$)/g, "") ||
        cleanData.context_summary?.replace(/(^"|"$)/g, "") ||
        "Untitled";

      // Save to history (localStorage only)
      const newHistoryItem = {
            title,
            time: new Date().toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            }),
            url,
      };
      setHistory((prev) => {
        const updated = [
          newHistoryItem,
          ...prev.filter((h) => h.url !== url).slice(0, 49), // Keep max 50 items
        ];
        // Save to localStorage
        try {
          localStorage.setItem("planit_history", JSON.stringify(updated));
        } catch (e) {
          console.error("Failed to save history to localStorage:", e);
        }
        return updated;
      });
      setLoadingStep("");
    } catch (err) {
      console.error("Extraction error:", err);
      let errorMessage = err.message || "Failed to extract venues. Please try again.";
      
      // Better error messages for connection issues
      if (err.name === "AbortError") {
        errorMessage = "Request timed out. Video processing is taking too long. The video might be too large or the backend is overloaded.";
      } else if (err.message.includes("Failed to fetch") || err.message.includes("NetworkError")) {
        // Mobile-specific error message
        const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
        if (isMobile) {
          errorMessage = `Cannot connect to backend. Make sure you're on WiFi or have good mobile data. API URL: ${API_BASE}`;
        } else {
          errorMessage = `Cannot connect to backend at ${API_BASE}. Check if the backend is running and REACT_APP_API_URL is set correctly. Current API URL: ${API_BASE}`;
        }
      } else if (err.message.includes("CORS")) {
        errorMessage = "CORS error: Backend is not allowing requests from this origin.";
      }
      
      setError(errorMessage);
      setLoadingStep("");
      setResult(null);
      setAbortController(null); // Clear controller on error
    }
  };

  // ===== Stop Analyzing =====
  const handleStopAnalyzing = () => {
    if (abortController) {
      abortController.abort();
      setAbortController(null);
      setLoadingStep("");
      setError("Analysis cancelled by user.");
    }
  };

  // ===== Tabs =====
  const handleHistoryClick = async (item) => {
    // Load cached result if available, otherwise extract
    if (cachedResults[item.url]) {
      setResult(cachedResults[item.url]);
      setVideoUrl(item.url);
      setViewingHistory(true);
      setActiveTab("home");
    } else {
      // If not cached, extract it (but keep viewingHistory flag)
    setVideoUrl(item.url);
      setViewingHistory(true); // Set before extract so it doesn't get reset
      await handleExtract(item.url, true); // Pass flag to indicate it's from history
      setActiveTab("home");
    }
  };

  const handleNewSearch = () => {
    setViewingHistory(false);
    setResult(null);
    setVideoUrl("");
    setError("");
    setActiveTab("home");
  };

  // ===== Lists =====
  const togglePlaceInList = (listName, place) => {
    const current = savedPlaces[listName] || [];
    const alreadyInList = current.some((p) => p.name === place.name);

    if (alreadyInList) {
      // Remove from list
      const updated = {
        ...savedPlaces,
        [listName]: (savedPlaces[listName] || []).filter((p) => p.name !== place.name),
      };
      // Remove empty lists
      if (updated[listName].length === 0) {
        delete updated[listName];
      }
      setSavedPlaces(updated);
      // Save to localStorage
      try {
        localStorage.setItem("planit_saved_places", JSON.stringify(updated));
      } catch (e) {
        console.error("Failed to save to localStorage:", e);
      }
    } else {
      // Add to list
      const updated = {
        ...savedPlaces,
        [listName]: [...(savedPlaces[listName] || []), place],
      };
      setSavedPlaces(updated);
      // Save to localStorage
      try {
        localStorage.setItem("planit_saved_places", JSON.stringify(updated));
      } catch (e) {
        console.error("Failed to save to localStorage:", e);
      }
    }
  };

  const handleAddNewList = () => {
    const listName = prompt("Add to new list (e.g., NYC Eats):");
    if (!listName || !listName.trim()) return;
    const trimmedName = listName.trim();
    const updated = { ...savedPlaces, [trimmedName]: [] };
    setSavedPlaces(updated);
    // Save to localStorage
    try {
      localStorage.setItem("planit_saved_places", JSON.stringify(updated));
    } catch (e) {
      console.error("Failed to save to localStorage:", e);
    }
  };

  const handleRemoveFromList = (listName, index) => {
    if (!window.confirm(`Remove this place from "${listName}"?`)) return;
    
    const place = savedPlaces[listName]?.[index];
    if (!place) return;

    const updated = { ...savedPlaces };
    const listPlaces = [...(updated[listName] || [])];
    listPlaces.splice(index, 1);
    
    if (listPlaces.length === 0) {
      delete updated[listName];
    } else {
      updated[listName] = listPlaces;
    }
    
    setSavedPlaces(updated);
    // Save to localStorage
    try {
      localStorage.setItem("planit_saved_places", JSON.stringify(updated));
    } catch (e) {
      console.error("Failed to save to localStorage:", e);
    }
  };

  const isInList = (listName, place) =>
    (savedPlaces[listName] || []).some((p) => p.name === place.name);

  const toggleShowMore = (index) => {
    setExpandedIndex(expandedIndex === index ? null : index);
  };


  // ===== UI =====
  return (
    <div className="App">
      <div className="main">
        {/* HOME */}
        {activeTab === "home" && (
          <>
            <div className="app-header">
              <PlanItLogo size={50} showText={false} />
              <h1 className="app-title">PlanIt</h1>
            </div>
            {!viewingHistory && (
            <div className="input-section">
              <input
                type="text"
                placeholder="Paste TikTok video URL..."
                value={videoUrl}
                onChange={(e) => setVideoUrl(e.target.value)}
              />
              <button onClick={() => handleExtract()}>Extract</button>
            </div>
            )}
            {viewingHistory && (
              <div className="history-view-header">
                <button onClick={handleNewSearch} className="back-to-search-btn">
                  ＋ New Search
                </button>
              </div>
            )}
            {loadingStep && (
              <div className="loading-container">
                <p className="loading">
                  {loadingStep.includes("Analyzing") && "🔍 "}
                  {loadingStep.includes("Downloading") && "📥 "}
                  {loadingStep.includes("Transcribing") && "🎤 "}
                  {loadingStep.includes("Extracting") && "✨ "}
                  {loadingStep.includes("Enriching") && "🌟 "}
                  {loadingStep}
                </p>
                {abortController && (
                  <button 
                    onClick={handleStopAnalyzing}
                    className="stop-analyzing-btn"
                  >
                    ⏹️ Stop Analyzing
                  </button>
                )}
              </div>
            )}
            {error && <p className="error">⚠️ {error}</p>}

            {result && (
              <div className="results-container">
                {(result.summary_title || result.context_summary) && (
                  <h2 className="summary-title">
                    {(() => {
                      const title = (result.summary_title || result.context_summary || "TikTok Venues").replace(/(^"|"$)/g, "");
                      // Add contextual emoji based on title content
                      let emoji = "📍";
                      if (title.toLowerCase().includes("pizza") || title.toLowerCase().includes("pizzeria")) emoji = "🍕";
                      else if (title.toLowerCase().includes("coffee") || title.toLowerCase().includes("cafe") || title.toLowerCase().includes("café")) emoji = "☕";
                      else if (title.toLowerCase().includes("bar") || title.toLowerCase().includes("cocktail")) emoji = "🍸";
                      else if (title.toLowerCase().includes("restaurant") || title.toLowerCase().includes("dining")) emoji = "🍽️";
                      else if (title.toLowerCase().includes("rooftop") || title.toLowerCase().includes("roof")) emoji = "🏙️";
                      else if (title.toLowerCase().includes("brunch")) emoji = "🥐";
                      else if (title.toLowerCase().includes("hidden") || title.toLowerCase().includes("secret")) emoji = "🔍";
                      else if (title.toLowerCase().includes("top") || title.toLowerCase().includes("best")) emoji = "⭐";
                      else if (title.toLowerCase().includes("date") || title.toLowerCase().includes("romantic")) emoji = "💕";
                      return `${emoji} ${title}`;
                    })()}
                  </h2>
                )}
                {result.video_url && (
                  <div className="tiktok-link-container">
                    <a
                      href={result.video_url}
                      target="_blank"
                      rel="noreferrer"
                      className="tiktok-button"
                    >
                      View on TikTok
                    </a>
                  </div>
                )}

                {result.places_extracted?.length === 0 && (
                  <div style={{ textAlign: "center", padding: "40px", color: "#888" }}>
                    <p>🔍 No venues found in this video.</p>
                    <p style={{ fontSize: "0.9rem", marginTop: "10px" }}>
                      {result.warning || "The video might not mention specific venue names, or they couldn't be extracted."}
                    </p>
                    {result.warning && (
                      <p style={{ fontSize: "0.85rem", marginTop: "10px", color: "#aaa", fontStyle: "italic" }}>
                        💡 Tip: Try a video with spoken audio or visible text on screen for better results.
                      </p>
                    )}
                  </div>
                )}

                {result.places_extracted?.length > 0 && (
                  <div className="view-toggle">
                    <button
                      className={viewMode === "list" ? "active" : ""}
                      onClick={() => setViewMode("list")}
                    >
                      📋 List
                    </button>
                    <button
                      className={viewMode === "map" ? "active" : ""}
                      onClick={() => setViewMode("map")}
                    >
                      🗺️ Map
                    </button>
                  </div>
                )}

                {result.places_extracted && result.places_extracted.length > 0 && viewMode === "map" && (
                  <MapView places={result.places_extracted} />
                )}
                {result.places_extracted && result.places_extracted.length > 0 && viewMode !== "map" && (
                  <div
                    className={
                      viewMode === "grid" ? "results-grid" : "results-list"
                    }
                  >
                    {result.places_extracted.map((p, i) => {
                    const isExpanded = expandedIndex === i;
                    const shortDesc =
                      p.summary && p.summary.length > 180
                        ? p.summary.slice(0, 180) + "..."
                        : p.summary;

                    return (
                      <div key={i} className="place-card">
                        {p.photo_url && (
                          <div className="photo-wrapper">
                            <img
                              src={p.photo_url}
                              alt={p.name}
                              className="place-photo"
                              referrerPolicy="no-referrer"
                            />
                          </div>
                        )}
                        <button
                          className="menu-btn"
                          onClick={() =>
                            setActiveMenu(activeMenu === i ? null : i)
                          }
                        >
                          ⋯
                        </button>
                        {activeMenu === i && (
                          <div className="menu-popup list-picker">
                            {Object.keys(savedPlaces).length > 0 ? (
                              Object.keys(savedPlaces).map((list, idx) => {
                                const selected = isInList(list, p);
                                return (
                                  <button
                                    key={idx}
                                    onClick={() => togglePlaceInList(list, p)}
                                    className={`list-toggle ${
                                      selected ? "selected" : ""
                                    }`}
                                  >
                                    <span
                                      className={`circle ${
                                        selected ? "filled" : ""
                                      }`}
                                    >
                                      {selected ? "✓" : ""}
                                    </span>
                                    {list}
                                  </button>
                                );
                              })
                            ) : (
                              <p className="empty-list-msg">No lists yet</p>
                            )}
                            <button
                              className="add-new-list-btn"
                              onClick={handleAddNewList}
                            >
                              ➕ Add to New List
                            </button>
                          </div>
                        )}

                        <div className="place-info">
                          <h3>{p.name}</h3>
                          {p.summary && (
                            <p className="description">
                              {isExpanded ? p.summary : shortDesc}
                              {p.summary?.length > 180 && (
                                <span
                                  className="show-more"
                                  onClick={() => toggleShowMore(i)}
                                >
                                  {isExpanded ? " Show less" : " Show more"}
                                </span>
                              )}
                            </p>
                          )}
                          {p.vibe_tags && p.vibe_tags.length > 0 && (
                            <div className="vibe-section">
                              <strong>Vibes:</strong>
                              <div className="vibe-tags">
                                {p.vibe_tags.map((tag, idx) => (
                                  <span key={idx} className="vibe-chip">
                                    {tag}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}
                          {p.other_videos && p.other_videos.length > 0 && (
                            <div className="other-videos-note">
                              <details className="tiktok-dropdown">
                                <summary className="tiktok-dropdown-summary">
                                  <strong>📹 From TikTok{p.other_videos.length > 1 ? 's' : ''} ({p.other_videos.length})</strong>
                                </summary>
                                <div className="other-videos-list">
                                  {p.other_videos.map((vid, idx) => (
                                    <div key={idx} className="other-video-item">
                                      {vid.username && (
                                        <span className="other-video-username">@{vid.username}</span>
                                      )}
                                      <a
                                        href={vid.url}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="other-video-link"
                                      >
                                        {vid.summary || "this video"}
                                      </a>
                                    </div>
                                  ))}
                                </div>
                              </details>
                            </div>
                          )}
                          <div className="meta">
                            {p.must_try && (
                              <p>
                                <strong>
                                  {p.must_try_field === "highlights" ? "✨ Highlights:" :
                                   p.must_try_field === "features" ? "🎯 Features:" :
                                   "🍴 Must Try:"}
                                </strong> {p.must_try}
                              </p>
                            )}
                            {p.when_to_go && (
                              <p>
                                <strong>🕐 When to Go:</strong> {p.when_to_go}
                              </p>
                            )}
                            {p.vibe && (
                              <p>
                                <strong>💫 Vibe:</strong> {p.vibe}
                              </p>
                            )}
                          </div>
                          {p.maps_url && (
                            <a
                              href={p.maps_url}
                              target="_blank"
                              rel="noreferrer"
                              className="maps-link"
                            >
                              🗺️ Open in Maps
                            </a>
                          )}
                        </div>
                      </div>
                    );
                  })}
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {/* HISTORY */}
        {activeTab === "history" && (
          <div className="history-page">
            <h2>📜 Extraction History</h2>
            {history.length === 0 ? (
              <p className="empty">📭 No extractions yet.</p>
            ) : (
              <div className="history-scroll">
                {history.map((h, i) => (
                  <div
                    key={i}
                    className="hist-item"
                    onClick={() => handleHistoryClick(h)}
                  >
                    <strong>📍 {h.title}</strong>
                    <span className="time">🕐 {h.time}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* SAVED */}
        {activeTab === "saved" && (
          <div className="saved-page">
            {!selectedList ? (
              <>
                <h2 className="saved-header">⭐ Your Saved Lists</h2>
                {Object.keys(savedPlaces).length === 0 ? (
                  <p className="empty">💾 No saved places yet.</p>
                ) : (
                  Object.entries(savedPlaces).map(([list, places], idx) => (
                    <div key={idx} className="saved-list-card">
                      <div
                        className="saved-list-header"
                        onClick={() => setSelectedList(list)}
                      >
                        <h3>{list}</h3>
                        <span className="count">
                          {places.length} place{places.length !== 1 ? "s" : ""}
                        </span>
                      </div>
                    </div>
                  ))
                )}
              </>
            ) : (
              <div className="list-detail-page">
                <div className="list-detail-header">
                  <button 
                    className="back-btn"
                    onClick={() => {
                      setSelectedList(null);
                      setShowListMap(false);
                    }}
                  >
                    ← Back
                  </button>
                  <h2 className="list-detail-title">{selectedList}</h2>
                  <div className="view-toggle-list-detail">
                    <button
                      className={!showListMap ? "active" : ""}
                      onClick={() => setShowListMap(false)}
                    >
                      List
                    </button>
                    <button
                      className={showListMap ? "active" : ""}
                      onClick={() => setShowListMap(true)}
                    >
                      Map
                    </button>
                  </div>
                </div>
                
                {showListMap ? (
                  <MapView places={savedPlaces[selectedList] || []} />
                ) : (
                  <div className="saved-list-places">
                    {savedPlaces[selectedList]?.map((p, i) => (
                      <div key={i} className="place-card">
                        {p.photo_url && (
                          <img
                            src={p.photo_url}
                            alt={p.name}
                            className="place-photo"
                          />
                        )}
                        <div className="place-info">
                          <h4>{p.name}</h4>
                          {p.summary && (
                            <p className="description">{p.summary}</p>
                          )}
                          {p.vibe_tags && p.vibe_tags.length > 0 && (
                            <p className="vibe-line">
                              Vibes: {p.vibe_tags.join(", ")}
                            </p>
                          )}
                          {p.other_videos && p.other_videos.length > 0 && (
                            <div className="other-videos-note">
                              <details className="tiktok-dropdown">
                                <summary className="tiktok-dropdown-summary">
                                  <strong>📹 From TikTok{p.other_videos.length > 1 ? 's' : ''} ({p.other_videos.length})</strong>
                                </summary>
                                <div className="other-videos-list">
                                  {p.other_videos.map((vid, vidIdx) => (
                                    <div key={vidIdx} className="other-video-item">
                                      {vid.username && (
                                        <span className="other-video-username">@{vid.username}</span>
                                      )}
                                      <a
                                        href={vid.url}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="other-video-link"
                                      >
                                        {vid.summary || "this video"}
                                      </a>
                                    </div>
                                  ))}
                                </div>
                              </details>
                            </div>
                          )}
                          <div className="button-row">
                            {p.maps_url && (
                              <a
                                href={p.maps_url}
                                target="_blank"
                                rel="noreferrer"
                                className="action-btn"
                              >
                                Open in Maps
                              </a>
                            )}
                            {p.video_url && (
                              <a
                                href={p.video_url}
                                target="_blank"
                                rel="noreferrer"
                                className="action-btn"
                              >
                                View on TikTok
                              </a>
                            )}
                            <button
                              className="action-btn remove"
                              onClick={() => {
                                handleRemoveFromList(selectedList, i);
                                if (savedPlaces[selectedList]?.length === 1) {
                                  setSelectedList(null);
                                }
                              }}
                            >
                              Remove
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* NAV */}
      <div className="bottom-nav">
        <button
          className={`nav-btn ${activeTab === "history" ? "active" : ""}`}
          onClick={() => setActiveTab("history")}
        >
          <Grid size={22} />
        </button>
        <button
          className={`nav-btn center-btn ${activeTab === "home" ? "active" : ""}`}
          onClick={() => {
            handleNewSearch();
            setActiveTab("home");
          }}
        >
          ＋
        </button>
        <button
          className={`nav-btn ${activeTab === "saved" ? "active" : ""}`}
          onClick={() => setActiveTab("saved")}
        >
          ⭐
        </button>
      </div>
    </div>
  );
}

export default App;
