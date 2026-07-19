const MANIFEST_URL = "./data/chart-manifest.json";

const fetchJson = async (url) => {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`无法加载 ${url}（HTTP ${response.status}）`);
  return response.json();
};

const requireValue = (condition, message) => {
  if (!condition) throw new Error(`榜单数据无效：${message}`);
};

const validateManifest = (manifest) => {
  requireValue(manifest?.schemaVersion === "1.0", "不支持的索引版本");
  requireValue(manifest.defaultView, "缺少默认视图");
  requireValue(Array.isArray(manifest.views), "缺少视图列表");
  return manifest;
};

const validateSnapshot = (snapshot) => {
  requireValue(snapshot?.schemaVersion === "1.0", "不支持的快照版本");
  requireValue(snapshot.chart?.entityType, "缺少实体类型");
  requireValue(snapshot.chart?.periodType, "缺少统计尺度");
  requireValue(snapshot.period?.key, "缺少周期标识");
  requireValue(Array.isArray(snapshot.entries), "缺少榜单条目");

  const ranks = new Set();
  snapshot.entries.forEach((entry) => {
    const rank = entry.rank?.current;
    requireValue(Number.isInteger(rank) && rank >= 1 && rank <= 100, "排名必须在 1—100 之间");
    requireValue(!ranks.has(rank), `排名 #${rank} 重复`);
    ranks.add(rank);
    const points = entry.points;
    requireValue(points && Number.isFinite(points.total), `#${rank} 缺少综合点数`);
    const calculatedTotal = points.netease + points.physical + points.bilibili
      + points.other + points.legacyBonus + points.manualAdjustment;
    requireValue(Math.abs(calculatedTotal - points.total) < 0.001, `#${rank} 点数合计不一致`);
  });
  return snapshot;
};

const findView = (manifest, entityType, periodType) => manifest.views.find(
  (view) => view.entityType === entityType && view.periodType === periodType,
);

const getSnapshotNavigation = (view, periodKey) => {
  const snapshots = view?.snapshots || [];
  const index = snapshots.findIndex((snapshot) => snapshot.periodKey === periodKey);
  return {
    current: index >= 0 ? snapshots[index] : null,
    previous: index > 0 ? snapshots[index - 1] : null,
    next: index >= 0 && index < snapshots.length - 1 ? snapshots[index + 1] : null,
  };
};

const getLatestPeriodKey = (view) => view?.snapshots?.at(-1)?.periodKey || null;

const mapEntry = (entry) => {
  const entity = entry.entity;
  const legacyArtists = entity.artists?.map((artist) => artist.name).join(" / ") || "";
  return {
    id: entry.entityId,
    rank: entry.rank.current,
    title: entity.title,
    subtitle: entity.subtitle ?? legacyArtists,
    detail: entity.detail ?? entity.album?.title ?? "",
    coverUrl: entity.coverUrl,
    cover: entity.coverColor,
    movement: entry.rank.movement,
    points: {
      netease: entry.points.netease,
      physical: entry.points.physical,
      bilibili: entry.points.bilibili,
    },
    total: entry.points.total,
    peak: entry.record.peak,
    periods: entry.record.periods,
    championships: entry.record.championships ?? 0,
  };
};

const trendCache = new Map();
const snapshotCache = new Map();
const PERIOD_STATUSES = new Set(["collecting", "settled", "partial", "missing", "failed"]);

const validateTrendHistory = (history) => {
  requireValue(history?.schemaVersion === "1.0", "不支持的走势版本");
  requireValue(history.entityType && history.periodType, "走势缺少榜单类型");
  requireValue(Array.isArray(history.periods), "走势缺少周期目录");
  requireValue(history.series && typeof history.series === "object", "走势缺少实体序列");
  const keys = new Set();
  history.periods.forEach((period) => {
    requireValue(period.key && !keys.has(period.key), "走势周期标识重复");
    keys.add(period.key);
    requireValue(PERIOD_STATUSES.has(period.status), `未知周期状态 ${period.status}`);
    requireValue(Number.isFinite(period.coverage) && period.coverage >= 0 && period.coverage <= 1, "覆盖率无效");
    requireValue(typeof period.frozen === "boolean", "冻结状态无效");
  });
  Object.values(history.series).flat().forEach((point) => {
    requireValue(keys.has(point.periodKey), "走势点引用了未知周期");
    requireValue(Number.isInteger(point.rank) && point.rank >= 1 && point.rank <= 100, "走势排名无效");
    requireValue(Number.isFinite(point.points), "走势点数无效");
  });
  return history;
};

const buildTrendSeries = (history, entityId) => {
  const points = new Map((history.series[entityId] || []).map((point) => [point.periodKey, point]));
  return history.periods.map((period) => {
    const point = points.get(period.key);
    const unavailable = period.status === "missing" || period.status === "failed";
    return {
      periodKey: period.key,
      label: period.label,
      status: period.status,
      coverage: period.coverage,
      frozen: period.frozen,
      state: unavailable ? "unavailable" : point ? "on-chart" : "off-chart",
      rank: point?.rank ?? null,
      points: point?.points ?? null,
      movement: point?.movement ?? null,
    };
  });
};

const buildAggregateTrend = (history, snapshot, topN) => {
  requireValue(history.entityType === snapshot.chart.entityType, "综合走势实体类型不一致");
  requireValue(history.periodType === snapshot.chart.periodType, "综合走势统计尺度不一致");
  const cohort = snapshot.entries
    .slice()
    .sort((left, right) => left.rank.current - right.rank.current)
    .slice(0, topN)
    .map((entry) => ({ ...mapEntry(entry), anchorRank: entry.rank.current }));
  return {
    periods: history.periods,
    cohort,
    series: new Map(cohort.map((item) => [item.id, buildTrendSeries(history, item.id)])),
    topN,
    anchorPeriodKey: snapshot.period.key,
    anchorLabel: snapshot.period.label,
  };
};

const loadTrendHistory = (path) => {
  if (!trendCache.has(path)) {
    trendCache.set(path, fetchJson(path).then(validateTrendHistory).catch((error) => {
      trendCache.delete(path);
      throw error;
    }));
  }
  return trendCache.get(path);
};

const loadManifest = async () => validateManifest(await fetchJson(MANIFEST_URL));
const loadSnapshot = (path) => {
  if (!snapshotCache.has(path)) {
    snapshotCache.set(path, fetchJson(path).then(validateSnapshot).catch((error) => {
      snapshotCache.delete(path);
      throw error;
    }));
  }
  return snapshotCache.get(path);
};

export {
  buildAggregateTrend,
  buildTrendSeries,
  findView,
  getLatestPeriodKey,
  getSnapshotNavigation,
  loadManifest,
  loadSnapshot,
  loadTrendHistory,
  mapEntry,
  validateTrendHistory,
};
