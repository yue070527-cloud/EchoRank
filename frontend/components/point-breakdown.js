const formatPoints = (value) => new Intl.NumberFormat("zh-CN").format(value || 0);

class PointBreakdown extends HTMLElement {
  set points(value) {
    this._points = value;
    this.render();
  }

  get points() {
    return this._points || { netease: 0, physical: 0, bilibili: 0 };
  }

  connectedCallback() {
    if (!this._points) {
      this._points = {
        netease: Number(this.getAttribute("netease")) || 0,
        physical: Number(this.getAttribute("physical")) || 0,
        bilibili: Number(this.getAttribute("bilibili")) || 0,
      };
    }
    this.render();
  }

  render() {
    const compact = this.hasAttribute("compact");
    const items = [
      { key: "netease", short: "云", label: "网易云", value: this.points.netease },
      { key: "physical", short: "实", label: "实体", value: this.points.physical },
      { key: "bilibili", short: "B", label: "B站", value: this.points.bilibili },
    ];

    this.innerHTML = `
      <div class="point-breakdown ${compact ? "is-compact" : ""}" aria-label="点数来源">
        ${items.map((item) => `
          <div class="point-source point-source--${item.key}" title="${item.label} ${formatPoints(item.value)} 点">
            <span class="point-source__label"><i aria-hidden="true"></i>${compact ? item.short : item.label}</span>
            <strong>${formatPoints(item.value)}</strong>
          </div>
        `).join("")}
      </div>
    `;
  }
}

customElements.define("point-breakdown", PointBreakdown);
export { formatPoints };
