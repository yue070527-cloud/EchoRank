class AuthGate extends HTMLElement {
  constructor() {
    super();
    this.mode = "loading";
    this.formMode = "login";
    this.user = null;
    this.email = "";
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

  setVerificationPending(email) {
    this.mode = "verification";
    this.email = email;
    this.busy = false;
    this.error = false;
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

  setFormMode(mode) {
    this.formMode = mode;
    this.message = mode === "register"
      ? "注册后请前往邮箱完成验证。"
      : "登录后查看你的个人榜单。";
    this.error = false;
    this.render();
  }

  render() {
    if (this.mode === "loading") {
      this.innerHTML = `
        <section class="auth-screen" aria-live="polite">
          <div class="auth-card auth-card--loading">
            <span class="auth-brand" aria-hidden="true">MB</span>
            <p>${this.escape(this.message)}</p>
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

    if (this.mode === "verification") {
      this.innerHTML = `
        <section class="auth-screen">
          <div class="auth-card auth-card--verification">
            <p class="auth-card__eyebrow">VERIFY YOUR EMAIL</p>
            <span class="auth-brand" aria-hidden="true">MB</span>
            <h1>账户已创建。<br><em>请验证邮箱。</em></h1>
            <p class="auth-card__intro">验证邮件已发送至 <strong>${this.escape(this.email)}</strong>。请点击邮件中的链接，再返回本站登录；若没有收到，请检查垃圾邮件。</p>
            <button type="button" class="auth-mode-switch" data-auth-back>返回登录</button>
          </div>
        </section>
      `;
      this.querySelector("[data-auth-back]").addEventListener("click", () => {
        this.mode = "signed-out";
        this.formMode = "login";
        this.setSignedOut("邮箱验证完成后即可登录。", false);
      });
      return;
    }

    const registering = this.formMode === "register";
    this.innerHTML = `
      <section class="auth-screen">
        <div class="auth-card">
          <p class="auth-card__eyebrow">PERSONAL MUSIC INDEX</p>
          <span class="auth-brand" aria-hidden="true">MB</span>
          <h1>${registering ? "创建你的<br><em>个人榜单。</em>" : "登录你的<br><em>个人榜单。</em>"}</h1>
          <p class="auth-card__intro">${registering
            ? "使用邮箱创建 EchoRank 账户，之后绑定网易云 UID。"
            : "使用 EchoRank 账户进入你的私人榜单。"}</p>
          <form class="auth-form">
            <label>
              <span>邮箱</span>
              <input type="email" name="email" autocomplete="email" required ${this.busy ? "disabled" : ""}>
            </label>
            <label>
              <span>密码${registering ? "（至少 6 位）" : ""}</span>
              <input type="password" name="password" autocomplete="${registering ? "new-password" : "current-password"}" minlength="6" required ${this.busy ? "disabled" : ""}>
            </label>
            <button type="submit" class="auth-primary" ${this.busy ? "disabled" : ""}>${this.busy
              ? (registering ? "正在创建账户…" : "正在登录…")
              : (registering ? "创建账户" : "登录")}</button>
          </form>
          <button type="button" class="auth-mode-switch" data-auth-mode ${this.busy ? "disabled" : ""}>${registering
            ? "已有账户？返回登录"
            : "没有账户？创建一个"}</button>
          <p class="auth-message ${this.error ? "is-error" : ""}" aria-live="polite">${this.escape(this.message)}</p>
        </div>
      </section>
    `;

    const form = this.querySelector(".auth-form");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      this.emitCredentials(registering ? "auth-register" : "auth-login", form);
    });
    this.querySelector("[data-auth-mode]").addEventListener("click", () => {
      this.setFormMode(registering ? "login" : "register");
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
