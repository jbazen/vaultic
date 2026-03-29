/**
 * calendarUtils.js — Shared helpers for the financial calendar.
 *
 * Provides the react-big-calendar localizer, event type metadata,
 * and conversion between DB event rows and RBC event objects.
 */
import { dateFnsLocalizer } from "react-big-calendar";
import { format, parse, startOfWeek, getDay } from "date-fns";
import { enUS } from "date-fns/locale";

/** react-big-calendar localizer using date-fns (Sunday week start) */
export const localizer = dateFnsLocalizer({
  format,
  parse,
  startOfWeek: () => startOfWeek(new Date(), { weekStartsOn: 0 }),
  getDay,
  locales: { "en-US": enUS },
});

/** All supported event types with display labels */
export const EVENT_TYPES = [
  { value: "tax_deadline",   label: "Tax Deadline" },
  { value: "estimated_tax",  label: "Estimated Tax Payment" },
  { value: "payday",         label: "Payday" },
  { value: "budget_meeting", label: "Budget Meeting" },
  { value: "statement_drop", label: "Statement Drop" },
  { value: "custom",         label: "Custom" },
];

/** Recurring interval options for the event form */
export const RECURRING_OPTIONS = [
  { value: "none",      label: "Does not repeat" },
  { value: "weekly",    label: "Weekly" },
  { value: "monthly",   label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "annually",  label: "Annually" },
];

/** CSS variable colors for each event type — matches the dark theme palette */
const EVENT_TYPE_COLORS = {
  tax_deadline:   "var(--red)",
  estimated_tax:  "var(--orange)",
  payday:         "var(--green)",
  budget_meeting: "var(--purple)",
  statement_drop: "var(--accent)",
  custom:         "var(--text2)",
};

/** Hex fallbacks for react-big-calendar (CSS vars don't work inline on RBC elements) */
const EVENT_TYPE_HEX = {
  tax_deadline:   "#f87171",
  estimated_tax:  "#fb923c",
  payday:         "#34d399",
  budget_meeting: "#a78bfa",
  statement_drop: "#4f8ef7",
  custom:         "#8b92a8",
};

/** Return the CSS variable color string for a given event type */
export function eventTypeColor(type) {
  return EVENT_TYPE_COLORS[type] || EVENT_TYPE_COLORS.custom;
}

/** Return the hex color for react-big-calendar inline styles */
export function eventTypeHex(type) {
  return EVENT_TYPE_HEX[type] || EVENT_TYPE_HEX.custom;
}

/** Return the display label for a given event type value */
export function eventTypeLabel(type) {
  return EVENT_TYPES.find((t) => t.value === type)?.label ?? "Custom";
}

/**
 * Convert a DB financial_events row into a react-big-calendar event object.
 *
 * RBC requires `start` and `end` as JS Date objects. For all-day events,
 * we set start to midnight and end to the following midnight so RBC renders
 * them correctly in month and week views.
 */
export function toRBCEvent(ev) {
  // Parse start — handle both "YYYY-MM-DD" and "YYYY-MM-DDTHH:MM:SS"
  const startStr = ev.start_dt.includes("T")
    ? ev.start_dt
    : `${ev.start_dt}T00:00:00`;
  const start = new Date(startStr);

  let end;
  if (ev.end_dt) {
    const endStr = ev.end_dt.includes("T") ? ev.end_dt : `${ev.end_dt}T00:00:00`;
    end = new Date(endStr);
  } else if (ev.all_day) {
    // All-day single-day event — end is next day midnight for correct RBC rendering
    end = new Date(start.getTime() + 24 * 60 * 60 * 1000);
  } else {
    // Timed event with no end — default 1 hour
    end = new Date(start.getTime() + 60 * 60 * 1000);
  }

  return {
    id: ev.id,
    title: ev.title,
    start,
    end,
    allDay: !!ev.all_day,
    resource: ev, // full DB row, accessible in event handlers
  };
}

/**
 * Format a Date object into the ISO strings expected by the API.
 * Returns { start_dt, end_dt, all_day } ready to pass to createCalendarEvent.
 */
export function formatEventDts({ allDay, startDate, startTime, endDate, endTime }) {
  if (allDay) {
    return { start_dt: startDate, end_dt: null, all_day: true };
  }
  return {
    start_dt: `${startDate}T${startTime}:00`,
    end_dt: endDate && endTime ? `${endDate}T${endTime}:00` : null,
    all_day: false,
  };
}
