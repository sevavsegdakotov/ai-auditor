(() => {
  const form = document.getElementById("analyze-form");
  const submitBtn = document.getElementById("submit-btn");
  const progressWrap = document.getElementById("progress-wrap");
  const progressBar = document.getElementById("progress-bar");
  const progressPercent = document.getElementById("progress-percent");
  const progressStatus = document.getElementById("progress-status");
  const progressEta = document.getElementById("progress-eta");

  const emptyState = document.getElementById("empty-state");
  const resultView = document.getElementById("result-view");
  const runIdTitle = document.getElementById("run-id-title");
  const errorsBox = document.getElementById("errors-box");

  const metricsAnalysis = document.getElementById("metrics-analysis");
  const audienceAnalysis = document.getElementById("audience-analysis");
  const pagesAnalysis = document.getElementById("pages-analysis");
  const pagesGrid = document.getElementById("pages-grid");
  const toggleLinks = document.querySelectorAll(".toggle-prompt-link");
  const topNMode = document.getElementById("top-n-mode");
  const customTopN = document.getElementById("custom-top-n");
  const topNValue = document.getElementById("top-n-value");
  const metricsPromptField = document.querySelector('textarea[name="metrics_prompt"]');
  const pagesPromptField = document.querySelector('textarea[name="pages_prompt"]');

  const stageTimeline = [
    { limit: 25, text: "Этап 1/4: загрузка файлов" },
    { limit: 55, text: "Этап 2/4: анализ выгрузок" },
    { limit: 85, text: "Этап 3/4: сбор скриншотов и текста" },
    { limit: 98, text: "Этап 4/4: финальные выводы" },
  ];

  let progressTimer = null;
  let progress = 0;
  let startTs = 0;

  const formatEta = (seconds) => {
    const safe = Math.max(0, Math.round(seconds));
    const mm = String(Math.floor(safe / 60)).padStart(2, "0");
    const ss = String(safe % 60).padStart(2, "0");
    return `~${mm}:${ss}`;
  };

  const updateProgressUi = () => {
    const stage = stageTimeline.find((item) => progress <= item.limit) || stageTimeline[stageTimeline.length - 1];
    const elapsed = (Date.now() - startTs) / 1000;
    const remaining = progress > 2 ? elapsed * ((100 - progress) / progress) : 180;

    progressBar.style.width = `${progress}%`;
    progressPercent.textContent = `${Math.round(progress)}%`;
    progressStatus.textContent = `${stage.text}…`;
    progressEta.textContent = `Примерное время до окончания: ${formatEta(remaining)}`;
  };

  const startProgress = () => {
    progress = 3;
    startTs = Date.now();
    progressWrap.classList.remove("hidden");
    updateProgressUi();

    progressTimer = window.setInterval(() => {
      if (progress < 92) {
        progress += 1.2;
      }
      updateProgressUi();
    }, 1200);
  };

  const completeProgress = () => {
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
    progress = 100;
    progressBar.style.width = "100%";
    progressPercent.textContent = "100%";
    progressStatus.textContent = "Анализ завершён";
    progressEta.textContent = "Готово";
  };

  const failProgress = () => {
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
    progressStatus.textContent = "Анализ завершился с ошибкой";
    progressEta.textContent = "Проверьте сообщение об ошибке справа";
  };

  const createPageCard = (page) => {
    const article = document.createElement("article");
    article.className = "page-card";

    const title = document.createElement("h4");
    const link = document.createElement("a");
    link.href = page.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = page.url;
    title.appendChild(link);

    const visits = document.createElement("p");
    visits.innerHTML = `<b>Визиты:</b> ${page.visits}`;

    const pageTitle = document.createElement("p");
    pageTitle.innerHTML = `<b>Title:</b> ${page.title || "-"}`;

    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Текст страницы";
    const txt = document.createElement("p");
    txt.textContent = page.text_excerpt || "Нет текста";
    details.appendChild(summary);
    details.appendChild(txt);

    const shots = document.createElement("div");
    shots.className = "shots";

    const desktop = document.createElement("a");
    desktop.href = `/${page.desktop_screenshot}`;
    desktop.target = "_blank";
    desktop.textContent = "Desktop screenshot";

    const mobile = document.createElement("a");
    mobile.href = `/${page.mobile_screenshot}`;
    mobile.target = "_blank";
    mobile.textContent = "Mobile screenshot";

    shots.appendChild(desktop);
    shots.appendChild(mobile);

    article.appendChild(title);
    article.appendChild(visits);
    article.appendChild(pageTitle);
    article.appendChild(details);
    article.appendChild(shots);

    return article;
  };

  const renderResult = (result) => {
    emptyState.classList.add("hidden");
    resultView.classList.remove("hidden");

    runIdTitle.textContent = `Результат запуска ${result.run_id || "-"}`;

    metricsAnalysis.textContent = result.metrics_analysis || "";
    audienceAnalysis.textContent = result.audience_analysis || "";
    pagesAnalysis.textContent = result.pages_analysis || "";

    pagesGrid.replaceChildren();
    (result.top_pages || []).forEach((item) => {
      pagesGrid.appendChild(createPageCard(item));
    });

    if (result.errors && result.errors.length > 0) {
      errorsBox.classList.remove("hidden");
      errorsBox.replaceChildren();
      result.errors.forEach((err) => {
        const p = document.createElement("p");
        p.textContent = err;
        errorsBox.appendChild(p);
      });
    } else {
      errorsBox.classList.add("hidden");
      errorsBox.replaceChildren();
    }
  };

  const applyTopN = (value) => {
    const n = Math.max(1, Math.min(30, Number(value) || 1));
    topNValue.value = String(n);
    if (metricsPromptField) {
      metricsPromptField.value = metricsPromptField.value.replace(/топ-\d+/gi, `топ-${n}`);
    }
    if (pagesPromptField) {
      pagesPromptField.value = pagesPromptField.value.replace(/топ-\d+/gi, `топ-${n}`);
    }
  };

  if (topNMode && customTopN && topNValue) {
    const syncMode = () => {
      if (topNMode.value === "custom") {
        customTopN.classList.remove("hidden");
        applyTopN(customTopN.value);
      } else {
        customTopN.classList.add("hidden");
        applyTopN(1);
      }
    };

    topNMode.addEventListener("change", syncMode);
    customTopN.addEventListener("input", () => applyTopN(customTopN.value));
    syncMode();
  }

  const resetPromptState = (link) => {
    const textarea = link.closest("label")?.querySelector(".prompt-field");
    if (!textarea) return;
    const collapsedRows = Number(textarea.dataset.collapsedRows || 2);
    const startHidden = textarea.dataset.startHidden === "true";
    if (startHidden) {
      textarea.classList.add("hidden");
      textarea.rows = collapsedRows;
      link.textContent = "Показать";
      return;
    }
    textarea.classList.remove("hidden");
    textarea.rows = collapsedRows;
    link.textContent = "Полностью";
  };

  toggleLinks.forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const textarea = link.closest("label")?.querySelector(".prompt-field");
      if (!textarea) return;
      const collapsedRows = Number(textarea.dataset.collapsedRows || 2);
      const expandedRows = Number(textarea.dataset.expandedRows || 12);
      const startHidden = textarea.dataset.startHidden === "true";
      const isHidden = textarea.classList.contains("hidden");
      const isCollapsed = Number(textarea.rows) === collapsedRows;

      const shouldOpen = startHidden ? isHidden : isCollapsed;
      if (shouldOpen) {
        toggleLinks.forEach((otherLink) => {
          if (otherLink === link) return;
          resetPromptState(otherLink);
        });
      }

      if (startHidden) {
        if (isHidden) {
          textarea.classList.remove("hidden");
          textarea.rows = expandedRows;
          link.textContent = "Свернуть";
        } else {
          resetPromptState(link);
        }
        return;
      }

      textarea.rows = isCollapsed ? expandedRows : collapsedRows;
      link.textContent = isCollapsed ? "Свернуть" : "Полностью";
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const formData = new FormData(form);
    submitBtn.disabled = true;
    startProgress();

    try {
      const response = await fetch("/analyze", {
        method: "POST",
        body: formData,
      });

      const payload = await response.json();
      renderResult(payload);

      if (!response.ok || (payload.errors && payload.errors.length > 0 && !payload.metrics_analysis)) {
        failProgress();
      } else {
        completeProgress();
      }
    } catch (error) {
      renderResult({
        run_id: "-",
        metrics_analysis: "",
        audience_analysis: "",
        pages_analysis: "",
        top_pages: [],
        errors: [String(error)],
      });
      failProgress();
    } finally {
      submitBtn.disabled = false;
    }
  });
})();
