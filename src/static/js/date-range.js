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

    // Last Week
    const lastWeekStart = new Date(today);
    const lastWeekOffset = today.getDay() === 0 ? 13 : today.getDay() + 6;
    lastWeekStart.setDate(today.getDate() - lastWeekOffset);
    const lastWeekEnd = new Date(lastWeekStart);
    lastWeekEnd.setDate(lastWeekStart.getDate() + 6);
    if (isSameDay(start, lastWeekStart) && isSameDay(end, lastWeekEnd)) {
      predefinedRange.value = "lastWeek";
      return;
    }

    // This Month
    const thisMonthStart = new Date(today.getFullYear(), today.getMonth(), 1);
    if (isSameDay(start, thisMonthStart) && isSameDay(end, today)) {
      predefinedRange.value = "thisMonth";
      return;
    }

    // Last Month
    const lastMonthStart = new Date(
      today.getFullYear(),
      today.getMonth() - 1,
      1
    );
    const lastMonthEnd = new Date(today.getFullYear(), today.getMonth(), 0);
    if (isSameDay(start, lastMonthStart) && isSameDay(end, lastMonthEnd)) {
      predefinedRange.value = "lastMonth";
      return;
    }

    // This Year
    const thisYearStart = new Date(today.getFullYear(), 0, 1);
    if (isSameDay(start, thisYearStart) && isSameDay(end, today)) {
      predefinedRange.value = "thisYear";
      return;
    }

    // Last Year
    const lastYearStart = new Date(today.getFullYear() - 1, 0, 1);
    const lastYearEnd = new Date(today.getFullYear() - 1, 11, 31);
    if (isSameDay(start, lastYearStart) && isSameDay(end, lastYearEnd)) {
      predefinedRange.value = "lastYear";
      return;
    }

    // All Time
    const allTimeStart = new Date(1900, 0, 1);
    if (isSameDay(start, allTimeStart) && isSameDay(end, today)) {
      predefinedRange.value = "allTime";
      return;
    }

    // If no match found, set to custom range
    predefinedRange.value = "";
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
        // For Monday start, if today.getDay() is 0 (Sunday), we need 6, otherwise subtract 1
        const dayOffset = today.getDay() === 0 ? 6 : today.getDay() - 1;
        start.setDate(today.getDate() - dayOffset);
        break;
      case "lastWeek":
        // For last week starting Monday, we go back to previous Monday
        const lastWeekOffset = today.getDay() === 0 ? 13 : today.getDay() + 6;
        start.setDate(today.getDate() - lastWeekOffset);
        end = new Date(start);
        end.setDate(start.getDate() + 6);
        break;
      case "thisMonth":
        start.setDate(1);
        break;
      case "lastMonth":
        start.setMonth(today.getMonth() - 1);
        start.setDate(1);
        end = new Date(today.getFullYear(), today.getMonth(), 0);
        break;
      case "thisYear":
        start = new Date(today.getFullYear(), 0, 1);
        break;
      case "lastYear":
        start = new Date(today.getFullYear() - 1, 0, 1);
        end = new Date(today.getFullYear() - 1, 11, 31);
        break;
      case "allTime":
        start = new Date(1900, 0, 1);
        break;
      default:
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
