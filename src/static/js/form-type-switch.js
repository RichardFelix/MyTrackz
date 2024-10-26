document.addEventListener("DOMContentLoaded", function () {
  const mediaTypeSelect = document.getElementById("id_media_type");
  const parentTVField = document.getElementById("id_parent_tv");
  const parentSeasonField = document.getElementById("id_parent_season");
  const seasonNumberField = document.getElementById("id_season_number");
  const episodeNumberField = document.getElementById("id_episode_number");

  function updateFieldVisibility() {
    const selectedMediaType = mediaTypeSelect.value;

    if (selectedMediaType === "season") {
      parentTVField.parentNode.style.display = "block";
      parentTVField.required = true;
      parentSeasonField.parentNode.style.display = "none";
      parentSeasonField.required = false;
      seasonNumberField.parentNode.style.display = "block";
      seasonNumberField.required = true;
    } else if (selectedMediaType === "episode") {
      parentTVField.parentNode.style.display = "none";
      parentTVField.required = false;
      parentSeasonField.parentNode.style.display = "block";
      parentSeasonField.required = true;
      seasonNumberField.parentNode.style.display = "block";
      episodeNumberField.parentNode.style.display = "block";
      seasonNumberField.required = true;
      episodeNumberField.required = true;
    } else {
      parentTVField.parentNode.style.display = "none";
      parentTVField.required = false;
      parentSeasonField.parentNode.style.display = "none";
      parentSeasonField.required = false;
      seasonNumberField.parentNode.style.display = "none";
      seasonNumberField.required = false;
      episodeNumberField.parentNode.style.display = "none";
      episodeNumberField.required = false;
    }
  }
  mediaTypeSelect.addEventListener("change", updateFieldVisibility);
  updateFieldVisibility(); // Call once to set initial state
});
