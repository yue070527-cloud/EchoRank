const colorFor = (id) => {
  let hash = 0;
  for (const char of id) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  return `hsl(${Math.abs(hash) % 360} 62% 42%)`;
};

class AggregateTrend extends HTMLElement {
  connectedCallback() {
    if (this._ready) return;
    this._ready = true;
    this._model = null;
    this._activePeriod = 0;
    this._displayPeriod = 0;
    this._highlightId = null;
    this._reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    this._motionListener = () => {
      if (this._reducedMotion.matches) this.stopPlayback();
      this.draw();
    };
    this._reducedMotion.addEventListener("change", this._motionListener);
    this.renderShell();
    this._canvas = this.querySelector("canvas");
    this._context = this._canvas.getContext("2d");
    this._resizeObserver = new ResizeObserver(() => this.draw());
    this._resizeObserver.observe(this.querySelector(".aggregate-trend__canvas-wrap"));
    this.bindEvents();
  }

  disconnectedCallback() {
    this.stopPlayback();
    this._resizeObserver?.disconnect();
    this._reducedMotion?.removeEventListener("change", this._motionListener);
  }

  renderShell() {
    this.innerHTML = `
      <section class="aggregate-trend" aria-labelledby="aggregate-title">
        <header class="aggregate-trend__header">
          <div>
            <p class="aggregate-trend__eyebrow">COMBINED RANK HISTORY</p>
            <h2 id="aggregate-title">综合排名走势</h2>
            <p data-aggregate-subtitle>正在准备走势数据…</p>
          </div>
          <div class="aggregate-trend__controls">
            <div class="aggregate-top" role="group" aria-label="显示名次数量">
              ${[10, 20, 50].map((value) => `<button type="button" data-top-n="${value}" aria-pressed="${value === 50}">Top ${value}</button>`).join("")}
            </div>
            <button type="button" class="aggregate-play">播放走势</button>
          </div>
        </header>
        <p class="aggregate-trend__status" data-aggregate-status aria-live="polite">正在加载…</p>
        <div class="aggregate-trend__legend">
          <span>细线：固定队列成员</span><span>粗线：当前高亮</span><span>阴影：周期缺失</span>
        </div>
        <div class="aggregate-trend__layout">
          <div class="aggregate-trend__canvas-scroll">
            <div class="aggregate-trend__canvas-wrap">
              <canvas aria-label="综合排名走势图"></canvas>
            </div>
          </div>
          <section class="aggregate-ranking" aria-labelledby="aggregate-period-title">
            <h3 id="aggregate-period-title">当前周期排名</h3>
            <p data-aggregate-period></p>
            <div class="aggregate-ranking__list" role="listbox" aria-label="固定队列排名"></div>
          </section>
        </div>
        <p class="sr-only" data-aggregate-live aria-live="polite"></p>
      </section>
    `;
  }

  bindEvents() {
    this.querySelectorAll("[data-top-n]").forEach((button) => {
      button.addEventListener("click", () => {
        const topN = Number(button.dataset.topN);
        this.dispatchEvent(new CustomEvent("aggregate-top-n-change", { bubbles: true, detail: { topN } }));
      });
    });
    this.querySelector(".aggregate-play").addEventListener("click", () => this.play());
    this._canvas.addEventListener("pointermove", (event) => this.handlePointer(event));
    this._canvas.addEventListener("pointerdown", (event) => this.handlePointer(event));
  }

  setLoading(message) {
    this.stopPlayback();
    this._model = null;
    this.querySelector("[data-aggregate-status]").textContent = message;
    this.querySelector(".aggregate-ranking__list").replaceChildren();
    this.draw();
  }

  setError(message) {
    this.setLoading(message);
  }

