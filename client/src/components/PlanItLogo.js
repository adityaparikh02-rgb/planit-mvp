import React from "react";

export default function PlanItLogo({ size = 120, showText = true, className = "" }) {
  const iconSize = showText ? size * 0.4 : size;

  return (
    <div className={`flex items-center gap-3 ${className}`} style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
      {/* Icon */}
      <svg
        width={iconSize}
        height={iconSize}
        viewBox="0 0 100 100"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        {/* Background */}
        <rect width="100" height="100" rx="22" fill="#4F46E5" />

        {/* Globe - circular with grid - white and extra bold */}
        <g opacity="0.5">
          {/* Main circle */}
          <circle cx="50" cy="50" r="36" stroke="#FFFFFF" strokeWidth="3.5" fill="none" />

          {/* Vertical longitude lines */}
          <ellipse cx="50" cy="50" rx="10" ry="36" stroke="#FFFFFF" strokeWidth="3" fill="none" />
          <ellipse cx="50" cy="50" rx="20" ry="36" stroke="#FFFFFF" strokeWidth="3" fill="none" />
          <ellipse cx="50" cy="50" rx="28" ry="36" stroke="#FFFFFF" strokeWidth="2.5" fill="none" />

          {/* Horizontal latitude lines */}
          <ellipse cx="50" cy="50" rx="36" ry="10" stroke="#FFFFFF" strokeWidth="3" fill="none" />
          <ellipse cx="50" cy="50" rx="36" ry="20" stroke="#FFFFFF" strokeWidth="3" fill="none" />
          <ellipse cx="50" cy="50" rx="36" ry="28" stroke="#FFFFFF" strokeWidth="2.5" fill="none" />
        </g>

        {/* 3D Map Pin - black and fully opaque */}
        <g opacity="0.8">
          {/* Pin shadow/base */}
          <ellipse cx="50" cy="72" rx="5" ry="1.5" fill="#333333" opacity="0.3" />

          {/* Pin body - teardrop shape - taller and skinnier */}
          <path
            d="M 50 30 C 43 30 37 36 37 43 C 37 49 40 54 50 72 C 60 54 63 49 63 43 C 63 36 57 30 50 30 Z"
            fill="#333333"
          />

          {/* Inner circle for 3D effect */}
          <circle cx="50" cy="43" r="6" fill="#2a2a2a" />

          {/* Center dot - slightly different color */}
          <circle cx="50" cy="43" r="3" fill="#1a1a1a" />
        </g>
      </svg>

      {/* Text */}
      {showText && (
        <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1 }}>
          <span
            style={{
              fontSize: size * 0.35,
              fontWeight: 700,
              letterSpacing: "-0.02em",
              color: "#fff",
            }}
          >
            Plan<span style={{ color: "#4F46E5" }}>It</span>
          </span>
        </div>
      )}
    </div>
  );
}
