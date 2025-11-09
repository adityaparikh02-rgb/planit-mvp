import React from "react";

export default function PlanItLogo({ size = 120, showText = true }) {
  const iconSize = showText ? size * 0.4 : size;

  return (
    <div className="flex items-center gap-3">
      <svg
        width={iconSize}
        height={iconSize}
        viewBox="0 0 100 100"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <rect width="100" height="100" rx="22" fill="url(#bgGradient)" />
        <circle cx="50" cy="50" r="32" fill="url(#globeGradient)" opacity="0.08" />
        <ellipse cx="50" cy="50" rx="32" ry="12" stroke="white" strokeWidth="1" opacity="0.15" fill="none" />
        <ellipse cx="50" cy="50" rx="12" ry="32" stroke="white" strokeWidth="1" opacity="0.15" fill="none" />
        <path
          d="M50 28C43.3726 28 38 33.3726 38 40C38 48.5 50 60 50 60C50 60 62 48.5 62 40C62 33.3726 56.6274 28 50 28Z"
          fill="white"
        />
        <circle cx="50" cy="40" r="6" fill="url(#pinGradient)" />
        <circle cx="50" cy="40" r="2.5" fill="white" />
        <defs>
          <linearGradient id="bgGradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#6366f1" />
            <stop offset="100%" stopColor="#4f46e5" />
          </linearGradient>
          <linearGradient id="globeGradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#ffffff" />
            <stop offset="100%" stopColor="#e0e7ff" />
          </linearGradient>
          <linearGradient id="pinGradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#6366f1" />
            <stop offset="100%" stopColor="#4f46e5" />
          </linearGradient>
        </defs>
      </svg>

      {showText && (
        <span
          style={{
            fontWeight: 700,
            fontSize: size * 0.35,
            letterSpacing: "-0.02em",
          }}
        >
          Plan<span style={{ color: "#a5b4fc" }}>It</span>
        </span>
      )}
    </div>
  );
}
