import React, { useState, useEffect, useContext } from "react";
import { Grid } from "lucide-react";
import "./App.css";
import PlanItLogo from "./components/PlanItLogo";
import Login from "./components/Login";
import Signup from "./components/Signup";
import { UserContext } from "./contexts/UserContext";

const API_BASE = process.env.REACT_APP_API_URL || "https://planit-backend-fbm5.onrender.com";

// Log the API base URL for debugging
console.log("🔧 API_BASE:", API_BASE);
console.log("🔧 REACT_APP_API_URL env:", process.env.REACT_APP_API_URL);

function App() {
  const { user, token, loading: userLoading } = useContext(UserContext);
  const [showAuth, setShowAuth] = useState(false);
  const [authMode, setAuthMode] = useState("login"); // "login" or "signup"
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

  // Handle share target / deep linking
  useEffect(() => {
    // Check URL params for shared TikTok URL
    const urlParams = new URLSearchParams(window.location.search);
    const sharedUrl = urlParams.get("url") || urlParams.get("tiktok_url") || urlParams.get("text");
    if (sharedUrl && sharedUrl.includes("tiktok.com")) {
      setVideoUrl(sharedUrl);
      // Auto-extract if user is logged in
      if (user && token) {
        setTimeout(() => {
          handleExtract(sharedUrl);
        }, 500);
      }
    }

    // Handle Android share intent
    if (window.Android && window.Android.getSharedUrl) {
      const sharedUrl = window.Android.getSharedUrl();
      if (sharedUrl && sharedUrl.includes("tiktok.com")) {
        setVideoUrl(sharedUrl);
        if (user && token) {
          setTimeout(() => {
            handleExtract(sharedUrl);
          }, 500);
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, token]);

  // Load saved places and history from API
  useEffect(() => {
    if (user && token) {
      // Load saved places
      fetch(`${API_BASE}/api/user/saved-places`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((res) => res.json())
        .then((data) => {
          if (data && !data.error) {
            setSavedPlaces(data);
          }
        })
        .catch((err) => console.error("Failed to load saved places:", err));

      // Load history
      fetch(`${API_BASE}/api/user/history`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((res) => res.json())
        .then((data) => {
          if (data && !data.error) {
            setHistory(data.map((h) => ({
              title: h.summary_title || "Untitled",
              time: new Date(h.timestamp).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              }),
              url: h.video_url,
            })));
          }
        })
        .catch((err) => console.error("Failed to load history:", err));
    } else {
      setSavedPlaces({});
      setHistory([]);
    }
  }, [user, token]);

  // ===== Extract TikTok =====
  const handleExtract = async (urlToUse = null) => {
    const url = urlToUse || videoUrl;
    if (!url) return setError("Enter a TikTok URL");

    setError("");
    setResult(null);
    setLoadingStep("Analyzing TikTok...");

    if (cachedResults[url]) {
      setResult(cachedResults[url]);
      setLoadingStep("");
      return;
    }

    try {
      console.log(`🌐 Calling API: ${API_BASE}/api/extract`);
      
      // Add timeout for long-running requests (10 minutes for videos with OCR)
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 10 * 60 * 1000); // 10 minutes
      
      const res = await fetch(`${API_BASE}/api/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_url: url }),
        signal: controller.signal,
      });
      
      clearTimeout(timeoutId);
      
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
      setCachedResults((prev) => ({ ...prev, [url]: cleanData }));
      setResult(cleanData);

      const title =
        cleanData.summary_title?.replace(/(^"|"$)/g, "") ||
        cleanData.context_summary?.replace(/(^"|"$)/g, "") ||
        "Untitled";

      // Save to history API if logged in
      if (user && token) {
        fetch(`${API_BASE}/api/user/history`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            video_url: url,
            summary_title: title,
          }),
        })
        .then(() => {
          // Reload history from API to ensure consistency
          return fetch(`${API_BASE}/api/user/history`, {
            headers: { Authorization: `Bearer ${token}` },
          });
        })
        .then((res) => res.json())
        .then((data) => {
          if (data && !data.error) {
            setHistory(data.map((h) => ({
              title: h.summary_title || "Untitled",
              time: new Date(h.timestamp).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              }),
              url: h.video_url,
            })));
          }
        })
        .catch((err) => console.error("Failed to save/load history:", err));
      } else {
        // If not logged in, just update local state (will be lost on refresh)
        setHistory((prev) => [
          {
            title,
            time: new Date().toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            }),
            url,
          },
          ...prev.filter((h) => h.url !== url).slice(0, 10),
        ]);
      }
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
    }
  };

  // ===== Tabs =====
  const handleHistoryClick = (item) => {
    setVideoUrl(item.url);
    handleExtract(item.url);
    setActiveTab("home");
  };

  // ===== Lists =====
  const togglePlaceInList = async (listName, place) => {
    if (!user || !token) {
      setShowAuth(true);
      setAuthMode("login");
      return;
    }

    const current = savedPlaces[listName] || [];
    const alreadyInList = current.some((p) => p.name === place.name);

    if (alreadyInList) {
      // Remove from list
      try {
        await fetch(`${API_BASE}/api/user/saved-places`, {
          method: "DELETE",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            list_name: listName,
            place_name: place.name,
          }),
        });
        setSavedPlaces((prev) => ({
          ...prev,
          [listName]: (prev[listName] || []).filter((p) => p.name !== place.name),
        }));
      } catch (err) {
        console.error("Failed to remove place:", err);
      }
    } else {
      // Add to list
      try {
        await fetch(`${API_BASE}/api/user/saved-places`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            list_name: listName,
            place_data: place,
          }),
        });
        setSavedPlaces((prev) => ({
          ...prev,
          [listName]: [...(prev[listName] || []), place],
        }));
      } catch (err) {
        console.error("Failed to add place:", err);
      }
    }
  };

  const handleAddNewList = () => {
    const listName = prompt("Add to new list (e.g., NYC Eats):");
    if (!listName) return;
    setSavedPlaces((prev) => ({ ...prev, [listName]: [] }));
  };

  const handleRemoveFromList = async (listName, index) => {
    if (!window.confirm(`Remove this place from "${listName}"?`)) return;
    
    const place = savedPlaces[listName]?.[index];
    if (!place || !user || !token) return;

    try {
      await fetch(`${API_BASE}/api/user/saved-places`, {
        method: "DELETE",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          list_name: listName,
          place_name: place.name,
        }),
      });
      setSavedPlaces((prev) => {
        const updated = [...(prev[listName] || [])];
        updated.splice(index, 1);
        return { ...prev, [listName]: updated };
      });
    } catch (err) {
      console.error("Failed to remove place:", err);
    }
  };

  const isInList = (listName, place) =>
    (savedPlaces[listName] || []).some((p) => p.name === place.name);

  const toggleShowMore = (index) => {
    setExpandedIndex(expandedIndex === index ? null : index);
  };

  // Show auth screen if not logged in
  if (userLoading) {
    return (
      <div className="App">
        <div className="main" style={{ display: "flex", justifyContent: "center", alignItems: "center" }}>
          <p style={{ color: "#aaa" }}>Loading...</p>
        </div>
      </div>
    );
  }

  if (showAuth || !user) {
    return (
      <div className="App">
        <div className="main" style={{ padding: 0 }}>
          {authMode === "login" ? (
            <Login onSwitchToSignup={() => setAuthMode("signup")} />
          ) : (
            <Signup onSwitchToLogin={() => setAuthMode("login")} />
          )}
        </div>
      </div>
    );
  }

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
              <button
                onClick={() => {
                  localStorage.removeItem("planit_token");
                  window.location.reload();
                }}
                className="logout-btn"
              >
                Logout
              </button>
            </div>
            <div className="input-section">
              <input
                type="text"
                placeholder="Paste TikTok video URL..."
                value={videoUrl}
                onChange={(e) => setVideoUrl(e.target.value)}
              />
              <button onClick={() => handleExtract()}>Extract</button>
            </div>
            {loadingStep && <p className="loading">{loadingStep}</p>}
            {error && <p className="error">{error}</p>}

            {result && (
              <div className="results-container">
                {(result.summary_title || result.context_summary) && (
                  <h2 className="summary-title">
                    {(result.summary_title || result.context_summary || "TikTok Venues").replace(/(^"|"$)/g, "")}
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
                    <p>No venues found in this video.</p>
                    <p style={{ fontSize: "0.9rem", marginTop: "10px" }}>
                      {result.warning || "The video might not mention specific venue names, or they couldn't be extracted."}
                    </p>
                    {result.warning && (
                      <p style={{ fontSize: "0.85rem", marginTop: "10px", color: "#aaa", fontStyle: "italic" }}>
                        Tip: Try a video with spoken audio or visible text on screen for better results.
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
                      List
                    </button>
                    <button
                      className={viewMode === "grid" ? "active" : ""}
                      onClick={() => setViewMode("grid")}
                    >
                      Grid
                    </button>
                  </div>
                )}

                {result.places_extracted && result.places_extracted.length > 0 && (
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
                              Add to New List
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
                              <strong>📹 Also featured in:</strong>
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
                            </div>
                          )}
                          <div className="meta">
                            {p.must_try && (
                              <p>
                                <strong>
                                  {p.must_try_field === "highlights" ? "Highlights:" :
                                   p.must_try_field === "features" ? "Features:" :
                                   "Must Try:"}
                                </strong> {p.must_try}
                              </p>
                            )}
                            {p.when_to_go && (
                              <p>
                                <strong>When to Go:</strong> {p.when_to_go}
                              </p>
                            )}
                            {p.vibe && (
                              <p>
                                <strong>Vibe:</strong> {p.vibe}
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
                              Open in Maps
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
            <h2>Extraction History</h2>
            {history.length === 0 ? (
              <p className="empty">No extractions yet.</p>
            ) : (
              <div className="history-scroll">
                {history.map((h, i) => (
                  <div
                    key={i}
                    className="hist-item"
                    onClick={() => handleHistoryClick(h)}
                  >
                    <strong>{h.title}</strong>
                    <span className="time">{h.time}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* SAVED */}
        {activeTab === "saved" && (
          <div className="saved-page">
            <h2 className="saved-header">Your Saved Lists</h2>
            {Object.keys(savedPlaces).length === 0 ? (
              <p className="empty">No saved places yet.</p>
            ) : (
              Object.entries(savedPlaces).map(([list, places], idx) => (
                <div key={idx} className="saved-list-card">
                  <div
                    className="saved-list-header"
                    onClick={() =>
                      setExpandedIndex(expandedIndex === idx ? null : idx)
                    }
                  >
                    <h3>{list}</h3>
                    <span className="count">
                      {places.length} place{places.length !== 1 ? "s" : ""}
                    </span>
                  </div>

                  {expandedIndex === idx && (
                    <div className="saved-list-places">
                      {places.map((p, i) => (
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
                                onClick={() =>
                                  handleRemoveFromList(list, i)
                                }
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
              ))
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
          onClick={() => setActiveTab("home")}
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
