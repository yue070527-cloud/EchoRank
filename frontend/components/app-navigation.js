class AppNavigation extends HTMLElement {
  connectedCallback() {
    this.render();
  }

  render() {
    const active = this.getAttribute("active") || "charts";
    const items = [
      { value: "charts", label: "数据及可视化" },
      { value: "trends", label: "走势" },
      { value: "settlements", label: "结算记录" },
    ];

    this.innerHTML = `
      <nav class="app-nav" aria-label="主导航">
        <a class="brand" href="#" aria-label="My Billboard 首页">
          <span class="brand-mark" aria-hidden="true">MB</span>
          <span>
            <strong>MY BILLBOARD</strong>
            <small>PERSONAL MUSIC INDEX</small>
          </span>
        </a>
        <div class="app-nav__links">
          ${items.map((item) => `
            <button type="button" data-value="${item.value}" class="app-nav__link ${active === item.value ? "is-active" : ""}" aria-current="${active === item.value ? "page" : "false"}">
              ${item.label}
            </button>
          `).join("")}
          ${["127.0.0.1", "localhost"].includes(location.hostname) ? `
            <a class="app-nav__link app-nav__admin" href="/admin/">添加数据</a>
          ` : ""}
        </div>
        <div class="update-clock">
          <span class="status-dot" aria-hidden="true"></span>
          每日 22:00 更新
        </div>
      </nav>
    `;

    this.querySelectorAll("[data-value]").forEach((button) => {
      button.addEventListener("click", () => {
        this.setAttribute("active", button.dataset.value);
        this.render();
        this.dispatchEvent(new CustomEvent("navigation-change", {
          bubbles: true,
          detail: { value: button.dataset.value },
        }));
      });
    });
  }
}

customElements.define("app-navigation", AppNavigation);
