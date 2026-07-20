import "../components/auth-gate.js";
import "../components/netease-onboarding.js";
import "../components/app-navigation.js";
import "../components/chart-tabs.js";
import "../components/period-selector.js";
import "../components/chart-list.js";
import "../components/bubbling-section.js";
import "../components/aggregate-trend.js";
import "../components/trend-detail.js";
import "../components/settlement-reveal.js";
import "../components/settlement-archive.js";
import {
  buildAggregateTrend,
  buildTrendSeries,
  clearChartDataCache,
  findView,
  getLatestPeriodKey,
  getSnapshotNavigation,
  loadManifest,
  loadSnapshot,
  loadTrendHistory,
  mapEntry,
} from "./chart-data.js";
import { supabase } from "./supabase.js";

const entityLabels = { songs: "歌曲", albums: "专辑", artists: "艺人" };
const periodLabels = { daily: "日榜", weekly: "周榜", monthly: "月榜", yearly: "年榜" };
const state = {
  manifest: null,
  entityType: "songs",
  periodType: "daily",
  periodKey: null,
  mode: "charts",
  topN: 50,
  chartRequest: 0,
  trendRequest: 0,
  aggregateRequest: 0,
  settlementRequest: 0,
  trendItem: null,
  snapshot: null,
  navigation: null,
  started: false,
  user: null,
};

const authGate = document.querySelector("auth-gate");
const neteaseOnboarding = document.querySelector("netease-onboarding");
const appShell = document.querySelector("[data-app-shell]");
const chartMain = document.querySelector("[data-chart-main]");
const chartList = document.querySelector("chart-list");
const bubblingSection = document.querySelector("bubbling-section");
const periodSelector = document.querySelector("period-selector");
const status = document.querySelector("[data-chart-status]");
const trendDetail = document.querySelector("trend-detail");
const aggregateTrend = document.querySelector("aggregate-trend");
const settlementReveal = document.querySelector("settlement-reveal");
const settlementArchive = document.querySelector("settlement-archive");
const chartsRegion = document.querySelector('[data-page-region="charts"]');
const trendsRegion = document.querySelector('[data-page-region="trends"]');
const settlementsRegion = document.querySelector('[data-page-region="settlements"]');

const clearChart = () => {
  chartList.items = [];
  bubblingSection.items = [];
};

