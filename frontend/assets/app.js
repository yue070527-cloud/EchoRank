import "../components/app-navigation.js";
import "../components/chart-tabs.js";
import "../components/period-selector.js";
import "../components/chart-list.js";
import "../components/bubbling-section.js";
import {
  findView,
  getLatestPeriodKey,
  getSnapshotNavigation,
  loadManifest,
  loadSnapshot,
  mapEntry,
} from "./chart-data.js";

const entityLabels = { songs: "歌曲", albums: "专辑", artists: "艺人" };
const periodLabels = { daily: "日榜", weekly: "周榜", monthly: "月榜", yearly: "年榜" };
const state = { manifest: null, entityType: "songs", periodType: "daily", periodKey: null };

const chartList = document.querySelector("chart-list");
const bubblingSection = document.querySelector("bubbling-section");
const periodSelector = document.querySelector("period-selector");
const status = document.querySelector("[data-chart-status]");

const clearChart = () => {
  chartList.items = [];
  bubblingSection.items = [];
};

const showUnavailable = () => {
  clearChart();
  state.periodKey = null;
  periodSelector.period = {
    title: `${entityLabels[state.entityType]}${periodLabels[state.periodType]}`,
    subtitle: "该视图尚未生成榜单快照",
    status: "unavailable",
    hasPrevious: false,
    hasNext: false,
  };
  status.textContent = `暂无${entityLabels[state.entityType]}${periodLabels[state.periodType]}数据。`;
};

const renderSnapshot = (snapshot, navigation) => {
  const items = snapshot.entries.map(mapEntry).sort((left, right) => left.rank - right.rank);
  chartList.setAttribute("title", snapshot.chart.title);
  chartList.setAttribute("eyebrow", `${entityLabels[state.entityType]}主榜`);
  chartList.items = items.filter((item) => item.rank <= 50);
  bubblingSection.items = items.filter((item) => item.rank > 50);

  const coverage = Math.round(snapshot.collection.coverage * 100);
  const collectedTime = snapshot.collection.collectedAt
    ? new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })
      .format(new Date(snapshot.collection.collectedAt))
    : "未采集";
  periodSelector.period = {
    title: snapshot.period.label,
    subtitle: `采集于 ${collectedTime} · 数据覆盖率 ${coverage}%`,
    status: snapshot.period.status,
    hasPrevious: Boolean(navigation.previous),
    hasNext: Boolean(navigation.next),
  };
  status.textContent = `当前展示：${entityLabels[state.entityType]}${periodLabels[state.periodType]} · ${items.length} 条已发布数据`;
};

const loadCurrentView = async () => {
  status.textContent = "正在加载榜单数据…";
  const view = findView(state.manifest, state.entityType, state.periodType);
  if (!view?.snapshots?.length) {
    showUnavailable();
    return;
  }

  state.periodKey = state.periodKey && view.snapshots.some((item) => item.periodKey === state.periodKey)
    ? state.periodKey
    : getLatestPeriodKey(view);
  const navigation = getSnapshotNavigation(view, state.periodKey);
  const snapshot = await loadSnapshot(navigation.current.path);
  renderSnapshot(snapshot, navigation);
};

document.addEventListener("tab-change", async (event) => {
  if (event.detail.name === "entity") state.entityType = event.detail.value;
  if (event.detail.name === "period") state.periodType = event.detail.value;
  state.periodKey = null;
  try {
    await loadCurrentView();
  } catch (error) {
    clearChart();
    status.textContent = error.message;
    periodSelector.period = { title: "榜单加载失败", subtitle: "请检查数据文件", status: "failed" };
  }
});

document.addEventListener("period-change", async (event) => {
  const view = findView(state.manifest, state.entityType, state.periodType);
  const navigation = getSnapshotNavigation(view, state.periodKey);
  const destination = navigation[event.detail.direction];
  if (!destination) return;
  state.periodKey = destination.periodKey;
  try {
    await loadCurrentView();
  } catch (error) {
    clearChart();
    status.textContent = error.message;
    periodSelector.period = { title: "榜单加载失败", subtitle: "请检查数据文件", status: "failed" };
  }
});

try {
  state.manifest = await loadManifest();
  Object.assign(state, state.manifest.defaultView);
  document.querySelector('chart-tabs[name="entity"]').setAttribute("active", state.entityType);
  document.querySelector('chart-tabs[name="period"]').setAttribute("active", state.periodType);
  await loadCurrentView();
} catch (error) {
  clearChart();
  status.textContent = error.message;
  periodSelector.period = { title: "榜单加载失败", subtitle: "请检查数据文件", status: "failed" };
}
