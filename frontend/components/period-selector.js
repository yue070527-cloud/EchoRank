class PeriodSelector extends HTMLElement {
  set period(value) {
    this._period = value;
    this.render();
  }

  get period() {
    return this._period || {};
  }

  connectedCallback() {
    this.render();
  }

  render() {
    const title = this.period.title || this.getAttribute("title") || "尚未选择周期";
    const subtitle = this.period.subtitle || this.getAttribute("subtitle") || "暂无数据";
    const status = this.period.status || (this.hasAttribute("live") ? "collecting" : "settled");
    const statusLabels = {
      collecting: '<span class="live-badge"><span></span> LIVE · 统计中</span>',
      settled: "已结算周期",
      missing: "数据缺失",
      failed: "加载失败",
      unavailable: "暂无快照",
    };

    this.innerHTML = `
      <section class="period-selector" aria-label="榜单统计周期">
        <button type="button" class="period-arrow" data-direction="previous" aria-label="上一期" ${this.period.hasPrevious ? "" : "disabled"}>←</button>
        <div class="period-selector__copy" aria-live="polite">
          <div class="period-selector__eyebrow">${statusLabels[status] || statusLabels.settled}</div>
          <strong>${title}</strong>
          <small>${subtitle}</small>
        </div>
        <button type="button" class="period-arrow" data-direction="next" aria-label="下一期" ${this.period.hasNext ? "" : "disabled"}>→</button>
      </section>
    `;

    this.querySelectorAll("[data-direction]").forEach((button) => {
      button.addEventListener("click", () => {
        this.dispatchEvent(new CustomEvent("period-change", {
          bubbles: true,
          detail: { direction: button.dataset.direction },
        }));
      });
    });
  }
}

customElements.define("period-selector", PeriodSelector);
