/**
 * 4-tier signal color system for artist evaluation signals.
 *
 * Levels:
 *   strong_positive — clear evidence of legitimacy
 *   weak_positive   — mild positive signal
 *   weak_negative   — mildly concerning
 *   strong_negative — clear red flag
 */

export const SIGNAL_COLORS = {
  strong_positive: '#22c55e',
  weak_positive: '#86efac',
  weak_negative: '#f59e0b',
  strong_negative: '#ef4444',
};

export const SIGNAL_ICONS = {
  strong_positive: '\u2713',  // ✓
  weak_positive: '\u2713',    // ✓
  weak_negative: '\u26A0',    // ⚠
  strong_negative: '\u2717',  // ✗
};

/**
 * Get the signal level from evidence type and strength.
 *
 * @param {string} evidenceType - "red_flag", "green_flag", or "neutral"
 * @param {string} strength - "strong", "moderate", or "weak"
 * @returns {string} one of the 4 signal level keys
 */
export function getSignalLevel(evidenceType, strength) {
  if (evidenceType === 'green_flag') {
    return strength === 'weak' ? 'weak_positive' : 'strong_positive';
  }
  if (evidenceType === 'red_flag') {
    return strength === 'weak' ? 'weak_negative' : 'strong_negative';
  }
  // neutral defaults to weak_negative
  return 'weak_negative';
}

/**
 * Get the color hex for a signal level.
 */
export function getSignalColor(level) {
  return SIGNAL_COLORS[level] || SIGNAL_COLORS.weak_negative;
}

/**
 * Get the icon prefix for a signal level.
 */
export function getSignalIcon(level) {
  return SIGNAL_ICONS[level] || '\u26A0';
}

/**
 * Get color for a score value (0-100) using the 4-tier system.
 */
export function getScoreColor(score) {
  if (score >= 65) return SIGNAL_COLORS.strong_positive;
  if (score >= 40) return SIGNAL_COLORS.weak_positive;
  if (score >= 20) return SIGNAL_COLORS.weak_negative;
  return SIGNAL_COLORS.strong_negative;
}

/**
 * Get color for a verdict string.
 */
export function getVerdictColor(verdict) {
  switch (verdict) {
    case 'Verified Artist':
      return SIGNAL_COLORS.strong_positive;
    case 'Likely Authentic':
      return SIGNAL_COLORS.weak_positive;
    case 'Inconclusive':
    case 'Insufficient Data':
    case 'Conflicting Signals':
      return SIGNAL_COLORS.weak_negative;
    case 'Suspicious':
      return SIGNAL_COLORS.weak_negative;
    case 'Likely Artificial':
      return SIGNAL_COLORS.strong_negative;
    default:
      return '#8b949e';
  }
}

/**
 * Get the badge background color (with transparency) for a verdict.
 */
export function getVerdictBadgeBg(verdict) {
  const color = getVerdictColor(verdict);
  return color + '22';
}