const showUnavailable = () => {
  clearChart();
  state.periodKey = null;
  state.snapshot = null;
  state.navigation = null;
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
  const items = snapshot.entries.map((entry) => ({
    ...mapEntry(entry),
    entityType: snapshot.chart.entityType,
    periodType: snapshot.chart.periodType,
  })).sort((left, right) => left.rank - right.rank);
  chartList.setAttribute("title", snapshot.chart.title);
  chartList.setAttribute("eyebrow", `${entityLabels[state.entityType]}主榜`);
  chartList.setAttribute("entity-label", entityLabels[state.entityType]);
  bubblingSection.setAttribute("entity-label", entityLabels[state.entityType]);
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

const maybeShowSettlement = async (snapshot, navigation) => {
  const isLatest = !navigation.next;
  const isFinal = snapshot.chart.periodType === "daily"
    ? snapshot.period.status === "settled"
    : ["settled", "partial"].includes(snapshot.period.status);
  if (!isLatest || !isFinal) return;
  const settlementVersion = snapshot.collection.collectedAt || snapshot.collection.sourceSnapshot || "unknown";
  const key = `echorank-settlement:${state.user.id}:${snapshot.chart.periodType}:${snapshot.period.key}:${snapshot.period.status}:${settlementVersion}`;
  if (localStorage.getItem(key)) return;
  const winners = [];
  for (const [entityType, label] of [["songs", "冠军单曲"], ["albums", "冠军专辑"]]) {
    const view = findView(state.manifest, entityType, snapshot.chart.periodType);
    const entry = view?.snapshots?.find((item) => item.periodKey === snapshot.period.key);
    if (!entry) continue;
    const winnerSnapshot = await loadSnapshot(state.user.id, entry);
    const champion = winnerSnapshot.entries.find((item) => item.rank.current === 1);
    if (champion) winners.push({ label, item: mapEntry(champion) });
  }
  if (!winners.length) return;
  localStorage.setItem(key, "seen");
  settlementReveal.open({
    periodLabel: snapshot.period.label,
    periodType: snapshot.chart.periodType,
    winners,
    opener: document.activeElement,
  });
};

const loadCurrentView = async () => {
  const request = ++state.chartRequest;
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
  const snapshot = await loadSnapshot(state.user.id, navigation.current);
  if (request !== state.chartRequest) return;
  state.snapshot = snapshot;
  state.navigation = navigation;
  renderSnapshot(snapshot, navigation);
  if (state.mode === "trends") loadAggregateTrend();
  else maybeShowSettlement(snapshot, navigation).catch(() => {});
};

const loadAggregateTrend = async () => {
  const request = ++state.aggregateRequest;
  aggregateTrend.stopPlayback();
  if (!state.snapshot) {
    aggregateTrend.setError("当前榜单没有可用的综合走势锚点。");
    return;
  }
  const view = findView(state.manifest, state.entityType, state.periodType);
  aggregateTrend.setLoading("正在加载综合排名走势…");
  if (!view?.snapshots?.length) {
    aggregateTrend.setError("该榜单尚未生成综合走势数据。");
    return;
  }
  try {
    const history = await loadTrendHistory(state.user.id, state.entityType, state.periodType);
    if (request !== state.aggregateRequest || state.mode !== "trends") return;
    aggregateTrend.setData(buildAggregateTrend(history, state.snapshot, state.topN), {
      entity: entityLabels[state.entityType],
      period: periodLabels[state.periodType],
    });
  } catch (error) {
    if (request === state.aggregateRequest) aggregateTrend.setError(error.message);
  }
};

const loadSettlementArchive = async () => {
  const request = ++state.settlementRequest;
  const userId = state.user.id;
  const records = [];
  for (const periodType of ["daily", "weekly", "monthly", "yearly"]) {
    const songView = findView(state.manifest, "songs", periodType);
    const albumView = findView(state.manifest, "albums", periodType);
    for (const songRef of songView?.snapshots || []) {
      const albumRef = albumView?.snapshots?.find((item) => item.periodKey === songRef.periodKey);
      if (!albumRef) continue;
      const [songs, albums] = await Promise.all([
        loadSnapshot(userId, songRef),
        loadSnapshot(userId, albumRef),
      ]);
      const final = periodType === "daily"
        ? songs.period.status === "settled"
        : ["settled", "partial"].includes(songs.period.status) && songs.entries[0]?.record.championships > 0;
      if (!final) continue;
      records.push({
        periodType,
        periodKey: songs.period.key,
        label: songs.period.label,
        winners: [
          { label: "冠军单曲", entry: songs.entries[0] },
          { label: "冠军专辑", entry: albums.entries[0] },
        ],
      });
    }
  }
  if (request !== state.settlementRequest || state.mode !== "settlements") return;
  settlementArchive.records = records.sort((left, right) => right.periodKey.localeCompare(left.periodKey));
};

const setMode = (mode) => {
  state.mode = mode;
  chartsRegion.hidden = mode !== "charts";
  trendsRegion.hidden = mode !== "trends";
  settlementsRegion.hidden = mode !== "settlements";
  if (mode === "trends") loadAggregateTrend();
  else if (mode === "settlements") loadSettlementArchive().catch((error) => {
    if (mode === state.mode) settlementArchive.records = [];
    status.textContent = error.message;
  });
  else {
    state.aggregateRequest += 1;
    state.settlementRequest += 1;
    aggregateTrend.stopPlayback();
  }
};

const loadTrend = async (periodType) => {
  const request = ++state.trendRequest;
  const view = findView(state.manifest, state.entityType, periodType);
  trendDetail.setPeriod(periodType);
  trendDetail.setLoading("正在加载排名走势…");
  if (!view?.snapshots?.length) {
    trendDetail.setError("该统计尺度尚未生成趋势数据。");
    return;
  }
  try {
    const history = await loadTrendHistory(state.user.id, state.entityType, periodType);
    if (request !== state.trendRequest) return;
    trendDetail.setData(buildTrendSeries(history, state.trendItem.id));
  } catch (error) {
    if (request === state.trendRequest) trendDetail.setError(error.message);
  }
};

document.addEventListener("navigation-change", (event) => {
  setMode(event.detail.value);
});

document.addEventListener("aggregate-top-n-change", (event) => {
  state.topN = event.detail.topN;
  loadAggregateTrend();
});

document.addEventListener("chart-entry-open", (event) => {
  state.trendItem = event.detail.item;
  trendDetail.open(state.trendItem, state.entityType, state.periodType, event.detail.opener);
  loadTrend(state.periodType);
});

document.addEventListener("trend-period-change", (event) => {
  loadTrend(event.detail.periodType);
});

document.addEventListener("tab-change", async (event) => {
  trendDetail.close();
  state.trendRequest += 1;
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
  trendDetail.close();
  state.trendRequest += 1;
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

const showEmptyChartApp = (uid) => {
  Object.assign(state, state.manifest.defaultView);
  document.querySelector('chart-tabs[name="entity"]').setAttribute("active", state.entityType);
  document.querySelector('chart-tabs[name="period"]').setAttribute("active", state.periodType);
  setMode("charts");
  showUnavailable();
  status.textContent = "网易云 UID 已绑定，等待下一次自动采集；当前暂无榜单数据。";
  neteaseOnboarding.hidden = true;
  appShell.hidden = false;
  authGate.setUser(state.user, `网易云 UID ${uid} 已绑定。`);
  chartMain.focus();
  appShell.scrollIntoView({ block: "start" });
};

const loadUserSettings = async (userId) => {
  const { data, error } = await supabase
    .from("user_settings")
    .select("netease_uid")
    .eq("user_id", userId)
    .maybeSingle();
  if (error) throw new Error(`读取用户设置失败：${error.message}`);
  if (!data) throw new Error("用户设置不存在，请重新注册或联系管理员。");
  return data;
};

const loadUserContext = async (user) => {
  const { error } = await supabase
    .from("profiles")
    .select("user_id")
    .eq("user_id", user.id)
    .maybeSingle();
  if (state.user?.id !== user.id) return;
  authGate.setUser(user, error ? "已登录；用户资料暂未同步。" : "个人资料已同步。");
};

const startChartApp = async (user) => {
  if (state.started && state.user?.id === user.id) return;
  if (state.started) stopChartApp();
  state.started = true;
  state.user = user;
  authGate.setUser(user, "正在加载个人榜单…");
  neteaseOnboarding.hidden = true;
  neteaseOnboarding.setLoading();
  try {
    const [manifest, settings] = await Promise.all([
      loadManifest(user.id),
      loadUserSettings(user.id),
    ]);
    if (state.user?.id !== user.id) return;
    state.manifest = manifest;
    loadUserContext(user).catch(() => {
      if (state.user?.id === user.id) authGate.setUser(user, "已登录；用户资料暂未同步。");
    });
    if (!manifest.views.length) {
      if (settings.netease_uid) {
        showEmptyChartApp(settings.netease_uid);
      } else {
        appShell.hidden = true;
        neteaseOnboarding.hidden = false;
        neteaseOnboarding.setEditable();
      }
      return;
    }
    Object.assign(state, manifest.defaultView);
    document.querySelector('chart-tabs[name="entity"]').setAttribute("active", state.entityType);
    document.querySelector('chart-tabs[name="period"]').setAttribute("active", state.periodType);
    await loadCurrentView();
    if (state.user?.id !== user.id) return;
    appShell.hidden = false;
  } catch (error) {
    if (state.user?.id !== user.id) return;
    state.started = false;
    clearChart();
    appShell.hidden = true;
    neteaseOnboarding.hidden = false;
    neteaseOnboarding.setFailure(error.message);
    authGate.setUser(user, "已登录，但个人数据加载失败。");
  }
};

const stopChartApp = () => {
  state.started = false;
  state.chartRequest += 1;
  state.trendRequest += 1;
  state.aggregateRequest += 1;
  state.settlementRequest += 1;
  state.manifest = null;
  state.user = null;
  state.snapshot = null;
  state.navigation = null;
  state.periodKey = null;
  state.trendItem = null;
  clearChartDataCache();
  trendDetail.close();
  settlementReveal.close();
  settlementArchive.records = [];
  aggregateTrend.stopPlayback();
  clearChart();
  appShell.hidden = true;
  neteaseOnboarding.hidden = true;
  neteaseOnboarding.reset();
};

const authErrorMessage = (error) => {
  const message = error?.message || "";
  if (/email not confirmed/i.test(message)) return "邮箱尚未验证，请先点击验证邮件中的链接。";
  if (/invalid login credentials/i.test(message)) return "邮箱或密码不正确。";
  if (/already registered|already been registered/i.test(message)) return "该邮箱已注册，请返回登录。";
  return message || "认证请求失败，请稍后重试。";
};

const showAuthError = (error) => {
  authGate.setSignedOut(authErrorMessage(error), true);
};

document.addEventListener("netease-uid-save", async (event) => {
  const userId = state.user?.id;
  if (!userId) return;
  const uid = event.detail.uid;
  neteaseOnboarding.setSaving(uid);
  const { data, error } = await supabase
    .from("user_settings")
    .update({ netease_uid: uid, updated_at: new Date().toISOString() })
    .eq("user_id", userId)
    .select("netease_uid")
    .maybeSingle();
  if (state.user?.id !== userId) return;
  if (error) {
    neteaseOnboarding.setError(`保存失败：${error.message}`);
    return;
  }
  if (!data) {
    neteaseOnboarding.setError("保存失败：用户设置不存在或无权更新。");
    return;
  }
  showEmptyChartApp(data.netease_uid);
});

document.addEventListener("auth-login", async (event) => {
  authGate.setBusy(true, "正在登录…");
  const { error } = await supabase.auth.signInWithPassword(event.detail);
  if (error) showAuthError(error);
});

document.addEventListener("auth-register", async (event) => {
  authGate.setBusy(true, "正在创建账户…");
  const { data, error } = await supabase.auth.signUp({
    email: event.detail.email,
    password: event.detail.password,
  });
  if (error) {
    showAuthError(error);
    return;
  }
  if (!data.session) {
    if (data.user) authGate.setVerificationPending(event.detail.email);
    else authGate.setSignedOut("注册请求未创建账户，请稍后重试。", true);
  }
});

document.addEventListener("auth-logout", async () => {
  authGate.setBusy(true, "正在退出…");
  const { error } = await supabase.auth.signOut();
  if (error) authGate.setMessage(error.message, true);
});

supabase.auth.onAuthStateChange((_event, session) => {
  queueMicrotask(() => {
    if (session?.user) startChartApp(session.user);
    else {
      stopChartApp();
      authGate.setSignedOut();
    }
  });
});

try {
  const { data, error } = await supabase.auth.getSession();
  if (error) throw error;
  if (data.session?.user) await startChartApp(data.session.user);
  else {
    stopChartApp();
    authGate.setSignedOut();
  }
} catch (error) {
  stopChartApp();
  showAuthError(error);
}
