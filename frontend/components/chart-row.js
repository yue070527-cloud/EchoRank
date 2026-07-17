import "./point-breakdown.js";
import "./rank-movement.js";
import { formatPoints } from "./point-breakdown.js";

class ChartRow extends HTMLElement {
  set item(value) {
    this._item = value;
    this.render();
  }

  get item() {
    return this._item;
  }

  connectedCallback() {
    this.render();
  }

  render() {
    if (!this.item) return;
    const { rank, title, artist, album, coverUrl, cover, movement, points, total, peak, periods } = this.item;

    this.innerHTML = `
      <article class="chart-row ${rank <= 3 ? "is-podium" : ""}" aria-label="第 ${rank} 名，${title}，${artist}">
        <div class="chart-row__rank">
          <span>${String(rank).padStart(2, "0")}</span>
          <rank-movement></rank-movement>
        </div>
        <div class="chart-row__identity">
          <div class="cover-art ${coverUrl ? "has-image" : ""}" style="--cover:${cover};${coverUrl ? `--cover-image:url('${coverUrl}')` : ""}" aria-hidden="true"><span>${String(rank).padStart(2, "0")}</span></div>
          <div class="track-copy">
            <strong>${title}</strong>
            <span>${artist}</span>
            <small>${album}</small>
          </div>
        </div>
        <point-breakdown compact></point-breakdown>
        <div class="chart-row__total">
          <small>综合点数</small>
          <strong>${formatPoints(total)}</strong>
        </div>
        <div class="chart-row__record">
          <span>峰值 <strong>#${peak}</strong></span>
          <span>在榜 <strong>${periods}</strong> 期</span>
        </div>
      </article>
    `;

    const movementElement = this.querySelector("rank-movement");
    movementElement.movement = movement;
    this.querySelector("point-breakdown").points = points;
  }
}

customElements.define("chart-row", ChartRow);
