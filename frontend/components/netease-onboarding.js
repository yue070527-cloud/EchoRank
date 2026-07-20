class NeteaseOnboarding extends HTMLElement {
  constructor() {
    super();
    this.mode = "loading";
    this.uid = "";
    this.message = "正在读取网易云绑定状态…";
    this.error = false;
  }

  connectedCallback() {
    this.render();
  }

  setLoading(message = "正在读取网易云绑定状态…") {
    this.mode = "loading";
    this.message = message;
    this.error = false;
    this.render();
  }

  setEditable(uid = "", message = "保存后，系统会立即准备首份基准榜单；通常需要几分钟。") {
    this.mode = "editable";
    this.uid = uid;
    this.message = message;
    this.error = false;
    this.render();
  }

  setSaving(uid) {
    this.mode = "saving";
    this.uid = uid;
    this.message = "正在保存网易云 UID…";
    this.error = false;
    this.render();
  }

  setError(message) {
    this.mode = "editable";
    this.message = message;
    this.error = true;
    this.render();
  }

  setFailure(message) {
    this.mode = "failure";
    this.message = message;
    this.error = true;
    this.render();
  }

  setConfigured(uid) {
    this.mode = "configured";
    this.uid = uid;
    this.message = "UID 已保存，系统正在准备首份基准榜单。";
    this.error = false;
    this.render();
  }

  reset() {
    this.mode = "loading";
    this.uid = "";
    this.message = "正在读取网易云绑定状态…";
    this.error = false;
    this.render();
  }

  render() {
    if (this.mode === "loading" || this.mode === "failure") {
      this.innerHTML = `
        <section class="onboarding-screen" aria-live="polite">
          <div class="auth-card ${this.mode === "loading" ? "auth-card--loading" : "onboarding-card"}">
            <span class="auth-brand" aria-hidden="true">MB</span>
            <p class="auth-message ${this.error ? "is-error" : ""}">${this.escape(this.message)}</p>
          </div>
        </section>
      `;
      return;
    }

    const configured = this.mode === "configured";
    const saving = this.mode === "saving";
    this.innerHTML = `
      <section class="onboarding-screen">
        <div class="auth-card onboarding-card">
          <p class="auth-card__eyebrow">NETEASE CONNECTION</p>
          <span class="auth-brand" aria-hidden="true">163</span>
          <h1>${configured ? "资料已绑定。" : "建立你的<br><em>个人榜单。</em>"}</h1>
          <p class="auth-card__intro">${configured
            ? `当前网易云 UID：<strong>${this.escape(this.uid)}</strong>`
            : "填写网易云数字 UID，作为之后采集个人听歌排行的账户标识。"}</p>
          ${configured ? "" : `
            <form class="auth-form onboarding-form">
              <label>
                <span>网易云数字 UID</span>
                <input type="text" name="uid" value="${this.escape(this.uid)}" inputmode="numeric" autocomplete="off" pattern="[0-9]+" required ${saving ? "disabled" : ""}>
              </label>
              <button type="submit" ${saving ? "disabled" : ""}>${saving ? "正在保存…" : "保存 UID"}</button>
            </form>
          `}
          <p class="auth-message ${this.error ? "is-error" : ""}" aria-live="polite">${this.escape(this.message)}</p>
        </div>
      </section>
    `;

    const form = this.querySelector(".onboarding-form");
    if (!form) return;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const uid = String(new FormData(form).get("uid") || "").trim();
      if (!/^[0-9]+$/.test(uid)) {
        this.setError("网易云 UID 只能包含数字。");
        return;
      }
      this.dispatchEvent(new CustomEvent("netease-uid-save", {
        bubbles: true,
        detail: { uid },
      }));
    });
  }

  escape(value) {
    const element = document.createElement("span");
    element.textContent = value;
    return element.innerHTML;
  }
}

customElements.define("netease-onboarding", NeteaseOnboarding);
