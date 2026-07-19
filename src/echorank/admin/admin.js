const status = document.querySelector("[data-status]");
const searchForm = document.querySelector('[data-form="netease"]');
const results = document.querySelector("[data-netease-results]");
const bilibiliForm = document.querySelector('[data-form="bilibili"]');
const physicalForm = document.querySelector('[data-form="physical"]');
const adjustmentForm = document.querySelector('[data-form="adjustment"]');
const selectedContainer = document.querySelector("[data-selected-song]");
const adjustmentContainer = document.querySelector("[data-adjustment-song]");
const physicalTracksContainer = document.querySelector("[data-physical-tracks]");
const physicalSummary = document.querySelector("[data-physical-summary]");
const clearPhysicalButton = document.querySelector("[data-clear-physical]");
const successOverlay = document.querySelector("[data-success]");
const successMessage = document.querySelector("[data-success-message]");
let mode = "bilibili";
let selectedSong = null;
let physicalSongs = [];
let periods = [];
let retryKeys = {};

const showStatus = (message) => {
  status.textContent = message;
  status.classList.add("is-visible");
  clearTimeout(showStatus.timer);
  showStatus.timer = setTimeout(() => status.classList.remove("is-visible"), 4000);
};

const showSuccess = (message) => {
  successMessage.textContent = message;
  successOverlay.hidden = false;
  clearTimeout(showSuccess.timer);
  showSuccess.timer = setTimeout(() => { successOverlay.hidden = true; }, 1200);
};

const clearSelection = () => {
  selectedSong = null;
  physicalSongs = [];
  retryKeys = {};
  selectedContainer.classList.add("is-empty");
  adjustmentContainer.classList.add("is-empty");
  selectedContainer.textContent = "请先选择一首歌曲";
  adjustmentContainer.textContent = "请先选择一首歌曲";
  results.replaceChildren();
  searchForm.reset();
  renderPhysicalTracks();
  updateAvailability();
};

const request = async (path, options = {}) => {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || "请求失败");
  return body;
};

const post = (path, payload) => request(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
});

const songCard = (song, actions = []) => {
  const card = document.createElement("article");
  card.className = "song-card";
  const cover = document.createElement("div");
  cover.className = "song-card__cover";
  cover.style.backgroundColor = song.coverColor || "#777777";
  if (song.coverUrl) {
    const image = document.createElement("img");
    image.src = song.coverUrl;
    image.alt = "";
    cover.append(image);
  }
  const info = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = song.title;
  const artists = document.createElement("span");
  artists.textContent = song.artists.map((artist) => artist.name).join(" / ");
  const album = document.createElement("small");
  album.textContent = song.album.title;
  info.append(title, artists, album);
  card.append(cover, info);
  if (actions.length) {
    const container = document.createElement("div");
    container.className = "song-card__actions";
    for (const action of actions) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = action.label;
      button.addEventListener("click", action.onClick);
      container.append(button);
    }
    card.append(container);
  }
  return card;
};

const dateIsFrozen = (value) => periods.some((period) => period.periodKey === value && period.frozen);

const updateAvailability = () => {
  const bilibiliFrozen = dateIsFrozen(bilibiliForm.elements.periodKey.value);
  const physicalFrozen = dateIsFrozen(physicalForm.elements.purchaseDate.value);
  const adjustmentFrozen = dateIsFrozen(adjustmentForm.elements.periodKey.value);
  document.querySelector("[data-freeze-warning]").hidden = !bilibiliFrozen;
  document.querySelector("[data-physical-freeze-warning]").hidden = !physicalFrozen;
  document.querySelector("[data-adjustment-freeze-warning]").hidden = !adjustmentFrozen;
  document.querySelector("[data-bilibili-submit]").disabled = !selectedSong || bilibiliFrozen;
  document.querySelector("[data-physical-submit]").disabled = !physicalSongs.length || physicalFrozen;
  document.querySelector("[data-adjustment-submit]").disabled = !selectedSong || adjustmentFrozen;
};

