import { formatPoints } from "./point-breakdown.js";

const periodUnits = { daily: "日冠", weekly: "周冠", monthly: "月冠", yearly: "年冠" };

class SettlementReveal extends HTMLElement {
  connectedCallback() {
    if (this._ready) return;
    this._ready = true;
    this.innerHTML = `
      <dialog class="settlement-dialog" aria-labelledby="settlement-title">
        <div class="settlement-reveal">
          <p class="settlement-reveal__eyebrow">CHART SETTLEMENT</p>
          <h2 id="settlement-title">本期冠军揭晓</h2>
          <p class="settlement-reveal__period" data-settlement-period></p>
          <div class="settlement-reveal__winners" data-settlement-winners></div>
          <button type="button" class="settlement-reveal__close">查看完整榜单</button>
        </div>
      </dialog>
    `;
    this._dialog = this.querySelector("dialog");
    this.querySelector(".settlement-reveal__close").addEventListener("click", () => this.close());
    this._dialog.addEventListener("close", () => this._opener?.focus());
  }

  open({ periodLabel, periodType, winners, opener }) {
    this._opener = opener;
    this.querySelector("[data-settlement-period]").textContent = periodLabel;
    const container = this.querySelector("[data-settlement-winners]");
    container.replaceChildren();
    winners.forEach(({ label, item }) => {
      const card = document.createElement("article");
      card.className = "settlement-winner";
      const cover = document.createElement("div");
      cover.className = `settlement-winner__cover ${item.coverUrl ? "has-image" : ""}`;
      cover.style.backgroundColor = item.cover || "#777777";
      if (item.coverUrl) cover.style.backgroundImage = `url("${item.coverUrl.replaceAll('"', "%22")}")`;
      const copy = document.createElement("div");
      const eyebrow = document.createElement("small");
      eyebrow.textContent = label;
      const title = document.createElement("h3");
      title.textContent = item.title;
      const subtitle = document.createElement("p");
      subtitle.textContent = item.subtitle;
      const record = document.createElement("strong");
      record.className = "champion-badge";
      record.textContent = `${item.championships} ${periodUnits[periodType]}`;
      const points = document.createElement("p");
      points.className = "settlement-winner__points";
      points.textContent = `综合 ${formatPoints(item.total)} · 网易云 ${formatPoints(item.points.netease)} · 实体 ${formatPoints(item.points.physical)} · B站 ${formatPoints(item.points.bilibili)}`;
      copy.append(eyebrow, title, subtitle, record, points);
      card.append(cover, copy);
      container.append(card);
    });
    if (!this._dialog.open) this._dialog.showModal();
    this.querySelector(".settlement-reveal__close").focus();
  }

  close() {
    if (this._dialog.open) this._dialog.close();
  }
}

customElements.define("settlement-reveal", SettlementReveal);
