/**
 * CalendarSection.jsx — Financial calendar orchestrator for the Dashboard.
 *
 * Owns all calendar state: event list, current view, current date, and modal
 * open/close. Fetches events from the API on mount and after any mutation.
 * Delegates rendering to CalendarView (pure display) and EventFormModal (form).
 *
 * Auto-seeds standard events (tax deadlines, estimated payments, budget
 * meetings) on first mount. Seed is idempotent — safe to call on every page
 * load because the backend skips already-existing auto-generated events.
 *
 * Architecture:
 *   CalendarSection (this file) — state, fetch, handlers
 *   └── CalendarView            — react-big-calendar wrapper
 *   └── EventFormModal          — create / edit / delete dialog
 */
import { useState, useEffect, useRef, useCallback } from "react";
import { getCalendarEvents, seedCalendarEvents } from "../../api";
import { toRBCEvent } from "./calendarUtils";
import CalendarView from "./CalendarView";
import EventFormModal from "./EventFormModal";

export default function CalendarSection() {
  const [events, setEvents] = useState([]);           // RBC event objects
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // react-big-calendar view / navigation state
  const [view, setView] = useState("month");
  const [currentDate, setCurrentDate] = useState(new Date());

  // Modal state: null = closed, "create" = new, object = edit existing
  const [modalMode, setModalMode] = useState(null);   // null | "create" | event-row
  const [slotStart, setSlotStart] = useState(null);   // Date from slot selection

  // mountedRef: React 18 StrictMode double-invokes effects. Setting the ref
  // at the START of each effect (not just on init) prevents permanent false
  // after the StrictMode cleanup-and-reinvoke cycle.
  const mountedRef = useRef(false);

  /** Fetch all events for a ±2-month window around the current date. */
  const loadEvents = useCallback(async (centerDate = new Date()) => {
    const from = new Date(centerDate.getFullYear(), centerDate.getMonth() - 1, 1);
    const to   = new Date(centerDate.getFullYear(), centerDate.getMonth() + 3, 0);
    const fmt  = (d) => d.toISOString().slice(0, 10);
    try {
      const rows = await getCalendarEvents(fmt(from), fmt(to));
      if (mountedRef.current) {
        setEvents(rows.map(toRBCEvent));
        setError("");
      }
    } catch (e) {
      if (mountedRef.current) setError("Could not load calendar events.");
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;

    async function init() {
      setLoading(true);
      // Seed standard events first (idempotent — backend skips duplicates)
      try { await seedCalendarEvents(); } catch (_) { /* non-fatal */ }
      await loadEvents(currentDate);
      if (mountedRef.current) setLoading(false);
    }

    init();
    return () => { mountedRef.current = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // run once on mount

  /** Reload when user navigates to a different month. */
  function handleNavigate(newDate) {
    setCurrentDate(newDate);
    loadEvents(newDate);
  }

  /** Click on an existing event — open edit modal. */
  function handleSelectEvent(rbcEvent) {
    setModalMode(rbcEvent.resource); // full DB row stored in .resource
    setSlotStart(null);
  }

  /** Click on an empty slot — open create modal pre-filled with that date. */
  function handleSelectSlot({ start }) {
    setModalMode("create");
    setSlotStart(start);
  }

  /** Called by EventFormModal after a successful create or update. */
  function handleSave(_savedEvent) {
    loadEvents(currentDate); // refresh from API to get canonical server data
  }

  /** Called by EventFormModal after a successful delete. */
  function handleDelete(_deletedId) {
    loadEvents(currentDate);
  }

  function closeModal() {
    setModalMode(null);
    setSlotStart(null);
  }

  // Derive modal props
  const modalOpen   = modalMode !== null;
  const initialEvent = modalMode !== null && modalMode !== "create" ? modalMode : null;
  const defaultStart = modalMode === "create" ? slotStart : null;

  return (
    <section className="dashboard-section" style={{ gridColumn: "1 / -1" }}>
      {/* Header row */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <h2 className="section-title" style={{ margin: 0 }}>Financial Calendar</h2>
        <button
          className="btn-primary"
          style={{ fontSize: "0.82rem", padding: "4px 12px" }}
          onClick={() => { setModalMode("create"); setSlotStart(new Date()); }}
        >
          + Add Event
        </button>
      </div>

      {/* Status messages */}
      {loading && <p className="loading-text">Loading calendar…</p>}
      {error   && <p className="error-text">{error}</p>}

      {/* Calendar — render even while loading so RBC doesn't flash layout */}
      {!loading && (
        <CalendarView
          events={events}
          onSelectEvent={handleSelectEvent}
          onSelectSlot={handleSelectSlot}
          view={view}
          onView={setView}
          date={currentDate}
          onNavigate={handleNavigate}
        />
      )}

      {/* Create / Edit modal */}
      <EventFormModal
        open={modalOpen}
        onClose={closeModal}
        onSave={handleSave}
        onDelete={handleDelete}
        initialEvent={initialEvent}
        defaultStart={defaultStart}
      />
    </section>
  );
}
