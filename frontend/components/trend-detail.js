const PERIODS = [
  ["daily", "日榜"],
  ["weekly", "周榜"],
  ["monthly", "月榜"],
  ["yearly", "年榜"],
];
const STATUS_LABELS = {
  collecting: "统计中",
  settled: "已结算",
  partial: "数据不完整",
  missing: "周期缺失",
  failed: "采集失败",
};
const MOVEMENT_LABELS = {
  up: (value) => `上升 ${value} 名`,
  down: (value) => `下降 ${value} 名`,
  same: () => "排名不变",
  new: () => "新上榜",
  re: () => "重新上榜",
};

class TrendDetail extends HTMLElement {
  connectedCallback() {
    if (this._ready) return;
    this._ready = true;
    this._series = [];
    this._periodType = "daily";
    this._activeIndex = null;
    this._animationProgress = 1;
    this._reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    this.renderShell();
    this._dialog = this.querySelector("dialog");
    this._canvas = this.querySelector("canvas");
    this._context = this._canvas.getContext("2d");
    this._tooltip = this.querySelector(".trend-tooltip");
    this._live = this.querySelector("[data-trend-live]");
    this._resizeObserver = new ResizeObserver(() => this.draw());
    this._resizeObserver.observe(this.querySelector(".trend-chart"));
    this._motionListener = () => {
      if (this._reducedMotion.matches) this.stopAnimation();
      this.draw();
    };
    this._reducedMotion.addEventListener("change", this._motionListener);
    this.bindEvents();
  }

  disconnectedCallback() {
    this.stopAnimation();
    this._resizeObserver?.disconnect();
    this._reducedMotion?.removeEventListener("change", this._motionListener);
  }

  renderShell() {
    this.innerHTML = `
      <dialog class="trend-dialog" aria-labelledby="trend-title">
        <div class="trend-detail">
          <header class="trend-detail__header">
            <div class="trend-detail__identity">
              <div class="trend-detail__cover" data-trend-cover aria-hidden="true"></div>
              <div>
                <p class="trend-detail__eyebrow">RANK HISTORY</p>
                <h2 id="trend-title">排名走势</h2>
                <p data-trend-subtitle></p>
              </div>
            </div>
            <button type="button" class="trend-close" aria-label="关闭排名走势">×</button>
          </header>
          <div class="trend-controls">
            <div class="trend-tabs" role="tablist" aria-label="走势图统计尺度"></div>
            <button type="button" class="trend-play">播放走势</button>
          </div>
          <p class="trend-status" data-trend-status aria-live="polite">请选择榜单条目。</p>
          <div class="trend-legend" aria-label="走势图图例">
            <span><i class="is-ranked"></i>入榜</span>
            <span><i class="is-off-chart"></i>榜外</span>
            <span><i class="is-missing"></i>周期缺失</span>
            <span><i class="is-live"></i>实时数据</span>
          </div>
          <div class="trend-chart">
            <canvas aria-label="排名走势图"></canvas>
            <div class="trend-tooltip" hidden></div>
            <p class="trend-chart__empty" data-trend-empty hidden>暂无可用趋势数据</p>
          </div>
          <p class="sr-only" data-trend-live aria-live="polite"></p>
          <div class="trend-table-wrap">
            <table class="trend-table">
              <caption>排名走势明细</caption>
              <thead><tr><th>周期</th><th>排名</th><th>综合点数</th><th>变化</th><th>状态</th></tr></thead>
              <tbody></tbody>
            </table>
          </div>
        </div>
      </dialog>
    `;
    const tabs = this.querySelector(".trend-tabs");
    PERIODS.forEach(([value, label], index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "trend-tab";
      button.dataset.value = value;
      button.role = "tab";
      button.textContent = label;
      button.tabIndex = index === 0 ? 0 : -1;
      tabs.append(button);
    });
  }

