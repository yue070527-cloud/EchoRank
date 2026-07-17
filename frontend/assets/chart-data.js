const MANIFEST_URL = "./data/chart-manifest.json";

const fetchJson = async (url) => {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`无法加载 ${url}（HTTP ${response.status}）`);
  }
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

const mapEntry = (entry) => ({
  id: entry.entityId,
  rank: entry.rank.current,
  title: entry.entity.title,
  artist: entry.entity.artists.map((artist) => artist.name).join(" / "),
  album: entry.entity.album.title,
  coverUrl: entry.entity.coverUrl,
  cover: entry.entity.coverColor,
  movement: entry.rank.movement,
  points: {
    netease: entry.points.netease,
    physical: entry.points.physical,
    bilibili: entry.points.bilibili,
  },
  total: entry.points.total,
  peak: entry.record.peak,
  periods: entry.record.periods,
});

const loadManifest = async () => validateManifest(await fetchJson(MANIFEST_URL));
const loadSnapshot = async (path) => validateSnapshot(await fetchJson(path));

export {
  findView,
  getLatestPeriodKey,
  getSnapshotNavigation,
  loadManifest,
  loadSnapshot,
  mapEntry,
};
