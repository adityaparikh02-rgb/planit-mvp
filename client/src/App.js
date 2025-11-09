import React, { useState, useEffect } from "react";
import "./App.css";
import PlanItLogo from "./components/PlanItLogo";

const API_BASE = process.env.REACT_APP_API_URL || "https://planit-mvp.onrender.com";

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

  // Load saved lists
  useEffect(() => {
    const saved = localStorage.getItem("planit_saved_places");
    if (saved) setSavedPlaces(JSON.parse(saved));
  }, []);

  useEffect(() => {
    localStorage.setItem("planit_saved_places", JSON.stringify(savedPlaces));
  }, [savedPlaces]);

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
      
      const res = await fetch(`${API_BASE}/api/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_url: url }),
      });
      
      console.log(`📡 Response status: ${res.status}`);
      
      // Try to parse JSON, but handle non-JSON errors
      let data;
      try {
        data = await res.json();
      } catch (parseError) {
        const text = await res.text();
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

      setCachedResults((prev) => ({ ...prev, [url]: cleanData }));
      setResult(cleanData);

      const title =
        cleanData.summary_title?.replace(/(^"|"$)/g, "") ||
        cleanData.context_summary?.replace(/(^"|"$)/g, "") ||
        "Untitled";

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
      setLoadingStep("");
    } catch (err) {
      console.error("Extraction error:", err);
      let errorMessage = err.message || "Failed to extract venues. Please try again.";
      
      // Better error messages for connection issues
      if (err.message.includes("Failed to fetch") || err.message.includes("NetworkError")) {
        errorMessage = `Cannot connect to backend at ${API_BASE}. Check if the backend is running and REACT_APP_API_URL is set correctly.`;
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
  const togglePlaceInList = (listName, place) => {
    setSavedPlaces((prev) => {
      const current = prev[listName] || [];
      const alreadyInList = current.some((p) => p.name === place.name);
      return {
        ...prev,
        [listName]: alreadyInList
          ? current.filter((p) => p.name !== place.name)
          : [...current, place],
      };
    });
  };

  const handleAddNewList = () => {
    const listName = prompt("Add to new list (e.g., NYC Eats):");
    if (!listName) return;
    setSavedPlaces((prev) => ({ ...prev, [listName]: [] }));
  };

  const handleRemoveFromList = (listName, index) => {
    if (!window.confirm(`Remove this place from "${listName}"?`)) return;
    setSavedPlaces((prev) => {
      const updated = [...(prev[listName] || [])];
      updated.splice(index, 1);
      return { ...prev, [listName]: updated };
    });
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
                {result.summary_title && (
                  <h2 className="summary-title">
                    {result.summary_title.replace(/(^"|"$)/g, "")}
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

                <div
                  className={
                    viewMode === "grid" ? "results-grid" : "results-list"
                  }
                >
                  {result.places_extracted?.map((p, i) => {
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
                          <div className="meta">
                            {p.must_try && (
                              <p>
                                <strong>Must Try:</strong> {p.must_try}
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
          📜
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