  bindEvents() {
    this.querySelector(".trend-close").addEventListener("click", () => this.close());
    this._dialog.addEventListener("close", () => {
      this.stopAnimation();
      this._opener?.focus();
      this.dispatchEvent(new CustomEvent("trend-closed", { bubbles: true }));
    });
    this.querySelectorAll(".trend-tab").forEach((button) => {
      button.addEventListener("click", () => this.selectPeriod(button.dataset.value));
      button.addEventListener("keydown", (event) => this.handleTabKey(event));
    });
    this.querySelector(".trend-play").addEventListener("click", () => this.play());
    this._canvas.addEventListener("pointermove", (event) => this.handlePointer(event));
    this._canvas.addEventListener("pointerdown", (event) => this.handlePointer(event));
    this._canvas.addEventListener("pointerleave", () => {
      if (!this._playing) this.setActive(null);
    });
  }

  open(item, entityType, periodType, opener) {
    this._item = item;
    this._entityType = entityType;
    this._opener = opener;
    this.querySelector("#trend-title").textContent = item.title;
    this.querySelector("[data-trend-subtitle]").textContent = `${item.subtitle} · 当前 #${item.rank} · 峰值 #${item.peak}`;
    const cover = this.querySelector("[data-trend-cover]");
    cover.style.backgroundColor = item.cover || "#777777";
    cover.style.backgroundImage = item.coverUrl ? `url("${item.coverUrl.replaceAll('"', "%22")}")` : "none";
    this.setPeriod(periodType);
    this.setLoading("正在加载排名走势…");
    if (!this._dialog.open) this._dialog.showModal();
    this.querySelector(".trend-close").focus();
  }

  close() {
    if (this._dialog.open) this._dialog.close();
  }

