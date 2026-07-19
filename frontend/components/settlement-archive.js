import { mapEntry } from "../assets/chart-data.js";
import { formatPoints } from "./point-breakdown.js";

const labels = { daily: "日榜", weekly: "周榜", monthly: "月榜", yearly: "年榜" };

class SettlementArchive extends HTMLElement {
  set records(value) {
    this._records = value;
    this.render();
  }

  connectedCallback() {
    this.render();
  }

  render() {
    const records = this._records || [];
    this.innerHTML = `
      <section class="settlement-archive" aria-labelledby="settlement-archive-title">
        <header><p>CHART WINNERS</p><h2 id="settlement-archive-title">结算记录</h2></header>
        <div class="settlement-archive__list"></div>
      </section>
    `;
    const list = this.querySelector(".settlement-archive__list");
    if (!records.length) {
      list.innerHTML = '<p class="empty-state">暂无正式结算记录</p>';
      return;
    }
    records.forEach((record) => {
      const section = document.createElement("article");
      section.className = "settlement-record";
      const heading = document.createElement("h3");
      heading.textContent = `${record.label} · ${labels[record.periodType]}`;
      const winners = document.createElement("div");
      winners.className = "settlement-record__winners";
      record.winners.forEach(({ label, entry }) => {
        const item = mapEntry(entry);
        const card = document.createElement("div");
        card.className = "settlement-record__winner";
        const cover = document.createElement("div");
        cover.className = "settlement-record__cover";
        cover.style.backgroundColor = item.cover || "#777777";
        if (item.coverUrl) cover.style.backgroundImage = `url("${item.coverUrl.replaceAll('"', "%22")}")`;
        const copy = document.createElement("div");
        const type = document.createElement("small");
        type.textContent = label;
        const title = document.createElement("strong");
        title.textContent = item.title;
        const subtitle = document.createElement("span");
        subtitle.textContent = item.subtitle;
        const points = document.createElement("b");
        points.textContent = `${formatPoints(item.total)} 分`;
        copy.append(type, title, subtitle, points);
        card.append(cover, copy);
        winners.append(card);
      });
      section.append(heading, winners);
      list.append(section);
    });
  }
}

customElements.define("settlement-archive", SettlementArchive);
