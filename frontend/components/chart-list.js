import "./chart-row.js";

class ChartList extends HTMLElement {
  set items(value) {
    this._items = value;
    this.render();
  }

  get items() {
    return this._items || [];
  }

  connectedCallback() {
    this.render();
  }

  render() {
    const title = this.getAttribute("title") || "PERSONAL CHART 50";
    const eyebrow = this.getAttribute("eyebrow") || "歌曲主榜";

    this.innerHTML = `
      <section class="chart-list" aria-label="${title}">
        <header class="chart-list__header">
          <div><span>${eyebrow}</span><h2>${title}</h2></div>
          <div class="chart-list__legend" aria-label="点数图例">
            <span class="legend-net">网易云</span>
            <span class="legend-physical">实体</span>
            <span class="legend-bilibili">B站</span>
          </div>
        </header>
        <div class="chart-column-labels" aria-hidden="true">
          <span>排名</span><span>歌曲</span><span>来源点数</span><span>综合</span><span>纪录</span>
        </div>
        <div class="chart-list__rows"></div>
        ${this.items.length ? "" : '<p class="empty-state">当前周期暂无榜单数据。</p>'}
      </section>
    `;

    const rows = this.querySelector(".chart-list__rows");
    this.items.forEach((item, index) => {
      const row = document.createElement("chart-row");
      row.item = item;
      row.style.setProperty("--row-index", index);
      rows.append(row);
    });
  }
}

customElements.define("chart-list", ChartList);
