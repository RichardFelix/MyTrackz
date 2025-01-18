document.addEventListener("DOMContentLoaded", function () {
  const predefinedRange = document.getElementById("predefined-range");
  const startDate = document.getElementById("start-date");
  const endDate = document.getElementById("end-date");

  // Check if the initial dates match a predefined range
  checkPredefinedRange();

  function isSameDay(date1, date2) {
    return (
      date1.getFullYear() === date2.getFullYear() &&
      date1.getMonth() === date2.getMonth() &&
      date1.getDate() === date2.getDate()
    );
  }

  function checkPredefinedRange() {
    const start = new Date(startDate.value);
    const end = new Date(endDate.value);
    const today = new Date();

    // Reset hours to avoid time comparison issues
    start.setHours(0, 0, 0, 0);
    end.setHours(0, 0, 0, 0);
    today.setHours(0, 0, 0, 0);

    // Check each predefined range
    if (isSameDay(start, today) && isSameDay(end, today)) {
      predefinedRange.value = "today";
      return;
    }

    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    if (isSameDay(start, yesterday) && isSameDay(end, yesterday)) {
      predefinedRange.value = "yesterday";
      return;
    }

    // This Week (Monday-Sunday)
    const thisWeekStart = new Date(today);
    const dayOffset = today.getDay() === 0 ? 6 : today.getDay() - 1;
    thisWeekStart.setDate(today.getDate() - dayOffset);
    if (isSameDay(start, thisWeekStart) && isSameDay(end, today)) {
      predefinedRange.value = "thisWeek";
      return;
    }

    // Last 7 days
    const last7DaysStart = new Date(today);
    last7DaysStart.setDate(today.getDate() - 6);
    if (isSameDay(start, last7DaysStart) && isSameDay(end, today)) {
      predefinedRange.value = "last7Days";
      return;
    }

    // This Month
    const thisMonthStart = new Date(today.getFullYear(), today.getMonth(), 1);
    if (isSameDay(start, thisMonthStart) && isSameDay(end, today)) {
      predefinedRange.value = "thisMonth";
      return;
    }

    // Last 30 days
    const last30DaysStart = new Date(today);
    last30DaysStart.setDate(today.getDate() - 29);
    if (isSameDay(start, last30DaysStart) && isSameDay(end, today)) {
      predefinedRange.value = "last30Days";
      return;
    }

    // Last 90 days
    const last90DaysStart = new Date(today);
    last90DaysStart.setDate(today.getDate() - 89);
    if (isSameDay(start, last90DaysStart) && isSameDay(end, today)) {
      predefinedRange.value = "last90Days";
      return;
    }

    // This Year
    const thisYearStart = new Date(today.getFullYear(), 0, 1);
    if (isSameDay(start, thisYearStart) && isSameDay(end, today)) {
      predefinedRange.value = "thisYear";
      return;
    }

    // Last 6 months
    const last6MonthsStart = new Date(today);
    last6MonthsStart.setMonth(today.getMonth() - 6);
    if (isSameDay(start, last6MonthsStart) && isSameDay(end, today)) {
      predefinedRange.value = "last6Months";
      return;
    }

    // Last 12 months
    const last12MonthsStart = new Date(today);
    last12MonthsStart.setMonth(today.getMonth() - 12);
    if (isSameDay(start, last12MonthsStart) && isSameDay(end, today)) {
      predefinedRange.value = "last12Months";
      return;
    }

    // All Time
    const allTimeStart = new Date(1990, 0, 1);
    if (isSameDay(start, allTimeStart) && isSameDay(end, today)) {
      predefinedRange.value = "allTime";
      return;
    }

    // If no match found, set to custom range
    predefinedRange.value = "custom";
  }

  function updateDatesFromPredefined() {
    const today = new Date();
    let start = new Date();
    let end = new Date();

    switch (predefinedRange.value) {
      case "today":
        break;
      case "yesterday":
        start.setDate(today.getDate() - 1);
        end = new Date(start);
        break;
      case "thisWeek":
        const dayOffset = today.getDay() === 0 ? 6 : today.getDay() - 1;
        start.setDate(today.getDate() - dayOffset);
        break;
      case "last7Days":
        start.setDate(today.getDate() - 6);
        break;
      case "thisMonth":
        start.setDate(1);
        break;
      case "last30Days":
        start.setDate(today.getDate() - 29);
        break;
      case "last90Days":
        start.setDate(today.getDate() - 89);
        break;
      case "thisYear":
        start = new Date(today.getFullYear(), 0, 1);
        break;
      case "last6Months":
        start.setMonth(today.getMonth() - 6);
        break;
      case "last12Months":
        start.setMonth(today.getMonth() - 12);
        break;
      case "allTime":
        start = new Date(1990, 0, 1);
        break;
      case "custom":
        // Custom range - don't update the dates
        return;
    }

    startDate.value = formatDate(start);
    endDate.value = formatDate(end);
  }

  function formatDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  // Event Listeners
  predefinedRange.addEventListener("change", function () {
    updateDatesFromPredefined();
  });

  startDate.addEventListener("change", function () {
    checkPredefinedRange();
  });

  endDate.addEventListener("change", function () {
    checkPredefinedRange();
  });
});
