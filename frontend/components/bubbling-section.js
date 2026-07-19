import "./chart-list.js";

class BubblingSection extends HTMLElement {
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
    const expanded = this.hasAttribute("expanded");
    const entityLabel = this.getAttribute("entity-label") || "歌曲";
    const unit = entityLabel === "专辑" ? "张" : entityLabel === "艺人" ? "位" : "首";
    this.innerHTML = `
      <section class="bubbling-section">
        <button type="button" class="bubbling-toggle" aria-expanded="${expanded}">
          <span>
            <small>BUBBLING UNDER</small>
            <strong>#51—#100</strong>
          </span>
          <span class="bubbling-toggle__meta">${this.items.length} ${unit}候补${entityLabel}</span>
          <span class="bubbling-toggle__icon" aria-hidden="true">${expanded ? "−" : "+"}</span>
        </button>
        <div class="bubbling-content" ${expanded ? "" : "hidden"}></div>
      </section>
    `;

    if (expanded) {
      const list = document.createElement("chart-list");
      list.setAttribute("title", "BUBBLING UNDER 50");
      list.setAttribute("eyebrow", `${entityLabel}候补榜`);
      list.setAttribute("entity-label", entityLabel);
      list.items = this.items;
      this.querySelector(".bubbling-content").append(list);
    }

    this.querySelector("button").addEventListener("click", () => {
      this.toggleAttribute("expanded");
      this.render();
      this.dispatchEvent(new CustomEvent("toggle", {
        bubbles: true,
        detail: { expanded: this.hasAttribute("expanded") },
      }));
    });
  }
}

customElements.define("bubbling-section", BubblingSection);
