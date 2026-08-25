"use strict";

document.querySelectorAll("[data-loading-form]").forEach((form) => {
  form.addEventListener("submit", () => {
    const button = form.querySelector("button[type='submit']");
    if (!button) return;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.textContent = button.dataset.loadingLabel || "Traitement en cours…";
  });
});

const orchestrationRoot = document.querySelector("[data-orchestration-status-url]");
if (orchestrationRoot && orchestrationRoot.dataset.terminal !== "true") {
  const stateLabels = {
    neutral: "En attente",
    in_progress: "En cours",
    completed: "Terminé",
    paused: "Action requise",
    failed: "Échec",
  };
  const stateIcons = { neutral: "•", in_progress: "•", completed: "✓", paused: "Ⅱ", failed: "×" };
  let timer = null;
  const updateStatus = (payload) => {
    orchestrationRoot.querySelector("[data-orchestration-label]").textContent = payload.status;
    orchestrationRoot.querySelector("[data-orchestration-state]").textContent = payload.status;
    orchestrationRoot.querySelector("[data-orchestration-message]").textContent = payload.message;
    payload.nodes.forEach((node) => {
      const item = orchestrationRoot.querySelector(`[data-node-key="${node.key}"]`);
      if (!item) return;
      item.className = `orchestration-step step-${node.state}`;
      item.querySelector(".step-indicator").textContent = stateIcons[node.state];
      item.querySelector("[data-node-message]").textContent = node.message;
      item.querySelector("[data-node-state]").textContent = stateLabels[node.state];
    });
    if (payload.terminal) {
      window.clearTimeout(timer);
      window.setTimeout(() => window.location.reload(), 350);
      return;
    }
    timer = window.setTimeout(poll, payload.poll_after_ms || 1500);
  };
  const poll = async () => {
    try {
      const response = await window.fetch(orchestrationRoot.dataset.orchestrationStatusUrl, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("status unavailable");
      updateStatus(await response.json());
    } catch (_error) {
      orchestrationRoot.querySelector("[data-orchestration-message]").textContent =
        "Actualisation temporairement indisponible. Les données persistées restent intactes.";
      timer = window.setTimeout(poll, 3000);
    }
  };
  timer = window.setTimeout(poll, 1000);
}
