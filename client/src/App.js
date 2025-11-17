import React, { useState, useEffect } from "react";
import { Grid, Search, Plus, Star, MapPin, Sparkles, Loader2, X, ChevronRight, Map, List, Edit2, Check, Pizza, Coffee, Wine, UtensilsCrossed, Building2, Croissant, Eye, Award, Heart } from "lucide-react";
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
  const [editingListName, setEditingListName] = useState(null); // For editing list names
  const [editingListValue, setEditingListValue] = useState("");
  const [expandedSavedPlaceIndex, setExpandedSavedPlaceIndex] = useState(null); // For expanded place details in saved lists
  const [noteToSelf, setNoteToSelf] = useState(""); // Note to self for current extraction
  const [editingNote, setEditingNote] = useState(false); // Whether note is being edited

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
    setNoteToSelf("");
    setEditingNote(false);
    if (!isFromHistory) {
      setViewingHistory(false); // Reset history view when starting new extraction
    }
    setLoadingStep("Analyzing TikTok...");

    if (cachedResults[url]) {
      const cached = cachedResults[url];
      setResult(cached);
      setNoteToSelf(cached.note_to_self || "");
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
      
      // Retry logic for connection errors (common with Render free tier sleeping)
      let res;
      let retries = 2;
      let lastError;
      
      for (let attempt = 0; attempt <= retries; attempt++) {
        try {
          if (attempt > 0) {
            console.log(`🔄 Retry attempt ${attempt} of ${retries}...`);
            setLoadingStep(`Connecting to backend... (attempt ${attempt + 1})`);
            await new Promise(resolve => setTimeout(resolve, 2000)); // Wait 2 seconds between retries
          }
          
          res = await fetch(`${API_BASE}/api/extract`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ video_url: url }),
            signal: controller.signal,
          });
          break; // Success, exit retry loop
        } catch (fetchError) {
          lastError = fetchError;
          // If it's a connection error and we have retries left, retry
          if (attempt < retries && (fetchError.message.includes("Failed to fetch") || fetchError.message.includes("NetworkError") || fetchError.name === "TypeError")) {
            continue; // Retry
          }
          throw fetchError; // Re-throw if no retries left or different error
        }
      }
      
      if (!res) {
        throw lastError || new Error("Failed to fetch");
      }
      
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

      // attach TikTok URL to every place and deduplicate
      const placesArray = data.places_extracted || [];
      // Use a plain object for deduplication to avoid Map constructor issues
      const placesMap = {};
      placesArray.forEach((p) => {
        if (p && p.name) {
          placesMap[p.name] = { ...p, video_url: data.video_url };
        }
      });
      const uniquePlaces = Object.values(placesMap);
      const cleanData = { ...data, places_extracted: uniquePlaces };

      console.log("💾 Setting result:", cleanData);
      
      // Load existing note if available
      const existingCached = cachedResults[url];
      const existingNote = existingCached?.note_to_self || "";
      setNoteToSelf(existingNote);
      
      setCachedResults((prev) => {
        const updated = { ...prev, [url]: { ...cleanData, note_to_self: existingNote } };
        // Save to localStorage for persistence
        try {
          localStorage.setItem("planit_cached_results", JSON.stringify(updated));
        } catch (e) {
          console.error("Failed to save cached results to localStorage:", e);
        }
        return updated;
      });
      setResult({ ...cleanData, note_to_self: existingNote });

      const title =
        cleanData.summary_title?.replace(/(^"|"$)/g, "") ||
        cleanData.context_summary?.replace(/(^"|"$)/g, "") ||
        "Untitled";

      // Save to history (localStorage only)
      // Use Date constructor directly to avoid minification issues
      const DateCtor = window.Date;
      const now = new DateCtor();
      const newHistoryItem = {
            title,
            time: now.toLocaleTimeString([], {
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
      } else if (err.message.includes("Failed to fetch") || err.message.includes("NetworkError") || err.message.includes("Network request failed")) {
        // Mobile-specific error message
        const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
        if (isMobile) {
          errorMessage = `Cannot connect to backend. The backend may be sleeping (Render free tier). Try again in a few seconds. API URL: ${API_BASE}`;
        } else {
          errorMessage = `Cannot connect to backend at ${API_BASE}. The backend may be sleeping (Render free tier) or down. Try again in a few seconds. If the problem persists, check the Render dashboard.`;
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
      const cached = cachedResults[item.url];
      setResult(cached);
      setNoteToSelf(cached.note_to_self || "");
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

  // Save note to self
  const handleSaveNote = () => {
    if (!result) return;
    const currentUrl = result.video_url || videoUrl;
    if (!currentUrl) return;
    
    const updatedResult = { ...result, note_to_self: noteToSelf };
    setResult(updatedResult);
    
    setCachedResults((prev) => {
      const updated = { ...prev, [currentUrl]: updatedResult };
      try {
        localStorage.setItem("planit_cached_results", JSON.stringify(updated));
      } catch (e) {
        console.error("Failed to save note to localStorage:", e);
      }
      return updated;
    });
    
    setEditingNote(false);
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

  const handleRenameList = (oldName, newName) => {
    if (!newName || !newName.trim() || newName === oldName) {
      setEditingListName(null);
      return;
    }
    
    const trimmedName = newName.trim();
    
    // Check if new name already exists
    if (savedPlaces[trimmedName] && trimmedName !== oldName) {
      alert(`A list named "${trimmedName}" already exists.`);
      return;
    }
    
    // Rename the list
    const updated = { ...savedPlaces };
    updated[trimmedName] = updated[oldName];
    delete updated[oldName];
    
    // Update selectedList if it was the renamed list
    if (selectedList === oldName) {
      setSelectedList(trimmedName);
    }
    
    setSavedPlaces(updated);
    setEditingListName(null);
    
    // Save to localStorage
    try {
      localStorage.setItem("planit_saved_places", JSON.stringify(updated));
    } catch (e) {
      console.error("Failed to save to localStorage:", e);
    }
  };

  const handleStartEditList = (listName, e) => {
    e.stopPropagation(); // Prevent opening the list
    setEditingListName(listName);
    setEditingListValue(listName);
  };

  const handleSaveEditList = (oldName, e) => {
    e.stopPropagation();
    handleRenameList(oldName, editingListValue);
  };

  const handleCancelEditList = (e) => {
    e.stopPropagation();
    setEditingListName(null);
    setEditingListValue("");
  };

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
              <div className="logo-glow-wrapper">
                <PlanItLogo size={40} showText={false} />
              </div>
              <h1 className="app-title">PlanIt</h1>
            </div>
            {!viewingHistory && (
            <div className="input-section">
              <Search size={20} className="search-icon" />
              <input
                type="text"
                placeholder="Paste TikTok video URL..."
                value={videoUrl}
                onChange={(e) => setVideoUrl(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && videoUrl) {
                    handleExtract();
                  }
                }}
              />
              <button onClick={() => handleExtract()} className="extract-pill-btn">
                <Sparkles size={16} />
                Extract
              </button>
            </div>
            )}
            {viewingHistory && (
              <div className="history-view-header">
                <button onClick={handleNewSearch} className="back-to-search-btn">
                  <Plus size={16} style={{ marginRight: '6px', verticalAlign: 'middle' }} />
                  New Search
                </button>
              </div>
            )}
            {loadingStep && (
              <div className="loading-container">
                <p className="loading">
                  {loadingStep.includes("Analyzing") && <Search size={16} className="loading-icon" />}
                  {loadingStep.includes("Downloading") && <Loader2 size={16} className="loading-icon spin" />}
                  {loadingStep.includes("Transcribing") && <Loader2 size={16} className="loading-icon spin" />}
                  {loadingStep.includes("Extracting") && <Sparkles size={16} className="loading-icon" />}
                  {loadingStep.includes("Enriching") && <Sparkles size={16} className="loading-icon" />}
                  {loadingStep}
                </p>
                {abortController && (
                  <button 
                    onClick={handleStopAnalyzing}
                    className="stop-analyzing-btn"
                  >
                    <X size={16} className="stop-icon" />
                    Stop Analyzing
                  </button>
                )}
              </div>
            )}
            {error && <p className="error"><X size={16} className="error-icon" />{error}</p>}

            {result && (
              <div className="results-container">
                {(result.summary_title || result.context_summary) && (
                  <h2 className="summary-title">
                    {(() => {
                      const title = (result.summary_title || result.context_summary || "TikTok Venues").replace(/(^"|"$)/g, "");
                      // Add contextual icon based on title content
                      let Icon = MapPin;
                      if (title.toLowerCase().includes("pizza") || title.toLowerCase().includes("pizzeria")) Icon = Pizza;
                      else if (title.toLowerCase().includes("coffee") || title.toLowerCase().includes("cafe") || title.toLowerCase().includes("café")) Icon = Coffee;
                      else if (title.toLowerCase().includes("bar") || title.toLowerCase().includes("cocktail")) Icon = Wine;
                      else if (title.toLowerCase().includes("restaurant") || title.toLowerCase().includes("dining")) Icon = UtensilsCrossed;
                      else if (title.toLowerCase().includes("rooftop") || title.toLowerCase().includes("roof")) Icon = Building2;
                      else if (title.toLowerCase().includes("brunch")) Icon = Croissant;
                      else if (title.toLowerCase().includes("hidden") || title.toLowerCase().includes("secret")) Icon = Eye;
                      else if (title.toLowerCase().includes("top") || title.toLowerCase().includes("best")) Icon = Award;
                      else if (title.toLowerCase().includes("date") || title.toLowerCase().includes("romantic")) Icon = Heart;
                      return (
                        <span className="summary-title-content">
                          <Icon size={20} className="summary-icon" />
                          {title}
                        </span>
                      );
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

                {/* Note to Self Section */}
                <div className="note-to-self-container">
                  {editingNote ? (
                    <div className="note-editor">
                      <label className="note-label">📝 Note to Self:</label>
                      <textarea
                        className="note-textarea"
                        value={noteToSelf}
                        onChange={(e) => setNoteToSelf(e.target.value)}
                        placeholder="Add a personal note about this extraction..."
                        rows={3}
                        autoFocus
                      />
                      <div className="note-actions">
                        <button
                          className="note-save-btn"
                          onClick={handleSaveNote}
                        >
                          Save
                        </button>
                        <button
                          className="note-cancel-btn"
                          onClick={() => {
                            setEditingNote(false);
                            // Restore original note if cancelled
                            setNoteToSelf(result.note_to_self || "");
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div 
                      className="note-display"
                      onClick={() => setEditingNote(true)}
                    >
                      <div className="note-label">📝 Note to Self:</div>
                      {noteToSelf ? (
                        <div className="note-content">{noteToSelf}</div>
                      ) : (
                        <div className="note-placeholder">Click to add a note...</div>
                      )}
                    </div>
                  )}
                </div>

                {result.places_extracted?.length === 0 && (
                  <div style={{ textAlign: "center", padding: "40px", color: "#888" }}>
                    <p><Search size={18} style={{ display: 'inline', marginRight: '8px', verticalAlign: 'middle' }} />No venues found in this video.</p>
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
                  <MapView 
                    places={result.places_extracted}
                    onClose={() => setViewMode("list")}
                  />
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
            <h2 className="history-header">Extraction History</h2>
            {history.length === 0 ? (
              <div className="empty-state">
                <p className="empty-text">No extractions yet</p>
                <p className="empty-subtext">Start by extracting venues from a TikTok video</p>
              </div>
            ) : (
              <div className="history-scroll">
                {history.map((h, i) => (
                  <div
                    key={i}
                    className="hist-item"
                    onClick={() => handleHistoryClick(h)}
                  >
                    <div className="hist-content">
                      <strong className="hist-title">{h.title}</strong>
                      <span className="hist-time">{h.time}</span>
                    </div>
                    <ChevronRight size={20} className="hist-arrow" />
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
                <div className="saved-header-wrapper">
                  <MapPin size={24} className="saved-header-icon" />
                  <h2 className="saved-header">Your Saved Lists</h2>
                </div>
                {Object.keys(savedPlaces).length === 0 ? (
                  <div className="empty-state">
                    <p className="empty-text">No saved places yet</p>
                    <p className="empty-subtext">Start saving venues to create your lists</p>
                  </div>
                ) : (
                  <div className="history-scroll">
                    {Object.entries(savedPlaces).map(([list, places], idx) => (
                      <div 
                        key={idx} 
                        className="hist-item"
                        onClick={() => {
                          if (editingListName !== list) {
                            setSelectedList(list);
                          }
                        }}
                      >
                        {editingListName === list ? (
                          <div className="list-name-edit" onClick={(e) => e.stopPropagation()}>
                            <input
                              type="text"
                              value={editingListValue}
                              onChange={(e) => setEditingListValue(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") {
                                  handleSaveEditList(list, e);
                                } else if (e.key === "Escape") {
                                  handleCancelEditList(e);
                                }
                              }}
                              onClick={(e) => e.stopPropagation()}
                              className="list-name-input"
                              autoFocus
                            />
                            <button
                              className="save-edit-btn"
                              onClick={(e) => handleSaveEditList(list, e)}
                              title="Save"
                            >
                              ✓
                            </button>
                            <button
                              className="cancel-edit-btn"
                              onClick={(e) => handleCancelEditList(e)}
                              title="Cancel"
                            >
                              ✕
                            </button>
                          </div>
                        ) : (
                          <>
                            <div className="hist-content">
                              <div className="list-name-wrapper">
                                <strong className="hist-title">{list}</strong>
                                <button
                                  className="edit-list-btn"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleStartEditList(list, e);
                                  }}
                                  title="Rename list"
                                >
                                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                                    <path d="M11.333 2.667a.667.667 0 0 1 .943 0l1.334 1.334a.667.667 0 0 1 0 .943L12.276 6.22 9.78 3.724l1.334-1.334a.667.667 0 0 1 .22-.723zM8.667 5.333L3.333 10.667v2.667h2.667l5.333-5.334L8.667 5.333z" fill="currentColor" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                                  </svg>
                                </button>
                              </div>
                              <span className="hist-time">
                                {places.length} place{places.length !== 1 ? "s" : ""}
                              </span>
                            </div>
                            <ChevronRight size={20} className="hist-arrow" />
                          </>
                        )}
                      </div>
                    ))}
                  </div>
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
                    <ChevronRight size={18} className="back-icon" style={{ transform: 'rotate(180deg)' }} />
                    Back
                  </button>
                  <div className="list-detail-center">
                    {editingListName === selectedList ? (
                      <div className="list-detail-title-edit">
                        <input
                          type="text"
                          value={editingListValue}
                          onChange={(e) => setEditingListValue(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              handleSaveEditList(selectedList, e);
                            } else if (e.key === "Escape") {
                              handleCancelEditList(e);
                            }
                          }}
                          className="list-name-input-large"
                          autoFocus
                        />
                        <button
                          className="save-edit-btn"
                          onClick={(e) => handleSaveEditList(selectedList, e)}
                          title="Save"
                        >
                          ✓
                        </button>
                        <button
                          className="cancel-edit-btn"
                          onClick={(e) => handleCancelEditList(e)}
                          title="Cancel"
                        >
                          ✕
                        </button>
                      </div>
                    ) : (
                      <div className="list-detail-title-wrapper">
                        <h2 className="list-detail-title">{selectedList}</h2>
                        <button
                          className="edit-list-btn-detail"
                          onClick={(e) => handleStartEditList(selectedList, e)}
                          title="Rename list"
                        >
                          <svg width="18" height="18" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M11.333 2.667a.667.667 0 0 1 .943 0l1.334 1.334a.667.667 0 0 1 0 .943L12.276 6.22 9.78 3.724l1.334-1.334a.667.667 0 0 1 .22-.723zM8.667 5.333L3.333 10.667v2.667h2.667l5.333-5.334L8.667 5.333z" fill="currentColor" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                          </svg>
                        </button>
                      </div>
                    )}
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
                </div>
                
                {showListMap ? (
                  <MapView 
                    places={savedPlaces[selectedList] || []}
                    savedPlaces={savedPlaces}
                    togglePlaceInList={togglePlaceInList}
                    handleAddNewList={handleAddNewList}
                    isInList={isInList}
                    onClose={() => setShowListMap(false)}
                  />
                ) : (
                  <div className="saved-list-places">
                    {savedPlaces[selectedList]?.map((p, i) => {
                      const isExpanded = expandedSavedPlaceIndex === i;
                      // Get first sentence or first 120 characters as one-line summary
                      const oneLineSummary = p.summary 
                        ? (p.summary.split('.')[0].trim() || p.summary.slice(0, 120)).replace(/\.$/, '')
                        : null;
                      
                      return (
                        <div 
                          key={i} 
                          className="place-card"
                          onClick={() => setExpandedSavedPlaceIndex(isExpanded ? null : i)}
                          style={{ cursor: 'pointer' }}
                        >
                          {p.photo_url && (
                            <img
                              src={p.photo_url}
                              alt={p.name}
                              className="place-photo"
                            />
                          )}
                          <div className="place-info">
                            <h4>{p.name}</h4>
                            {oneLineSummary && (
                              <p className="description">{oneLineSummary}</p>
                            )}
                            {p.vibe_tags && p.vibe_tags.length > 0 && (
                              <div className="vibe-section">
                                <div className="vibe-tags">
                                  {p.vibe_tags.map((tag, idx) => (
                                    <span key={idx} className="vibe-chip">
                                      {tag}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}
                            
                            {/* Show More/Less Toggle */}
                            {(p.summary?.length > (oneLineSummary?.length || 0) || p.must_try || p.when_to_go || p.vibe || p.other_videos?.length > 0) && (
                              <button
                                className="show-more-btn"
                                onClick={() => setExpandedSavedPlaceIndex(isExpanded ? null : i)}
                              >
                                {isExpanded ? "Show Less" : "Show More"}
                              </button>
                            )}
                            
                            {/* Expanded Content */}
                            {isExpanded && (
                              <div className="expanded-content">
                                {p.summary && p.summary !== oneLineSummary && (
                                  <p className="description full-summary">{p.summary}</p>
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
                      );
                    })}
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
          <span className="nav-btn-label">History</span>
        </button>
        <button
          className={`nav-btn center-btn ${activeTab === "home" ? "active" : ""}`}
          onClick={() => {
            handleNewSearch();
            setActiveTab("home");
          }}
        >
          <Plus size={20} />
        </button>
        <button
          className={`nav-btn ${activeTab === "saved" ? "active" : ""}`}
          onClick={() => setActiveTab("saved")}
        >
          <Star size={22} />
          <span className="nav-btn-label">Saved</span>
        </button>
      </div>
    </div>
  );
}

export default App;