  setPeriod(value) {
    this._periodType = value;
    this.querySelectorAll(".trend-tab").forEach((button) => {
      const active = button.dataset.value === value;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
  }

  selectPeriod(value) {
    if (value === this._periodType) return;
    this.setPeriod(value);
    this.dispatchEvent(new CustomEvent("trend-period-change", { bubbles: true, detail: { periodType: value } }));
  }

  handleTabKey(event) {
    const tabs = [...this.querySelectorAll(".trend-tab")];
    const index = tabs.indexOf(event.currentTarget);
    let destination = null;
    if (event.key === "ArrowRight") destination = tabs[(index + 1) % tabs.length];
    if (event.key === "ArrowLeft") destination = tabs[(index - 1 + tabs.length) % tabs.length];
    if (event.key === "Home") destination = tabs[0];
    if (event.key === "End") destination = tabs.at(-1);
    if (!destination) return;
    event.preventDefault();
    destination.focus();
    destination.click();
  }

  setLoading(message) {
    this.stopAnimation();
    this._series = [];
    this.querySelector("[data-trend-status]").textContent = message;
    this.querySelector("[data-trend-empty]").hidden = true;
    this.querySelector(".trend-table tbody").replaceChildren();
    this.draw();
  }

  setError(message) {
    this.setLoading(message);
    this.querySelector("[data-trend-empty]").textContent = message;
    this.querySelector("[data-trend-empty]").hidden = false;
  }

  setData(series) {
    this.stopAnimation();
    this._series = series;
    this._activeIndex = null;
    const appearances = series.filter((point) => point.state === "on-chart").length;
    this.querySelector("[data-trend-status]").textContent = appearances
      ? `共 ${series.length} 个周期，${appearances} 期进入 Top 100。`
      : "该统计尺度暂无入榜记录。";
    this.querySelector("[data-trend-empty]").textContent = "暂无可用趋势数据";
    this.querySelector("[data-trend-empty]").hidden = appearances > 0;
    this.renderTable();
    this.animateReveal();
  }

  renderTable() {
    const body = this.querySelector(".trend-table tbody");
    body.replaceChildren();
    this._series.forEach((point, index) => {
      const row = document.createElement("tr");
      row.dataset.index = index;
      const rank = point.state === "on-chart" ? `#${point.rank}` : point.state === "off-chart" ? "榜外" : "缺失";
      const movement = point.movement ? MOVEMENT_LABELS[point.movement.type]?.(point.movement.value) || "—" : "—";
      const status = `${STATUS_LABELS[point.status] || point.status}${point.coverage < 1 ? ` · ${Math.round(point.coverage * 100)}%` : ""}`;
      [point.label, rank, point.points == null ? "—" : Math.round(point.points).toLocaleString("zh-CN"), movement, status]
        .forEach((value) => {
          const cell = document.createElement("td");
          cell.textContent = value;
          row.append(cell);
        });
      body.append(row);
    });
  }

  animateReveal() {
    this.stopAnimation();
    if (this._reducedMotion.matches || this._series.length < 2) {
      this._animationProgress = 1;
      this.draw();
      return;
    }
    const start = performance.now();
    const duration = 900;
    const frame = (time) => {
      this._animationProgress = Math.min((time - start) / duration, 1);
      this.draw();
      if (this._animationProgress < 1) this._animationFrame = requestAnimationFrame(frame);
    };
    this._animationFrame = requestAnimationFrame(frame);
  }

  stopAnimation() {
    if (this._animationFrame) cancelAnimationFrame(this._animationFrame);
    this._animationFrame = null;
    if (this._playTimer) clearTimeout(this._playTimer);
    this._playTimer = null;
    this._playing = false;
    const button = this.querySelector(".trend-play");
    if (button) button.textContent = "播放走势";
  }

  play() {
    if (this._playing) {
      this.stopAnimation();
      return;
    }
    if (!this._series.length || this._reducedMotion.matches) {
      if (this._series.length) this.setActive(this._series.length - 1);
      return;
    }
    this.stopAnimation();
    this._playing = true;
    this.querySelector(".trend-play").textContent = "停止播放";
    let index = 0;
    const next = () => {
      if (!this._playing) return;
      this.setActive(index);
      index += 1;
      if (index >= this._series.length) {
        this._playing = false;
        this.querySelector(".trend-play").textContent = "重新播放";
        return;
      }
      this._playTimer = setTimeout(next, 520);
    };
    next();
  }

  setActive(index, pointer = null) {
    this._activeIndex = index;
    this.querySelectorAll(".trend-table tbody tr").forEach((row) => {
      row.classList.toggle("is-active", Number(row.dataset.index) === index);
    });
    if (index == null) {
      this._tooltip.hidden = true;
      this.draw();
      return;
    }
    const point = this._series[index];
    const rankText = point.state === "on-chart" ? `#${point.rank}` : point.state === "off-chart" ? "榜外" : "周期缺失";
    const movement = point.movement ? MOVEMENT_LABELS[point.movement.type]?.(point.movement.value) || "" : "";
    const details = [point.label, `排名：${rankText}`];
    if (point.points != null) details.push(`总分：${Math.round(point.points).toLocaleString("zh-CN")}`);
    if (movement) details.push(movement);
    details.push(STATUS_LABELS[point.status] || point.status);
    if (point.coverage < 1) details.push(`覆盖率 ${Math.round(point.coverage * 100)}%`);
    this._tooltip.textContent = details.join(" · ");
    this._tooltip.hidden = false;
    this._live.textContent = details.join("，");
    if (pointer) {
      const wrapper = this.querySelector(".trend-chart").getBoundingClientRect();
      this._tooltip.style.left = `${Math.min(Math.max(pointer.clientX - wrapper.left, 12), wrapper.width - 180)}px`;
      this._tooltip.style.top = `${Math.max(pointer.clientY - wrapper.top - 54, 8)}px`;
    } else {
      this._tooltip.style.left = "12px";
      this._tooltip.style.top = "8px";
    }
    this.draw();
  }

  handlePointer(event) {
    if (!this._series.length) return;
    if (this._playing) this.stopAnimation();
    const rect = this._canvas.getBoundingClientRect();
    const left = 50;
    const right = 20;
    const width = Math.max(rect.width - left - right, 1);
    const position = Math.min(Math.max(event.clientX - rect.left - left, 0), width);
    const index = Math.round((position / width) * Math.max(this._series.length - 1, 0));
    this.setActive(index, event);
  }

  draw() {
    if (!this._context || !this._canvas) return;
    const wrapper = this.querySelector(".trend-chart");
    const width = Math.max(wrapper.clientWidth, 280);
    const height = Math.max(wrapper.clientHeight, 320);
    const ratio = window.devicePixelRatio || 1;
    this._canvas.width = Math.round(width * ratio);
    this._canvas.height = Math.round(height * ratio);
    this._canvas.style.width = `${width}px`;
    this._canvas.style.height = `${height}px`;
    const context = this._context;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    const left = 50;
    const right = 20;
    const top = 30;
    const bottom = 58;
    const offChartY = height - 26;
    const plotBottom = height - bottom;
    context.font = '11px "Aptos", sans-serif';
    context.fillStyle = "#706c63";
    context.strokeStyle = "#ddd8ce";
    context.lineWidth = 1;
    [1, 25, 50, 75, 100].forEach((rank) => {
      const y = top + ((rank - 1) / 99) * (plotBottom - top);
      context.beginPath();
      context.moveTo(left, y);
      context.lineTo(width - right, y);
      context.stroke();
      context.fillText(`#${rank}`, 7, y + 4);
    });
    context.fillText("榜外", 7, offChartY + 4);
    if (!this._series.length) return;
    const chartWidth = width - left - right;
    const xFor = (index) => left + (index / Math.max(this._series.length - 1, 1)) * chartWidth;
    const yFor = (point) => point.state === "on-chart"
      ? top + ((point.rank - 1) / 99) * (plotBottom - top)
      : offChartY;
    const visibleLast = Math.floor((this._series.length - 1) * this._animationProgress + 0.0001);

    this._series.forEach((point, index) => {
      if (index > visibleLast || point.state !== "unavailable") return;
      const x = xFor(index);
      context.fillStyle = "rgba(193,62,53,.09)";
      context.fillRect(x - 5, top, 10, offChartY - top);
    });

    context.strokeStyle = "#1686ad";
    context.lineWidth = 3;
    context.lineJoin = "round";
    let open = false;
    context.beginPath();
    this._series.forEach((point, index) => {
      if (index > visibleLast || point.state === "unavailable") {
        open = false;
        return;
      }
      const x = xFor(index);
      const y = yFor(point);
      if (!open) context.moveTo(x, y);
      else context.lineTo(x, y);
      open = true;
    });
    context.stroke();

    this._series.forEach((point, index) => {
      if (index > visibleLast || point.state === "unavailable") return;
      const x = xFor(index);
      const y = yFor(point);
      context.beginPath();
      context.arc(x, y, index === this._activeIndex ? 7 : 4.5, 0, Math.PI * 2);
      context.fillStyle = point.status === "collecting" ? "#f1d54b" : point.state === "off-chart" ? "#f3f0e8" : "#1686ad";
      context.fill();
      context.strokeStyle = point.state === "off-chart" ? "#706c63" : "#11100e";
      context.lineWidth = 1.5;
      context.stroke();
    });
    const labels = this._series.length <= 6 ? this._series : this._series.filter((_, index) => index === 0 || index === this._series.length - 1);
    context.fillStyle = "#706c63";
    labels.forEach((point) => {
      const index = this._series.indexOf(point);
      const text = point.periodKey;
      const x = xFor(index);
      context.save();
      context.translate(x, height - 8);
      context.rotate(-0.35);
      context.fillText(text, -context.measureText(text).width / 2, 0);
      context.restore();
    });
  }
}

customElements.define("trend-detail", TrendDetail);
