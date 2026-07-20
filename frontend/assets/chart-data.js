import { supabase } from "./supabase.js";

const ENTITY_TITLES = {
  songs: "PERSONAL CHART 50",
  albums: "PERSONAL ALBUM 50",
  artists: "PERSONAL ARTIST 50",
};
const PERIOD_STATUSES = new Set(["pending", "collecting", "settled", "partial", "missing", "failed"]);
const PAGE_SIZE = 1000;
const trendCache = new Map();
const snapshotCache = new Map();

const requireValue = (condition, message) => {
  if (!condition) throw new Error(`榜单数据无效：${message}`);
};

const throwQueryError = (error, label) => {
  if (error) throw new Error(`${label}：${error.message}`);
};

const periodLabel = (periodType, periodKey) => {
  if (periodType === "daily") {
    const [year, month, day] = periodKey.split("-").map(Number);
    return `${year} 年 ${month} 月 ${day} 日`;
  }
  if (periodType === "weekly") {
    const [year, week] = periodKey.split("-W");
    return `${year} 年第 ${Number(week)} 周`;
  }
  if (periodType === "monthly") {
    const [year, month] = periodKey.split("-");
    return `${year} 年 ${Number(month)} 月`;
  }
  return `${periodKey} 年`;
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

const periodToReference = (period) => ({
  periodKey: period.period_key,
  periodId: period.id,
});

const periodToSnapshot = (period, entries) => ({
  schemaVersion: "1.0",
  chart: {
    id: `${period.entity_type}-${period.period_type}-${period.period_key}`,
    entityType: period.entity_type,
    periodType: period.period_type,
    title: ENTITY_TITLES[period.entity_type],
  },
  period: {
    key: period.period_key,
    label: periodLabel(period.period_type, period.period_key),
    scheduledAt: period.scheduled_at,
    status: period.status,
  },
  collection: {
    collectedAt: period.collected_at,
    coverage: Number(period.coverage),
    status: period.status === "settled" ? "success" : period.status,
    sourceSnapshot: period.source_snapshot,
    version: null,
  },
  scoringVersions: {
    netease: "cloud-snapshot-v1",
    physical: "cloud-snapshot-v1",
    bilibili: "cloud-snapshot-v1",
    combined: "cloud-snapshot-v1",
  },
  entries: entries.map((entry) => ({
    entityId: entry.entity_id,
    entity: entry.entity,
    rank: {
      current: entry.rank,
      previous: entry.previous_rank,
      movement: {
        type: entry.movement_type,
        value: entry.movement_value,
      },
    },
    points: {
      netease: Number(entry.netease_points),
      physical: Number(entry.physical_points),
      bilibili: Number(entry.bilibili_points),
      other: Number(entry.other_points),
      legacyBonus: Number(entry.legacy_bonus),
      manualAdjustment: Number(entry.manual_adjustment),
      total: Number(entry.total_points),
    },
    record: {
      peak: entry.peak,
      periods: entry.periods,
      championships: entry.championships,
    },
  })),
});

const readAllPages = async (buildQuery) => {
  const rows = [];
  for (let offset = 0; ; offset += PAGE_SIZE) {
    const { data, error } = await buildQuery().range(offset, offset + PAGE_SIZE - 1);
    throwQueryError(error, "读取 Supabase 榜单失败");
    rows.push(...data);
    if (data.length < PAGE_SIZE) return rows;
  }
};

const loadManifest = async (userId) => {
  const { data, error } = await supabase
    .from("chart_periods")
    .select("id,entity_type,period_type,period_key")
    .eq("user_id", userId)
    .order("entity_type")
    .order("period_type")
    .order("period_key");
  throwQueryError(error, "读取 Supabase 榜单目录失败");
  const views = [];
  for (const period of data) {
    let view = findView({ views }, period.entity_type, period.period_type);
    if (!view) {
      view = {
        entityType: period.entity_type,
        periodType: period.period_type,
        snapshots: [],
        hasHistory: true,
      };
      views.push(view);
    }
    view.snapshots.push(periodToReference(period));
  }
  const preferred = findView({ views }, "songs", "daily") || views[0];
  const defaultView = preferred ? {
    entityType: preferred.entityType,
    periodType: preferred.periodType,
    periodKey: getLatestPeriodKey(preferred),
  } : {
    entityType: "songs",
    periodType: "daily",
    periodKey: null,
  };
  return validateManifest({ schemaVersion: "1.0", defaultView, views });
};

const loadSnapshot = (userId, reference) => {
  const key = `${userId}:${reference.periodId}`;
  if (!snapshotCache.has(key)) {
    snapshotCache.set(key, (async () => {
      const [periodResult, entryResult] = await Promise.all([
        supabase
          .from("chart_periods")
          .select("*")
          .eq("id", reference.periodId)
          .eq("user_id", userId)
          .maybeSingle(),
        supabase
          .from("chart_entries")
          .select("*")
          .eq("period_id", reference.periodId)
          .eq("user_id", userId)
          .order("rank"),
      ]);
      throwQueryError(periodResult.error, "读取 Supabase 榜单周期失败");
      throwQueryError(entryResult.error, "读取 Supabase 榜单条目失败");
      requireValue(periodResult.data, "榜单周期不存在或无权访问");
      return validateSnapshot(periodToSnapshot(periodResult.data, entryResult.data));
    })().catch((error) => {
      snapshotCache.delete(key);
      throw error;
    }));
  }
  return snapshotCache.get(key);
};

const loadTrendHistory = (userId, entityType, periodType) => {
  const key = `${userId}:${entityType}:${periodType}`;
  if (!trendCache.has(key)) {
    trendCache.set(key, (async () => {
      const { data: periods, error } = await supabase
        .from("chart_periods")
        .select("id,period_key,status,coverage,frozen")
        .eq("user_id", userId)
        .eq("entity_type", entityType)
        .eq("period_type", periodType)
        .order("period_key");
      throwQueryError(error, "读取 Supabase 走势周期失败");
      const periodIds = periods.map((period) => period.id);
      const entries = periodIds.length ? await readAllPages(() => supabase
        .from("chart_entries")
        .select("period_id,entity_id,rank,movement_type,movement_value,total_points,id")
        .eq("user_id", userId)
        .in("period_id", periodIds)
        .order("period_id")
        .order("rank")
        .order("id")) : [];
      const periodKeys = new Map(periods.map((period) => [period.id, period.period_key]));
      const series = {};
      for (const entry of entries) {
        (series[entry.entity_id] ||= []).push({
          periodKey: periodKeys.get(entry.period_id),
          rank: entry.rank,
          points: Number(entry.total_points),
          movement: {
            type: entry.movement_type,
            value: entry.movement_value,
          },
        });
      }
      return validateTrendHistory({
        schemaVersion: "1.0",
        entityType,
        periodType,
        periods: periods.map((period) => ({
          key: period.period_key,
          label: periodLabel(periodType, period.period_key),
          status: period.status,
          coverage: Number(period.coverage),
          frozen: period.frozen,
        })),
        series,
      });
    })().catch((error) => {
      trendCache.delete(key);
      throw error;
    }));
  }
  return trendCache.get(key);
};

const clearChartDataCache = () => {
  trendCache.clear();
  snapshotCache.clear();
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

export {
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
  validateTrendHistory,
};
