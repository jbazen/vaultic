/**
 * CalendarView.jsx — Pure display wrapper around react-big-calendar.
 *
 * Receives all data and callbacks from CalendarSection (parent). Owns no
 * state. Handles color-coding via eventPropGetter using hex values (CSS
 * variables do not resolve in react-big-calendar inline styles).
 *
 * Props:
 *   events       {Array}    RBC event objects (from toRBCEvent())
 *   onSelectEvent {Function} Called when user clicks an existing event
 *   onSelectSlot  {Function} Called when user clicks an empty slot (create new)
 *   view         {string}   "month" | "week" | "day"
 *   onView       {Function} Called when user switches view tabs
 *   date         {Date}     Currently displayed date
 *   onNavigate   {Function} Called when user navigates (prev/next/today)
 */
import { Calendar } from "react-big-calendar";
import "react-big-calendar/lib/css/react-big-calendar.css";
import { localizer, eventTypeHex } from "./calendarUtils";

/**
 * Returns inline style overrides for each RBC event pill.
 * react-big-calendar requires hex/rgb values here — CSS variables like
 * var(--red) are not resolved inside inline style objects.
 */
function eventPropGetter(event) {
  const hex = eventTypeHex(event.resource?.event_type);
  return {
    style: {
      backgroundColor: hex,
      borderColor: hex,
      color: "#0d0f14",       // --bg: dark text over coloured pill
      borderRadius: "4px",
      fontSize: "0.78rem",
      fontWeight: 600,
    },
  };
}

/** Applies inline colour to agenda-view event cells (different DOM path). */
function eventWrapperStyle(event) {
  const hex = eventTypeHex(event.resource?.event_type);
  return { style: { borderLeft: `4px solid ${hex}` } };
}

export default function CalendarView({
  events,
  onSelectEvent,
  onSelectSlot,
  view,
  onView,
  date,
  onNavigate,
}) {
  return (
    <div className="calendar-rbc-wrapper">
      <Calendar
        localizer={localizer}
        events={events}
        view={view}
        onView={onView}
        date={date}
        onNavigate={onNavigate}
        onSelectEvent={onSelectEvent}
        onSelectSlot={onSelectSlot}
        selectable                      /* enables click-on-slot to create */
        eventPropGetter={eventPropGetter}
        /* Provide agenda row style via components.eventWrapper */
        components={{ eventWrapper: ({ event, children }) => (
          <div {...eventWrapperStyle(event)}>{children}</div>
        )}}
        views={["month", "week", "day"]}
        defaultView="month"
        style={{ height: 600 }}
        popup                           /* month view: "+N more" opens popup */
        showMultiDayTimes               /* timed events show time in week/day */
        step={30}                       /* 30-min slot intervals in week/day */
        timeslots={2}                   /* 2 slots per step (30min each) */
      />
    </div>
  );
}
