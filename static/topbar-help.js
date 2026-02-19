(() => {
  const modal = document.getElementById("tool-help-modal");
  const titleEl = document.getElementById("tool-help-title");
  const textEl = document.getElementById("tool-help-text");
  const closeBtn = document.getElementById("tool-help-close");
  const helpButtons = document.querySelectorAll(".tool-help-btn");

  if (!modal || !titleEl || !textEl || helpButtons.length === 0) return;

  const closeModal = () => {
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
  };

  const openModal = (title, text) => {
    titleEl.textContent = title || "Описание";
    textEl.textContent = text || "";
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
  };

  helpButtons.forEach((button) => {
    button.addEventListener("click", () => {
      openModal(button.dataset.helpTitle, button.dataset.helpText);
    });
  });

  if (closeBtn) {
    closeBtn.addEventListener("click", closeModal);
  }

  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.classList.contains("hidden")) {
      closeModal();
    }
  });
})();
