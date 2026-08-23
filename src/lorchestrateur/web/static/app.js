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
