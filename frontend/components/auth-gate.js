class AuthGate extends HTMLElement {
  constructor() {
    super();
    this.mode = "loading";
    this.user = null;
    this.message = "正在恢复登录状态…";
    this.error = false;
    this.busy = false;
  }

  connectedCallback() {
    this.render();
  }

  setLoading(message = "正在恢复登录状态…") {
    this.mode = "loading";
    this.message = message;
    this.error = false;
    this.render();
  }

  setSignedOut(message = "登录后查看你的个人榜单。", error = false) {
    this.mode = "signed-out";
    this.user = null;
    this.message = message;
    this.error = error;
    this.busy = false;
    this.render();
  }

  setUser(user, message = "登录状态已同步。") {
    this.mode = "signed-in";
    this.user = user;
    this.message = message;
    this.error = false;
    this.busy = false;
    this.render();
  }

  setBusy(busy, message = this.message) {
    this.busy = busy;
    this.message = message;
    this.error = false;
    this.render();
  }

  setMessage(message, error = false) {
    this.message = message;
    this.error = error;
    this.busy = false;
    this.render();
  }

  render() {
    if (this.mode === "loading") {
      this.innerHTML = `
        <section class="auth-screen" aria-live="polite">
          <div class="auth-card auth-card--loading">
            <span class="auth-brand" aria-hidden="true">MB</span>
            <p>${this.message}</p>
          </div>
        </section>
      `;
      return;
    }

    if (this.mode === "signed-in") {
      this.innerHTML = `
        <aside class="auth-session" aria-label="当前账户">
          <span>已登录 · <strong>${this.escape(this.user?.email || "Supabase 用户")}</strong></span>
          <span class="auth-session__message">${this.escape(this.message)}</span>
          <button type="button" data-auth-logout ${this.busy ? "disabled" : ""}>退出</button>
        </aside>
      `;
      this.querySelector("[data-auth-logout]").addEventListener("click", () => {
        this.dispatchEvent(new CustomEvent("auth-logout", { bubbles: true }));
      });
      return;
    }

    this.innerHTML = `
      <section class="auth-screen">
        <div class="auth-card">
          <p class="auth-card__eyebrow">PERSONAL MUSIC INDEX</p>
          <span class="auth-brand" aria-hidden="true">MB</span>
          <h1>登录你的<br><em>个人榜单。</em></h1>
          <p class="auth-card__intro">使用 Supabase 账户进入 EchoRank。</p>
          <form class="auth-form">
            <label>
              <span>邮箱</span>
              <input type="email" name="email" autocomplete="email" required ${this.busy ? "disabled" : ""}>
            </label>
            <label>
              <span>密码</span>
              <input type="password" name="password" autocomplete="current-password" minlength="6" required ${this.busy ? "disabled" : ""}>
            </label>
            <div class="auth-form__actions">
              <button type="submit" data-auth-action="login" ${this.busy ? "disabled" : ""}>登录</button>
              <button type="button" data-auth-action="register" ${this.busy ? "disabled" : ""}>注册</button>
            </div>
          </form>
          <p class="auth-message ${this.error ? "is-error" : ""}" aria-live="polite">${this.escape(this.message)}</p>
        </div>
      </section>
    `;

    const form = this.querySelector(".auth-form");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      this.emitCredentials("auth-login", form);
    });
    this.querySelector('[data-auth-action="register"]').addEventListener("click", () => {
      if (!form.reportValidity()) return;
      this.emitCredentials("auth-register", form);
    });
  }

  emitCredentials(name, form) {
    const data = new FormData(form);
    this.dispatchEvent(new CustomEvent(name, {
      bubbles: true,
      detail: {
        email: String(data.get("email") || "").trim(),
        password: String(data.get("password") || ""),
      },
    }));
  }

  escape(value) {
    const element = document.createElement("span");
    element.textContent = value;
    return element.innerHTML;
  }
}

customElements.define("auth-gate", AuthGate);
