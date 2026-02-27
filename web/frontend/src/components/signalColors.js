/**
 * Global color constants and 4-tier signal system for artist evaluation.
 *
 * UI Spec Round 2 — single source of truth for all colors.
 *
 * Verdict colors:
 *   Verified Artist:        #22c55e (green)
 *   Likely Authentic:       #86efac (light green)
 *   Inconclusive:           #fbbf24 (amber)
 *   Suspicious:             #f97316 (orange)
 *   Likely Artificial:      #ef4444 (red)
 *   Not Scanned / No Data:  #9ca3af (gray)
 *   Blocklist Hit:          #ef4444 (red, binary)
 *
 * Per-category score thresholds (4-tier):
 *   70-100: Green    #22c55e  (positive signals dominate)
 *   40-69:  Lt Green #86efac  (more positive than negative)
 *   15-39:  Orange   #f97316  (more negative than positive)
 *   0-14:   Red      #ef4444  (strong negative signals)
 *   0 (no data): Gray #9ca3af (not red — no data ≠ negative)
 *
 * Accessibility indicators alongside color:
 *   Green:     ✓ checkmark
 *   Lt Green:  ○ open circle
 *   Orange:    △ triangle
 *   Red:       ✗ cross
 */

// ---------------------------------------------------------------------------
// Verdict colors (global constants)
// ---------------------------------------------------------------------------

export const VERDICT_COLORS = {
  'Verified Artist':    '#22c55e',
  'Likely Authentic':   '#86efac',
  'Inconclusive':       '#fbbf24',
  'Insufficient Data':  '#fbbf24',
  'Conflicting Signals':'#fbbf24',
  'Suspicious':         '#f97316',
  'Likely Artificial':  '#ef4444',
  'Not Scanned':        '#9ca3af',
};

// ---------------------------------------------------------------------------
// 4-tier signal colors
// ---------------------------------------------------------------------------

export const SIGNAL_COLORS = {
  strong_positive: '#22c55e',
  weak_positive:   '#86efac',
  weak_negative:   '#f97316',
  strong_negative: '#ef4444',
  no_data:         '#9ca3af',
};

// Accessibility indicators (shape + color for colorblind users)
export const SIGNAL_ICONS = {
  strong_positive: '\u2713',  // ✓ checkmark
  weak_positive:   '\u25CB',  // ○ open circle
  weak_negative:   '\u25B3',  // △ triangle
  strong_negative: '\u2717',  // ✗ cross
  no_data:         '\u2014',  // — em dash
};

/**
 * Get the signal level from evidence type and strength.
 */
export function getSignalLevel(evidenceType, strength) {
  if (evidenceType === 'green_flag') {
    return strength === 'weak' ? 'weak_positive' : 'strong_positive';
  }
  if (evidenceType === 'red_flag') {
    return strength === 'weak' ? 'weak_negative' : 'strong_negative';
  }
  return 'weak_negative';
}

/**
 * Get the color hex for a signal level.
 */
export function getSignalColor(level) {
  return SIGNAL_COLORS[level] || SIGNAL_COLORS.weak_negative;
}

/**
 * Get the accessibility icon for a signal level.
 */
export function getSignalIcon(level) {
  return SIGNAL_ICONS[level] || '\u25B3';
}

/**
 * Get color for a category score (0-100) using the 4-tier system.
 * Special: 0 with no data → gray (not red).
 */
export function getScoreColor(score, hasData = true) {
  if (score === 0 && !hasData) return SIGNAL_COLORS.no_data;
  if (score >= 70) return SIGNAL_COLORS.strong_positive;
  if (score >= 40) return SIGNAL_COLORS.weak_positive;
  if (score >= 15) return SIGNAL_COLORS.weak_negative;
  return SIGNAL_COLORS.strong_negative;
}

/**
 * Get the accessibility icon for a category score.
 */
export function getScoreIcon(score, hasData = true) {
  if (score === 0 && !hasData) return SIGNAL_ICONS.no_data;
  if (score >= 70) return SIGNAL_ICONS.strong_positive;
  if (score >= 40) return SIGNAL_ICONS.weak_positive;
  if (score >= 15) return SIGNAL_ICONS.weak_negative;
  return SIGNAL_ICONS.strong_negative;
}

/**
 * Get color for a blocklist score. Binary: 100 = green, <100 = red.
 */
export function getBlocklistColor(score) {
  return score >= 100 ? SIGNAL_COLORS.strong_positive : SIGNAL_COLORS.strong_negative;
}

/**
 * Get color for a verdict string.
 */
export function getVerdictColor(verdict) {
  return VERDICT_COLORS[verdict] || '#9ca3af';
}

/**
 * Get the badge background color (with transparency) for a verdict.
 */
export function getVerdictBadgeBg(verdict) {
  const color = getVerdictColor(verdict);
  return color + '22';
}
