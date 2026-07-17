class ChartTabs extends HTMLElement {
  connectedCallback() {
    this.render();
  }

  get options() {
    try {
      return JSON.parse(this.getAttribute("options") || "[]");
    } catch {
      return [];
    }
  }

  render() {
    const active = this.getAttribute("active") || this.options[0]?.value;
    const label = this.getAttribute("label") || "榜单选项";

    this.innerHTML = `
      <div class="chart-tabs" role="tablist" aria-label="${label}">
        ${this.options.map((option, index) => `
          <button type="button" role="tab" class="chart-tab ${active === option.value ? "is-active" : ""}" data-value="${option.value}" aria-selected="${active === option.value}" tabindex="${active === option.value ? "0" : "-1"}">
            ${option.label}
          </button>
        `).join("")}
      </div>
    `;

    const buttons = [...this.querySelectorAll("[role=tab]")];
    buttons.forEach((button, index) => {
      button.addEventListener("click", () => this.select(button.dataset.value));
      button.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        let nextIndex = index;
        if (event.key === "ArrowRight") nextIndex = (index + 1) % buttons.length;
        if (event.key === "ArrowLeft") nextIndex = (index - 1 + buttons.length) % buttons.length;
        if (event.key === "Home") nextIndex = 0;
        if (event.key === "End") nextIndex = buttons.length - 1;
        buttons[nextIndex].focus();
        this.select(buttons[nextIndex].dataset.value);
      });
    });
  }

  select(value) {
    this.setAttribute("active", value);
    this.render();
    this.querySelector(`[data-value="${value}"]`)?.focus();
    this.dispatchEvent(new CustomEvent("tab-change", {
      bubbles: true,
      detail: { value, name: this.getAttribute("name") },
    }));
  }
}

customElements.define("chart-tabs", ChartTabs);
