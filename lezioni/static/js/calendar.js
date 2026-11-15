/* Calendar page: FullCalendar with RTL/Arabic support. Loaded after the FullCalendar bundle. */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var calEl = $("#calendar");
  if (calEl && window.FullCalendar) {
    var rtl = document.documentElement.dir === "rtl";
    var lang = document.documentElement.lang;
    var openSlot = $("#open-slot-form");
    var cal = new FullCalendar.Calendar(calEl, {
      locale: lang === "ar" ? "ar-u-nu-latn" : "it",
      direction: rtl ? "rtl" : "ltr",
      firstDay: 1,
      initialView: window.innerWidth < 760 ? "listWeek" : "timeGridWeek",
      headerToolbar: { start: "prev,next today", center: "title", end: "dayGridMonth,timeGridWeek,listWeek" },
      slotMinTime: "07:00:00",
      slotMaxTime: "22:00:00",
      allDaySlot: false,
      nowIndicator: true,
      height: "auto",
      eventTimeFormat: { hour: "2-digit", minute: "2-digit", hour12: false },
      slotLabelFormat: { hour: "2-digit", minute: "2-digit", hour12: false },
      events: calEl.dataset.events,
      eventDidMount: function (info) {
        if (info.event.extendedProps.virtual) {
          info.el.classList.add("is-virtual");
          info.el.style.color = info.event.backgroundColor;
        }
      },
      eventClick: function (info) {
        if (info.event.extendedProps.virtual && openSlot) {
          info.jsEvent.preventDefault();
          openSlot.querySelector('[name="slot_id"]').value = info.event.extendedProps.slot_id;
          openSlot.querySelector('[name="date"]').value = info.event.extendedProps.date;
          openSlot.submit();
        }
      }
    });
    cal.render();
  }
})();