const physicalPreviewWeight = () => {
  const quantity = Math.max(1, Number(physicalForm.elements.quantity.value) || 1);
  const copyWeights = [1, 0.3, 0.15, 0.1];
  return Array.from({ length: quantity }, (_, index) => copyWeights[Math.min(index, 3)])
    .reduce((total, value) => total + value, 0);
};

const updatePhysicalPreview = () => {
  document.querySelector("[data-physical-preview]").textContent =
    `${physicalSongs.length} 首曲目 · 本次基础购买权重 ${physicalPreviewWeight().toFixed(2)}`;
};

const renderPhysicalTracks = () => {
  physicalTracksContainer.replaceChildren();
  physicalTracksContainer.classList.toggle("is-empty", !physicalSongs.length);
  clearPhysicalButton.disabled = !physicalSongs.length;
  physicalSummary.textContent = physicalSongs.length
    ? `${physicalSongs[0].album.title} · 已选 ${physicalSongs.length} 首`
    : "尚未选择专辑";
  updatePhysicalPreview();
  if (!physicalSongs.length) {
    physicalTracksContainer.textContent = "尚未添加曲目";
    return;
  }
  physicalTracksContainer.append(...physicalSongs.map((song) => songCard(song, [{
    label: "移除",
    onClick: () => {
      physicalSongs = physicalSongs.filter((item) => item.id !== song.id);
      retryKeys.physical = null;
      renderPhysicalTracks();
      updateAvailability();
    },
  }])));
};

const loadAlbum = async (album) => {
  results.textContent = "正在载入专辑曲目…";
  try {
    const { songs } = await request(`/api/admin/netease/albums/${encodeURIComponent(album.id)}/tracks`);
    physicalSongs = songs;
    retryKeys.physical = null;
    renderPhysicalTracks();
    renderResults(songs);
    updateAvailability();
    showStatus(`已载入《${album.title}》的 ${songs.length} 首曲目`);
  } catch (error) {
    results.textContent = "";
    showStatus(error.message);
  }
};

const selectSong = (song) => {
  retryKeys = {};
  if (mode === "physical") {
    if (physicalSongs.length && physicalSongs[0].album.id !== song.album.id) {
      physicalSongs = [];
    }
    if (!physicalSongs.some((item) => item.id === song.id)) physicalSongs.push(song);
    renderPhysicalTracks();
  } else {
    selectedSong = song;
    selectedContainer.classList.remove("is-empty");
    adjustmentContainer.classList.remove("is-empty");
    selectedContainer.replaceChildren(songCard(song));
    adjustmentContainer.replaceChildren(songCard(song));
  }
  updateAvailability();
};

const renderResults = (songs) => {
  results.replaceChildren();
  if (!songs.length) {
    results.textContent = "未找到匹配歌曲";
    return;
  }
  results.append(...songs.map((song) => {
    const actions = mode === "physical"
      ? [
          { label: "载入专辑", onClick: () => loadAlbum(song.album) },
          { label: "仅加此曲", onClick: () => selectSong(song) },
        ]
      : [{ label: "选择", onClick: () => selectSong(song) }];
    return songCard(song, actions);
  }));
};

const loadPeriods = async () => {
  ({ periods } = await request("/api/admin/periods"));
  const container = document.querySelector("[data-periods]");
  container.replaceChildren();
  if (!periods.length) container.textContent = "暂无周期记录";
  for (const period of periods) {
    const row = document.createElement("div");
    row.className = "period";
    const label = document.createElement("span");
    label.textContent = period.periodKey;
    const state = document.createElement("strong");
    state.textContent = period.frozen ? "已结算" : "可录入";
    row.append(label, state);
    container.append(row);
  }
  updateAvailability();
};

const bilibiliPreview = (count) => {
  if (count <= 0) return 0;
  if (count === 1) return 20;
  if (count === 2) return 35;
  if (count <= 4) return 55;
  if (count <= 7) return 80;
  if (count <= 12) return 110;
  if (count <= 20) return 145;
  if (count <= 35) return 180;
  if (count <= 60) return 215;
  return 250;
};

searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = searchForm.querySelector("button");
  button.disabled = true;
  results.textContent = "正在搜索…";
  try {
    const { songs } = await request(`/api/admin/netease/search?query=${encodeURIComponent(searchForm.elements.query.value)}`);
    renderResults(songs);
  } catch (error) {
    results.textContent = "";
    showStatus(error.message);
  } finally {
    button.disabled = false;
  }
});

document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => {
  mode = button.dataset.mode;
  document.querySelectorAll("[data-mode]").forEach((item) => item.classList.toggle("is-active", item === button));
  document.querySelectorAll("[data-panel]").forEach((panel) => { panel.hidden = panel.dataset.panel !== mode; });
  renderResults([]);
}));

bilibiliForm.elements.viewCount.addEventListener("input", () => {
  document.querySelector("[data-bilibili-preview]").textContent = bilibiliPreview(Number(bilibiliForm.elements.viewCount.value));
});

bilibiliForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedSong) return;
  retryKeys.bilibili ||= `bilibili-event:${crypto.randomUUID()}`;
  try {
    const result = await post("/api/admin/bilibili/events", {
      song: selectedSong,
      periodKey: bilibiliForm.elements.periodKey.value,
      viewCount: Number(bilibiliForm.elements.viewCount.value),
      videoRef: bilibiliForm.elements.videoRef.value,
      notes: bilibiliForm.elements.notes.value,
      externalKey: retryKeys.bilibili,
    });
    retryKeys.bilibili = null;
    showSuccess(`《${result.song.title}》观看数据已记录`);
    bilibiliForm.reset();
    bilibiliForm.elements.periodKey.value = today;
    document.querySelector("[data-bilibili-preview]").textContent = "0";
    clearSelection();
    await loadPeriods();
  } catch (error) { showStatus(error.message); }
});

physicalForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  retryKeys.physical ||= `physical-event:${crypto.randomUUID()}`;
  try {
    await post("/api/admin/physical/events", {
      songs: physicalSongs,
      selectedSongIds: physicalSongs.map((song) => song.id),
      purchaseDate: physicalForm.elements.purchaseDate.value,
      format: physicalForm.elements.format.value,
      editionLabel: physicalForm.elements.editionLabel.value,
      quantity: Number(physicalForm.elements.quantity.value),
      notes: physicalForm.elements.notes.value,
      externalKey: retryKeys.physical,
    });
    retryKeys.physical = null;
    showSuccess("实体购买已记录，28 天周期已建立");
    physicalForm.reset();
    physicalForm.elements.purchaseDate.value = today;
    physicalForm.elements.quantity.value = "1";
    clearSelection();
    await loadPeriods();
  } catch (error) { showStatus(error.message); }
});

adjustmentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedSong) return;
  retryKeys.adjustment ||= `manual-adjustment:${crypto.randomUUID()}`;
  try {
    await post("/api/admin/manual-adjustments", {
      song: selectedSong,
      periodKey: adjustmentForm.elements.periodKey.value,
      points: Number(adjustmentForm.elements.points.value),
      reason: adjustmentForm.elements.reason.value,
      externalKey: retryKeys.adjustment,
    });
    retryKeys.adjustment = null;
    showSuccess("人工修正已记录");
    adjustmentForm.reset();
    adjustmentForm.elements.periodKey.value = today;
    clearSelection();
    await loadPeriods();
  } catch (error) { showStatus(error.message); }
});

physicalForm.elements.quantity.addEventListener("input", updatePhysicalPreview);
clearPhysicalButton.addEventListener("click", () => {
  physicalSongs = [];
  retryKeys.physical = null;
  renderPhysicalTracks();
  updateAvailability();
});

[bilibiliForm.elements.periodKey, physicalForm.elements.purchaseDate, adjustmentForm.elements.periodKey]
  .forEach((input) => input.addEventListener("change", updateAvailability));

const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date());
bilibiliForm.elements.periodKey.value = today;
physicalForm.elements.purchaseDate.value = today;
adjustmentForm.elements.periodKey.value = today;
renderPhysicalTracks();
loadPeriods().catch((error) => showStatus(error.message));