  setData(model, labels) {
    this.stopPlayback();
    this._model = model;
    this._activePeriod = Math.max(model.periods.findIndex((period) => period.key === model.anchorPeriodKey), 0);
    this._displayPeriod = this._activePeriod;
    if (!model.cohort.some((item) => item.id === this._highlightId)) this._highlightId = model.cohort[0]?.id || null;
    this.querySelector("#aggregate-title").textContent = `${labels.entity}${labels.period} Top ${model.topN} 综合走势`;
    this.querySelector("[data-aggregate-subtitle]").textContent = `${model.anchorLabel}固定前 ${model.cohort.length} 名，展示其全部历史排名。`;
    this.querySelector("[data-aggregate-status]").textContent = model.periods.length > 1
      ? `共 ${model.periods.length} 个历史周期，可播放动态变化。`
      : "目前只有 1 个历史周期，后续结算后将形成动态轨迹。";
    this.querySelectorAll("[data-top-n]").forEach((button) => {
      button.setAttribute("aria-pressed", String(Number(button.dataset.topN) === model.topN));
    });
    const play = this.querySelector(".aggregate-play");
    play.disabled = model.periods.length <= 1;
    play.textContent = play.disabled ? "历史周期不足" : "播放走势";
    this.renderRanking();
    this.draw();
  }

  stopPlayback() {
    if (this._animationFrame) cancelAnimationFrame(this._animationFrame);
    this._animationFrame = null;
    this._playing = false;
    const button = this.querySelector(".aggregate-play");
    if (button && !button.disabled) button.textContent = "播放走势";
  }

  play() {
    if (!this._model || this._model.periods.length <= 1) return;
    if (this._playing) {
      this.stopPlayback();
      return;
    }
    if (this._reducedMotion.matches) {
      this.setActivePeriod(this._model.periods.length - 1, true);
      return;
    }
    this._playing = true;
    this._activePeriod = 0;
    this._displayPeriod = 0;
    this.renderRanking();
    this.draw();
    this.querySelector(".aggregate-play").textContent = "暂停";
    const lastIndex = this._model.periods.length - 1;
    const periodDuration = 850;
    const startedAt = performance.now();
    const ease = (value) => value < .5
      ? 4 * value * value * value
      : 1 - Math.pow(-2 * value + 2, 3) / 2;
    let announcedIndex = 0;
    const animate = (now) => {
      if (!this._playing) return;
      const elapsed = now - startedAt;
      const segment = Math.min(Math.floor(elapsed / periodDuration), lastIndex - 1);
      const segmentProgress = Math.min((elapsed - segment * periodDuration) / periodDuration, 1);
      this._displayPeriod = segment + ease(segmentProgress);
      const activeIndex = Math.min(Math.round(this._displayPeriod), lastIndex);
      if (activeIndex !== this._activePeriod) {
        this._activePeriod = activeIndex;
        this.renderRanking();
      }
      if (activeIndex !== announcedIndex) {
        announcedIndex = activeIndex;
        const period = this._model.periods[activeIndex];
        this.querySelector("[data-aggregate-live]").textContent = `正在展示${period.label}的固定 Top ${this._model.topN} 排名。`;
      }
      this.draw();
      if (elapsed < lastIndex * periodDuration) {
        this._animationFrame = requestAnimationFrame(animate);
        return;
      }
      this._displayPeriod = lastIndex;
      this._activePeriod = lastIndex;
      this.renderRanking();
      this.draw();
      this._playing = false;
      this._animationFrame = null;
      this.querySelector(".aggregate-play").textContent = "重新播放";
    };
    this._animationFrame = requestAnimationFrame(animate);
  }

  setActivePeriod(index, announce = false) {
    if (!this._model) return;
    this._activePeriod = Math.min(Math.max(index, 0), this._model.periods.length - 1);
    this._displayPeriod = this._activePeriod;
    this.renderRanking();
    this.draw();
    if (announce) {
      const period = this._model.periods[this._activePeriod];
      this.querySelector("[data-aggregate-live]").textContent = `正在展示${period.label}的固定 Top ${this._model.topN} 排名。`;
    }
  }

