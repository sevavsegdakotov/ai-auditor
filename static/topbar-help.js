(() => {
  const initTopbarDropdowns = () => {
    const dropdowns = Array.from(document.querySelectorAll(".tool-nav .tool-dropdown"));
    if (!dropdowns.length) return;

    const closeAll = (except = null) => {
      dropdowns.forEach((dropdown) => {
        if (except && dropdown === except) return;
        dropdown.open = false;
      });
    };

    dropdowns.forEach((dropdown) => {
      dropdown.addEventListener("toggle", () => {
        if (dropdown.open) closeAll(dropdown);
      });

      dropdown.querySelectorAll(".tool-dropdown-link").forEach((link) => {
        link.addEventListener("click", () => closeAll());
      });
    });

    document.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (!target.closest(".tool-nav")) closeAll();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeAll();
    });
  };

  initTopbarDropdowns();

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
