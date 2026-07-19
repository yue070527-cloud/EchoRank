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
    const {
      rank, title, subtitle, detail, coverUrl, cover, movement, points, total, peak, periods,
      championships = 0, entityType, periodType,
    } = this.item;
    const button = document.createElement("button");
    button.type = "button";
    button.className = `chart-row chart-row__button ${rank <= 3 ? "is-podium" : ""}`;
    button.setAttribute("aria-label", `查看《${title}》的排名走势，第 ${rank} 名`);

    const rankBlock = document.createElement("div");
    rankBlock.className = "chart-row__rank";
    const rankText = document.createElement("span");
    rankText.textContent = String(rank).padStart(2, "0");
    const movementElement = document.createElement("rank-movement");
    movementElement.movement = movement;
    rankBlock.append(rankText, movementElement);

    const identity = document.createElement("div");
    identity.className = "chart-row__identity";
    const coverArt = document.createElement("div");
    coverArt.className = `cover-art ${coverUrl ? "has-image" : ""}`;
    coverArt.style.setProperty("--cover", cover || "#777777");
    if (coverUrl) coverArt.style.setProperty("--cover-image", `url("${coverUrl.replaceAll('"', "%22")}")`);
    coverArt.setAttribute("aria-hidden", "true");
    const coverRank = document.createElement("span");
    coverRank.textContent = String(rank).padStart(2, "0");
    coverArt.append(coverRank);
    const copy = document.createElement("div");
    copy.className = "track-copy";
    const titleElement = document.createElement("strong");
    titleElement.textContent = title;
    const subtitleElement = document.createElement("span");
    subtitleElement.textContent = subtitle;
    const detailElement = document.createElement("small");
    detailElement.textContent = detail;
    copy.append(titleElement, subtitleElement, detailElement);
    identity.append(coverArt, copy);

    const breakdown = document.createElement("point-breakdown");
    breakdown.setAttribute("compact", "");
    breakdown.points = points;

    const totalBlock = document.createElement("div");
    totalBlock.className = "chart-row__total";
    const totalLabel = document.createElement("small");
    totalLabel.textContent = "综合点数";
    const totalValue = document.createElement("strong");
    totalValue.textContent = formatPoints(total);
    totalBlock.append(totalLabel, totalValue);

    const record = document.createElement("div");
    record.className = "chart-row__record";
    if (rank === 1 && entityType !== "artists" && championships > 0) {
      const unit = { daily: "日冠", weekly: "周冠", monthly: "月冠", yearly: "年冠" }[periodType];
      const badge = document.createElement("strong");
      badge.className = "champion-badge";
      badge.textContent = `${championships} ${unit}`;
      record.append(badge);
    } else {
      const peakRecord = document.createElement("span");
      peakRecord.innerHTML = `峰值 <strong>#${peak}</strong>`;
      record.append(peakRecord);
    }
    const periodRecord = document.createElement("span");
    periodRecord.innerHTML = `在榜 <strong>${periods}</strong> 期`;
    record.append(periodRecord);

    button.append(rankBlock, identity, breakdown, totalBlock, record);
    button.addEventListener("click", () => {
      this.dispatchEvent(new CustomEvent("chart-entry-open", {
        bubbles: true,
        detail: { item: this.item, opener: button },
      }));
    });
    this.replaceChildren(button);
  }
}

customElements.define("chart-row", ChartRow);
