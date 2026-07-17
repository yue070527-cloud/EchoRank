class RankMovement extends HTMLElement {
  set movement(value) {
    this._movement = value;
    this.render();
  }

  connectedCallback() {
    this.render();
  }

  render() {
    const movement = this._movement || {
      type: this.getAttribute("type") || "same",
      value: Number(this.getAttribute("value")) || 0,
    };
    const states = {
      up: { icon: "↑", text: `上升 ${movement.value} 位`, display: movement.value },
      down: { icon: "↓", text: `下降 ${movement.value} 位`, display: movement.value },
      new: { icon: "", text: "新上榜", display: "NEW" },
      re: { icon: "", text: "重新入榜", display: "RE" },
      same: { icon: "—", text: "排名不变", display: "" },
    };
    const state = states[movement.type] || states.same;

    this.innerHTML = `
      <span class="rank-movement rank-movement--${movement.type}" aria-label="${state.text}">
        <span aria-hidden="true">${state.icon}${state.display}</span>
      </span>
    `;
  }
}

customElements.define("rank-movement", RankMovement);
