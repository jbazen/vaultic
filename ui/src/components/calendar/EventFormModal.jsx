/**
 * EventFormModal.jsx — Create / edit a financial calendar event.
 *
 * Self-managing form state. Parent controls only open/close via props.
 * Handles both new events (no initialEvent) and edits (initialEvent provided).
 *
 * Props:
 *   open          {boolean}      Whether the modal is visible
 *   onClose       {Function}     Close without saving
 *   onSave        {Function}     Called with the created/updated event object
 *   onDelete      {Function}     Called with event id (edit mode only)
 *   initialEvent  {object|null}  Existing DB event row for edit; null for create
 *   defaultStart  {Date|null}    Pre-fill start date (from slot selection)
 */
import { useState, useEffect } from "react";
import {
  createCalendarEvent,
  updateCalendarEvent,
  deleteCalendarEvent,
} from "../../api";
import { EVENT_TYPES, RECURRING_OPTIONS, formatEventDts } from "./calendarUtils";

/** Format a Date to "YYYY-MM-DD" for <input type="date"> */
function toDateInput(d) {
  if (!d) return "";
  const date = d instanceof Date ? d : new Date(d);
  return date.toISOString().slice(0, 10);
}

/** Format a Date to "HH:MM" for <input type="time"> */
function toTimeInput(d) {
  if (!d) return "";
  const date = d instanceof Date ? d : new Date(d);
  return date.toTimeString().slice(0, 5);
}

/** Build initial form state from an existing event row or defaults. */
function buildInitial(ev, defaultStart) {
  if (ev) {
    // Edit mode — populate from existing event
    const isAllDay = !!ev.all_day;
    const startDate = ev.start_dt ? ev.start_dt.slice(0, 10) : "";
    const startTime = (!isAllDay && ev.start_dt?.includes("T"))
      ? ev.start_dt.slice(11, 16)
      : "09:00";
    const endDate = ev.end_dt ? ev.end_dt.slice(0, 10) : startDate;
    const endTime = (!isAllDay && ev.end_dt?.includes("T"))
      ? ev.end_dt.slice(11, 16)
      : "10:00";
    return {
      title: ev.title || "",
      description: ev.description || "",
      eventType: ev.event_type || "custom",
      allDay: isAllDay,
      startDate,
      startTime,
      endDate,
      endTime,
      recurring: ev.recurring || "none",
      reminderDays: ev.reminder_days_before ?? 3,
    };
  }

  // Create mode — use defaultStart if provided, otherwise today
  const base = defaultStart instanceof Date ? defaultStart : new Date();
  return {
    title: "",
    description: "",
    eventType: "custom",
    allDay: true,
    startDate: toDateInput(base),
    startTime: "09:00",
    endDate: toDateInput(base),
    endTime: "10:00",
    recurring: "none",
    reminderDays: 3,
  };
}

export default function EventFormModal({
  open,
  onClose,
  onSave,
  onDelete,
  initialEvent,
  defaultStart,
}) {
  const isEdit = !!initialEvent;
  const [form, setForm] = useState(() => buildInitial(initialEvent, defaultStart));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // Reset form whenever the modal opens (handles reuse for create vs edit)
  useEffect(() => {
    if (open) {
      setForm(buildInitial(initialEvent, defaultStart));
      setError("");
    }
  }, [open, initialEvent, defaultStart]);

  if (!open) return null;

  /** Generic field updater — works for all controlled inputs. */
  function set(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSave() {
    if (!form.title.trim()) {
      setError("Title is required.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const dts = formatEventDts(form);
      const payload = {
        title: form.title.trim(),
        description: form.description.trim() || null,
        event_type: form.eventType,
        recurring: form.recurring,
        reminder_days_before: Number(form.reminderDays),
        ...dts,
      };

      if (isEdit) {
        await updateCalendarEvent(initialEvent.id, payload);
        onSave({ ...initialEvent, ...payload });
      } else {
        const res = await createCalendarEvent(payload);
        onSave({ id: res.id, ...payload });
      }
      onClose();
    } catch (e) {
      setError(e.message || "Failed to save event.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!window.confirm("Delete this event?")) return;
    setSaving(true);
    try {
      await deleteCalendarEvent(initialEvent.id);
      onDelete(initialEvent.id);
      onClose();
    } catch (e) {
      setError(e.message || "Failed to delete event.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-box"
        style={{ maxWidth: 480 }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="modal-title">{isEdit ? "Edit Event" : "New Event"}</h2>

        {error && <div className="banner-error" style={{ marginBottom: 12 }}>{error}</div>}

        {/* Title */}
        <label className="form-label">Title *</label>
        <input
          className="form-input"
          value={form.title}
          onChange={(e) => set("title", e.target.value)}
          placeholder="Event title"
          maxLength={120}
        />

        {/* Event type */}
        <label className="form-label">Type</label>
        <select
          className="form-select"
          value={form.eventType}
          onChange={(e) => set("eventType", e.target.value)}
        >
          {EVENT_TYPES.map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </select>

        {/* All-day toggle */}
        <label className="form-label" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="checkbox"
            checked={form.allDay}
            onChange={(e) => set("allDay", e.target.checked)}
          />
          All-day event
        </label>

        {/* Date / time fields */}
        <div style={{ display: "flex", gap: 12, marginTop: 8 }}>
          <div style={{ flex: 1 }}>
            <label className="form-label">Start date</label>
            <input
              type="date"
              className="form-input"
              value={form.startDate}
              onChange={(e) => set("startDate", e.target.value)}
            />
          </div>
          {!form.allDay && (
            <div style={{ flex: 1 }}>
              <label className="form-label">Start time</label>
              <input
                type="time"
                className="form-input"
                value={form.startTime}
                onChange={(e) => set("startTime", e.target.value)}
              />
            </div>
          )}
        </div>

        {!form.allDay && (
          <div style={{ display: "flex", gap: 12, marginTop: 8 }}>
            <div style={{ flex: 1 }}>
              <label className="form-label">End date</label>
              <input
                type="date"
                className="form-input"
                value={form.endDate}
                onChange={(e) => set("endDate", e.target.value)}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label className="form-label">End time</label>
              <input
                type="time"
                className="form-input"
                value={form.endTime}
                onChange={(e) => set("endTime", e.target.value)}
              />
            </div>
          </div>
        )}

        {/* Recurring */}
        <label className="form-label" style={{ marginTop: 12 }}>Repeat</label>
        <select
          className="form-select"
          value={form.recurring}
          onChange={(e) => set("recurring", e.target.value)}
        >
          {RECURRING_OPTIONS.map((r) => (
            <option key={r.value} value={r.value}>{r.label}</option>
          ))}
        </select>

        {/* Reminder */}
        <label className="form-label" style={{ marginTop: 12 }}>Reminder (days before)</label>
        <input
          type="number"
          className="form-input"
          min={0}
          max={60}
          value={form.reminderDays}
          onChange={(e) => set("reminderDays", e.target.value)}
          style={{ width: 80 }}
        />

        {/* Description */}
        <label className="form-label" style={{ marginTop: 12 }}>Notes</label>
        <textarea
          className="form-input"
          rows={2}
          value={form.description}
          onChange={(e) => set("description", e.target.value)}
          placeholder="Optional notes"
          style={{ resize: "vertical" }}
        />

        {/* Actions */}
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 20 }}>
          {isEdit ? (
            <button
              className="btn-danger"
              onClick={handleDelete}
              disabled={saving}
            >
              Delete
            </button>
          ) : (
            <span />
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn-secondary" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button className="btn-primary" onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : isEdit ? "Save" : "Create"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