  setHighlight(entityId) {
    this._highlightId = entityId;
    this.querySelectorAll(".aggregate-ranking__item").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.entityId === entityId);
      button.setAttribute("aria-selected", String(button.dataset.entityId === entityId));
    });
    this.draw();
  }

  renderRanking() {
    const list = this.querySelector(".aggregate-ranking__list");
    list.replaceChildren();
    if (!this._model?.periods.length) return;
    const period = this._model.periods[this._activePeriod];
    this.querySelector("[data-aggregate-period]").textContent = period.label;
    if (period.status === "missing" || period.status === "failed") {
      const message = document.createElement("p");
      message.className = "aggregate-ranking__missing";
      message.textContent = "该周期整体数据缺失。";
      list.append(message);
      return;
    }
    const ranked = this._model.cohort.map((item) => ({
      item,
      point: this._model.series.get(item.id)[this._activePeriod],
    })).sort((left, right) => {
      if (left.point.state === "on-chart" && right.point.state !== "on-chart") return -1;
      if (left.point.state !== "on-chart" && right.point.state === "on-chart") return 1;
      return (left.point.rank ?? 999) - (right.point.rank ?? 999) || left.item.anchorRank - right.item.anchorRank;
    });
    ranked.forEach(({ item, point }, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "aggregate-ranking__item";
      button.dataset.entityId = item.id;
      button.role = "option";
      button.setAttribute("aria-selected", String(item.id === this._highlightId));
      button.classList.toggle("is-active", item.id === this._highlightId);
      const rank = point.state === "on-chart" ? `#${point.rank}` : "榜外";
      button.innerHTML = `<span class="aggregate-ranking__rank">${rank}</span><span><strong></strong><small></small></span>`;
      button.querySelector("strong").textContent = item.title;
      button.querySelector("small").textContent = `${item.subtitle} · 锚点 #${item.anchorRank}`;
      button.addEventListener("mouseenter", () => this.setHighlight(item.id));
      button.addEventListener("focus", () => this.setHighlight(item.id));
      button.addEventListener("click", () => this.setHighlight(item.id));
      button.addEventListener("keydown", (event) => this.handleListKey(event, index));
      list.append(button);
    });
  }

  handleListKey(event, index) {
    const buttons = [...this.querySelectorAll(".aggregate-ranking__item")];
    let destination = null;
    if (event.key === "ArrowDown") destination = buttons[Math.min(index + 1, buttons.length - 1)];
    if (event.key === "ArrowUp") destination = buttons[Math.max(index - 1, 0)];
    if (event.key === "Home") destination = buttons[0];
    if (event.key === "End") destination = buttons.at(-1);
    if (!destination) return;
    event.preventDefault();
    destination.focus();
  }

  handlePointer(event) {
    if (!this._model?.periods.length) return;
    const rect = this._canvas.getBoundingClientRect();
    const left = 48;
    const right = 20;
    const top = 24;
    const bottom = 50;
    const plotWidth = Math.max(rect.width - left - right, 1);
    const plotHeight = Math.max(rect.height - top - bottom, 1);
    const periodIndex = Math.round(Math.min(Math.max(event.clientX - rect.left - left, 0), plotWidth) / plotWidth * Math.max(this._model.periods.length - 1, 0));
    const pointerRank = 1 + Math.min(Math.max(event.clientY - rect.top - top, 0), plotHeight) / plotHeight * 99;
    let nearest = null;
    this._model.cohort.forEach((item) => {
      const point = this._model.series.get(item.id)[periodIndex];
      if (point.state !== "on-chart") return;
      const distance = Math.abs(point.rank - pointerRank);
      if (!nearest || distance < nearest.distance) nearest = { id: item.id, distance };
    });
    this.setActivePeriod(periodIndex);
    if (nearest && nearest.distance <= 10) this.setHighlight(nearest.id);
  }

  draw() {
    if (!this._context || !this._canvas) return;
    const wrapper = this.querySelector(".aggregate-trend__canvas-wrap");
    const width = Math.max(wrapper.clientWidth, 700);
    const height = Math.max(wrapper.clientHeight, 470);
    const ratio = window.devicePixelRatio || 1;
    this._canvas.width = Math.round(width * ratio);
    this._canvas.height = Math.round(height * ratio);
    this._canvas.style.width = `${width}px`;
    this._canvas.style.height = `${height}px`;
    const context = this._context;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    if (!this._model?.periods.length) return;
    const left = 48;
    const right = 20;
    const top = 24;
    const bottom = 50;
    const plotBottom = height - bottom;
    const offChartY = height - 18;
    const plotWidth = width - left - right;
    const xFor = (index) => left + index / Math.max(this._model.periods.length - 1, 1) * plotWidth;
    const yFor = (point) => point.state === "on-chart" ? top + (point.rank - 1) / 99 * (plotBottom - top) : offChartY;
    context.font = '11px "Aptos", sans-serif';
    [1, 10, 20, 50, 100].forEach((rank) => {
      const y = top + (rank - 1) / 99 * (plotBottom - top);
      context.strokeStyle = rank === this._model.topN ? "#11100e" : "#ddd8ce";
      context.lineWidth = rank === this._model.topN ? 2 : 1;
      context.beginPath(); context.moveTo(left, y); context.lineTo(width - right, y); context.stroke();
      context.fillStyle = "#706c63"; context.fillText(`#${rank}`, 7, y + 4);
    });
    context.fillText("榜外", 7, offChartY + 4);
    this._model.periods.forEach((period, index) => {
      if (period.status !== "missing" && period.status !== "failed") return;
      const x = xFor(index);
      context.fillStyle = "rgba(193,62,53,.1)";
      context.fillRect(x - 6, top, 12, offChartY - top);
    });
    const displayPeriod = Math.min(Math.max(this._displayPeriod ?? this._activePeriod, 0), this._model.periods.length - 1);
    const wholePeriod = Math.floor(displayPeriod);
    const periodProgress = displayPeriod - wholePeriod;
    const drawSeries = (item, highlighted) => {
      const series = this._model.series.get(item.id);
      context.beginPath();
      let connected = false;
      series.forEach((point, index) => {
        if (index > wholePeriod || point.state === "unavailable") { connected = false; return; }
        const x = xFor(index); const y = yFor(point);
        if (!connected) context.moveTo(x, y); else context.lineTo(x, y);
        connected = true;
      });
      const current = series[wholePeriod];
      const next = series[wholePeriod + 1];
      let markerX = current ? xFor(wholePeriod) : null;
      let markerY = current && current.state !== "unavailable" ? yFor(current) : null;
      if (periodProgress > 0 && current?.state !== "unavailable" && next?.state !== "unavailable") {
        markerX = xFor(wholePeriod) + (xFor(wholePeriod + 1) - xFor(wholePeriod)) * periodProgress;
        markerY = yFor(current) + (yFor(next) - yFor(current)) * periodProgress;
        if (connected) context.lineTo(markerX, markerY);
      }
      context.strokeStyle = colorFor(item.id);
      context.globalAlpha = highlighted ? 1 : .22;
      context.lineWidth = highlighted ? 4 : 1.25;
      context.lineJoin = "round";
      context.lineCap = "round";
      context.stroke();
      context.globalAlpha = 1;
      if (markerX !== null && markerY !== null && (highlighted || this._model.cohort.length <= 10)) {
        context.beginPath(); context.arc(markerX, markerY, highlighted ? 6 : 3, 0, Math.PI * 2);
        context.fillStyle = colorFor(item.id); context.fill();
      }
    };
    this._model.cohort.filter((item) => item.id !== this._highlightId).forEach((item) => drawSeries(item, false));
    const highlighted = this._model.cohort.find((item) => item.id === this._highlightId);
    if (highlighted) drawSeries(highlighted, true);
    const guideX = xFor(displayPeriod);
    context.strokeStyle = "#11100e"; context.lineWidth = 1; context.setLineDash([5, 5]);
    context.beginPath(); context.moveTo(guideX, top); context.lineTo(guideX, offChartY); context.stroke(); context.setLineDash([]);
    const period = this._model.periods[this._activePeriod];
    context.fillStyle = "#11100e";
    context.fillText(period.key, Math.min(guideX + 6, width - 90), 16);
  }
}

customElements.define("aggregate-trend", AggregateTrend);
